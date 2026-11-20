"""Module: @role: 実行中の異常（UI変更や座標ズレ）を検知し、UIA・画像照合・OCRの3段階で自律修復を統括するファサード。"""

import json
import logging
import platform
import re
from typing import Any, Callable, Dict, List, Optional

from core.healer.uia_recovery import (
    get_effective_hwnd,
    get_window_crop_box,
    try_browser_uia_recovery,
    try_windows_uia_recovery,
)
from core.healer.vision_recovery import (
    is_logo_symbol_noise as _is_logo_symbol_noise,
    match_target_text as _match_target_text,
    resolve_crop_path,
    try_edge_template_recovery,
    try_ocr_recovery,
    try_template_recovery,
)
from core.shared.macro_paths import get_macros_root
from core.shared.screen_capture import take_screenshot
from core.shared.window_utils import extract_app_name, get_browser_family, is_ignored_system_window
from engines.ocr.reader import read_text_from_image

logger = logging.getLogger(__name__)

_INVALID_RECOVERY_LABELS = {
    "left_click",
    "right_click",
    "middle_click",
    "double_click",
    "move",
    "click",
    "unknown",
    "ndlocr未設定",
    "切り抜き失敗",
}

def _extract_selector_labels(selector: Any) -> List[str]:
    """CSS/XPathセレクタ内のテキストや属性値から修復用ラベル候補を抽出する。"""
    sel = str(selector or "").strip()
    if not sel:
        return []
    extracted: List[str] = []
    for pat in (
        r"has-text\(['\"]([^'\"]+)['\"]\)",
        r"(?:aria-label|title|placeholder|alt|value)=['\"]([^'\"]+)['\"]",
        r"text\(\)\s*,\s*['\"]([^'\"]+)['\"]",
    ):
        for m in re.findall(pat, sel):
            if m and m not in extracted:
                extracted.append(m)
    return extracted


def _resolve_hwnd_by_title(last_win_args: Optional[Dict[str, Any]]) -> int:
    """ウィンドウタイトル・ブラウザ種別・アプリ名から、除外対象を除いた最良の有効HWNDを解決する。"""
    if platform.system() != "Windows" or not last_win_args:
        return 0
    win_title = str(last_win_args.get("window_title") or "").strip()
    if not win_title:
        return 0
    try:
        import ctypes
        from ctypes import wintypes

        user32 = ctypes.windll.user32
        expected_family = get_browser_family(win_title)
        app_name = extract_app_name(win_title).lower()
        exact_matches: List[int] = []
        browser_matches: List[int] = []
        app_matches: List[int] = []

        @ctypes.WINFUNCTYPE(wintypes.BOOL, wintypes.HWND, wintypes.LPARAM)
        def _enum_cb(hwnd: int, _lparam: int) -> int:
            if not user32.IsWindowVisible(hwnd) or user32.GetParent(hwnd):
                return 1
            ln = user32.GetWindowTextLengthW(hwnd)
            if ln <= 0:
                return 1
            buf = ctypes.create_unicode_buffer(ln + 1)
            user32.GetWindowTextW(hwnd, buf, ln + 1)
            title = buf.value
            if is_ignored_system_window(title):
                return 1
            if win_title.lower() in title.lower():
                exact_matches.append(int(hwnd))
                return 0
            if expected_family and get_browser_family(title) == expected_family:
                browser_matches.append(int(hwnd))
            elif len(app_name) >= 2 and (
                extract_app_name(title).lower() == app_name or app_name in title.lower()
            ):
                app_matches.append(int(hwnd))
            return 1

        user32.EnumWindows(_enum_cb, 0)
        if exact_matches:
            return exact_matches[0]
        if browser_matches:
            return browser_matches[0]
        if app_matches:
            return app_matches[0]
        return 0
    except Exception:
        return 0


def _collect_candidate_labels(target_info: Dict[str, Any], target_context: Optional[Dict[str, Any]]) -> List[str]:
    """コマンド引数とui_targetsからノイズを除外した修復用テキストラベル候補を収集する。"""
    candidates: List[str] = []
    raw_values = []
    if target_context:
        raw_values.extend([target_context.get("element_name"), target_context.get("text")])
        raw_values.extend(_extract_selector_labels(target_context.get("selector")))
    raw_values.append(target_info.get("semantic_role"))

    for val in raw_values:
        s = str(val or "").strip()
        if not s or s.startswith("{{") or s.startswith("${") or s.startswith("http"):
            continue
        if s.lower() in _INVALID_RECOVERY_LABELS:
            continue
        if s not in candidates:
            candidates.append(s)
    return candidates


def attempt_recovery(
    workflow_id: str,
    target_id: str,
    window_hwnd: int = 0,
    raw_event_id: str = "",
    target_context: Optional[Dict[str, Any]] = None,
    last_win_args: Optional[Dict[str, Any]] = None,
    fast_mode: bool = False,
    heartbeat_callback: Optional[Callable[[], None]] = None,
) -> Dict[str, Any]:
    """UIA、マルチスケール画像照合、AIビジョンの各段階で目標UI座標を自律修復する。"""
    logger.info(f"[{workflow_id}] Initiating multi-stage self-recovery for target: {target_id}")
    if heartbeat_callback:
        heartbeat_callback()

    macros_root = get_macros_root()
    target_dir = macros_root / workflow_id
    ui_targets_path = target_dir / "ui_targets.json"

    ui_targets: Dict[str, Any] = {}
    if ui_targets_path.exists():
        try:
            with open(ui_targets_path, "r", encoding="utf-8") as f:
                ui_targets = json.load(f)
        except Exception as e:
            logger.warning(f"[{workflow_id}] Could not parse ui_targets.json: {e}")

    target_info = ui_targets.get(target_id, {})
    candidate_labels = _collect_candidate_labels(target_info, target_context)
    effective_hwnd = get_effective_hwnd(window_hwnd) or get_effective_hwnd(_resolve_hwnd_by_title(last_win_args))

    # --- Stage 1: Browser/Windows UI Automation (最速・高精度) ---
    uia_timeout = 0.45 if fast_mode else 1.5
    browser_uia_res = try_browser_uia_recovery(
        workflow_id, target_context, last_win_args, candidate_labels, timeout_sec=uia_timeout
    )
    if browser_uia_res:
        return browser_uia_res
    if heartbeat_callback:
        heartbeat_callback()

    win_uia_res = try_windows_uia_recovery(
        workflow_id, effective_hwnd, candidate_labels, timeout_sec=(0.45 if fast_mode else 1.8)
    )
    if win_uia_res:
        return win_uia_res
    if heartbeat_callback:
        heartbeat_callback()

    crop_path = resolve_crop_path(target_dir, raw_event_id, target_id)
    has_crop = bool(crop_path and crop_path.exists())
    if not has_crop and not candidate_labels:
        logger.warning(f"[{workflow_id}] All recovery stages exhausted for '{target_id}'.")
        return {"success": False, "reason": "element_not_found"}

    # --- Stage 2 & 3 共通の現在画面キャプチャ ---
    full_img, monitor_info = take_screenshot()
    win_crop_box = get_window_crop_box(effective_hwnd, monitor_info, full_img.width, full_img.height)

    # --- Stage 2: カラー/グレースケールのマルチスケールテンプレート照合 ---
    if has_crop:
        template_res = try_template_recovery(
            workflow_id=workflow_id,
            target_dir=target_dir,
            raw_event_id=raw_event_id,
            target_id=target_id,
            full_img=full_img,
            monitor_info=monitor_info,
            win_crop_box=win_crop_box,
            crop_path=crop_path,
        )
        if template_res:
            return template_res
    if heartbeat_callback:
        heartbeat_callback()

    if fast_mode:
        return {"success": False, "reason": "element_not_found"}

    # --- Stage 3: クリーンアップ済みOCRビジョンスキャン (同サイズ別文字ボタン誤爆防止のためエッジより優先) ---
    ocr_res = try_ocr_recovery(
        workflow_id=workflow_id,
        target_dir=target_dir,
        target_id=target_id,
        candidate_labels=candidate_labels,
        full_img=full_img,
        monitor_info=monitor_info,
        win_crop_box=win_crop_box,
        read_text_fn=read_text_from_image,
    )
    if ocr_res:
        return ocr_res
    if heartbeat_callback:
        heartbeat_callback()

    # --- Stage 2b: ラベル情報不在またはOCR未検出時の最終エッジ構造フォールバック ---
    if has_crop:
        edge_res = try_edge_template_recovery(
            workflow_id=workflow_id,
            target_dir=target_dir,
            raw_event_id=raw_event_id,
            target_id=target_id,
            full_img=full_img,
            monitor_info=monitor_info,
            win_crop_box=win_crop_box,
            crop_path=crop_path,
        )
        if edge_res:
            return edge_res

    logger.warning(f"[{workflow_id}] All recovery stages exhausted for '{target_id}'.")
    return {"success": False, "reason": "element_not_found"}


__all__ = ["attempt_recovery", "_is_logo_symbol_noise", "_match_target_text"]
