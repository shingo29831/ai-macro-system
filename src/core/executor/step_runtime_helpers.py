"""Module: @role: StepDispatcherから分離した動的マスク生成、待機制御およびウィンドウ状態確認のランタイム補助処理を提供する。"""

from pathlib import Path
from typing import Any, Callable, Dict, List, Optional, Tuple
import ctypes
import logging
import platform
import time

import cv2
import numpy as np

from core.executor.input_helper import wait_for_screen_settle
from core.executor.window_manager import activate_and_restore_window
from core.recorder.screen_capturer import take_screenshot

logger = logging.getLogger(__name__)


def compute_dynamic_region_mask(
    workflow_id: str,
    check_stop_callback: Callable[[], None],
) -> np.ndarray:
    """初期フレーム群の標準偏差と静的エッジ保護から動的領域マスクを生成する。"""
    logger.info(f"[{workflow_id}] Buffering initial frames to detect dynamic regions (e.g., videos)...")
    initial_frames = []
    for _ in range(5):
        check_stop_callback()
        img_pil, _ = take_screenshot()
        img_cv = cv2.cvtColor(np.array(img_pil), cv2.COLOR_RGB2GRAY)
        initial_frames.append(img_cv)
        time.sleep(0.1)

    std_dev_global = np.std(initial_frames, axis=0)
    mask = (std_dev_global > 12).astype(np.uint8) * 255

    edges_list = [cv2.Canny(f, 50, 150) for f in initial_frames]
    static_edges = edges_list[0]
    for edge in edges_list[1:]:
        static_edges = cv2.bitwise_and(static_edges, edge)

    kernel_protect = np.ones((3, 3), np.uint8)
    static_edges_dilated = cv2.dilate(static_edges, kernel_protect, iterations=1)
    mask[static_edges_dilated == 255] = 0

    # Why: 大画面動画が流れるトップページでも静的枠組みを保持して照合可能に
    if np.count_nonzero(mask) / mask.size > 0.85:
        mask = (std_dev_global > 25).astype(np.uint8) * 255
        mask[static_edges_dilated == 255] = 0

    return mask


def get_window_rect_by_hwnd(hwnd: int) -> Optional[Tuple[int, int, int, int]]:
    """指定HWNDのウィンドウ矩形 (x, y, width, height) を取得する。"""
    if not hwnd or platform.system() != "Windows":
        return None
    try:
        class _Rect(ctypes.Structure):
            _fields_ = [
                ("left", ctypes.c_long),
                ("top", ctypes.c_long),
                ("right", ctypes.c_long),
                ("bottom", ctypes.c_long),
            ]

        rect = _Rect()
        if ctypes.windll.user32.GetWindowRect(hwnd, ctypes.byref(rect)):
            return rect.left, rect.top, rect.right - rect.left, rect.bottom - rect.top
    except Exception:
        pass
    return None


def ensure_browser_window_foreground(
    last_win_args: Optional[Dict[str, Any]],
    workflow_id: str,
) -> None:
    """ブラウザ操作直前にターゲットウィンドウが最前面でない場合のみ再アクティブ化する。"""
    if not last_win_args or platform.system() != "Windows":
        return
    mapped_hwnd = last_win_args.get("mapped_hwnd")
    hwnd = ctypes.windll.user32.GetForegroundWindow()
    root_hwnd = ctypes.windll.user32.GetAncestor(hwnd, 3)
    if mapped_hwnd and hwnd != mapped_hwnd and root_hwnd != mapped_hwnd:
        activate_and_restore_window(
            last_win_args.get("window_title", ""),
            last_win_args.get("x", 0),
            last_win_args.get("y", 0),
            last_win_args.get("width", 0),
            last_win_args.get("height", 0),
            workflow_id,
            last_win_args.get("launch_cmd", ""),
            mapped_hwnd,
            last_win_args.get("is_maximized"),
        )


def execute_wait_step(
    commands: List[Dict[str, Any]],
    step_index: int,
    args: Dict[str, Any],
    target_dir: Path,
    workflow_id: str,
    check_stop_callback: Callable[[], None],
) -> None:
    """画面照合スキップおよび描画収束検知を考慮した待機ステップを実行する。"""
    prev_method = commands[step_index - 1].get("method") if step_index > 0 else ""
    next_has_pre_img = False
    for j in range(step_index + 1, len(commands)):
        if commands[j].get("method") not in ["wait", "activate_window", "loop_start", "loop_end"]:
            next_eid = commands[j].get("args", {}).get("raw_event_id")
            if next_eid and (target_dir / "images" / f"{next_eid}_pre.png").exists():
                next_has_pre_img = True
            break

    # Why: スクロール直後は描画・アニメーション完了が必須のため固定待機を維持
    if next_has_pre_img and prev_method not in ["move", "click", "scroll"]:
        logger.info(f"[{workflow_id}] Skipping fixed wait in favor of screen matching for the next action.")
        return

    duration = float(args.get("duration", 0.0))
    # Why: 0.6秒以上の待機時は画面描画収束を検知して無駄な待機時間を適応短縮
    if duration >= 0.6 and prev_method in ["click", "type_text", "press_key", "browser_action"]:
        settled = wait_for_screen_settle(timeout=min(duration, 3.0), settle_threshold=0.003)
        if settled:
            logger.info(f"[{workflow_id}] Smart adaptive wait: Screen settled early, proceeding.")
            return

    sleep_intervals = int(duration * 10)
    for _ in range(sleep_intervals):
        check_stop_callback()
        time.sleep(0.1)
    remainder = duration - (sleep_intervals * 0.1)
    if remainder > 0:
        check_stop_callback()
        time.sleep(remainder)
