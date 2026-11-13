"""Module: @role: テキスト入力（IME制御・クリップボード/Unicode・Excel/ブラウザ連携）およびキー押下の物理再現を担当する。"""

import ctypes
import logging
import platform
import time
from typing import Any, Dict, Optional

from pynput.keyboard import Controller as KeyboardController, Key
from pynput.mouse import Button, Controller as MouseController

from core.executor.excel_handler import get_excel_application as _get_excel_application
from core.executor.input_helper import (
    detect_unexpected_dialog as _detect_unexpected_dialog,
    send_unicode_string as _send_unicode_string,
    set_clipboard_text as _set_clipboard_text,
    smooth_move as _smooth_move,
)

logger = logging.getLogger(__name__)


def execute_type_text_action(
    args: Dict[str, Any],
    off_x: int,
    off_y: int,
    last_win_args: Optional[Dict[str, Any]],
    is_browser_target: bool,
    excel_app_cache: Any,
    workflow_id: str,
    mouse: MouseController,
    keyboard: KeyboardController,
) -> Any:
    """テキスト入力（Excel COM書込・ブラウザ高精度入力・IME監視付物理タイピング）を実行する。"""
    from core.executor.os_env_controller import (
        ensure_ime_state,
        is_link_or_url,
        normalize_text_width,
        should_input_as_fullwidth,
        should_input_as_halfwidth,
    )

    raw_text = args.get("text", "")
    text = normalize_text_width(raw_text)
    excel_cell = args.get("excel_cell")
    clear_before = args.get("clear_before_typing", False)
    use_clip = args.get("use_clipboard")
    ime_mode = args.get("ime_mode", "auto")
    if is_link_or_url(text) and ime_mode == "auto":
        ime_mode = "off"
    selector = args.get("selector")
    elem_name = args.get("element_name")
    type_x = args.get("x")
    type_y = args.get("y")

    # Why: 突発的ダイアログの割り込みによる入力消失を事前検出
    has_dlg, dlg_title = _detect_unexpected_dialog(last_win_args.get("mapped_hwnd") if last_win_args else None)
    if has_dlg:
        logger.warning(f"[{workflow_id}] Unexpected dialog detected before typing: {dlg_title}")

    if not text:
        return excel_app_cache

    skip_physical = False
    if excel_cell and platform.system() == "Windows":
        try:
            if excel_app_cache is None:
                target_hwnd = (
                    last_win_args.get("mapped_hwnd") if last_win_args else None
                ) or ctypes.windll.user32.GetForegroundWindow()
                expected_t = last_win_args.get("window_title", "") if last_win_args else ""
                excel_app_cache = _get_excel_application(target_hwnd, expected_t)
            if excel_app_cache:
                sheet = excel_app_cache.ActiveSheet
                sheet.Range(excel_cell).Select()
                sheet.Range(excel_cell).Value = text
                time.sleep(0.05)
                skip_physical = True
        except Exception as e:
            logger.warning(f"[{workflow_id}] Failed to set Excel cell value {excel_cell}: {e}")
            excel_app_cache = None

    # Why: ブラウザ専用セレクタがある場合は高精度タイピングを最優先実行
    if not skip_physical and is_browser_target and (selector or elem_name):
        try:
            from core.executor.browser_controller import BrowserController

            bc = BrowserController.get_instance()
            if bc.type_by_uia_or_selector(
                selector, text, clear_before, last_win_args, timeout=0.6, element_name=elem_name
            ):
                logger.info(f"[{workflow_id}] High-precision browser typing succeeded on '{elem_name or selector}'.")
                skip_physical = True
        except Exception as b_err:
            logger.info(f"[{workflow_id}] High-precision browser typing bypassed: {b_err}")

    if not skip_physical:
        # Why: 画面端(<=20px)の無効座標によるフォーカス脱落を防止し実座標のみクリック
        has_valid_coords = type_x is not None and type_y is not None and type_x > 20 and type_y > 20
        if has_valid_coords:
            fx = int(type_x + off_x)
            fy = int(type_y + off_y)
            _smooth_move(fx, fy)
            time.sleep(0.04)
            mouse.click(Button.left, 1)
            time.sleep(0.06)

        if clear_before:
            keyboard.press(Key.ctrl)
            keyboard.press("a")
            keyboard.release("a")
            keyboard.release(Key.ctrl)
            time.sleep(0.04)
            keyboard.press(Key.backspace)
            keyboard.release(Key.backspace)
            time.sleep(0.04)

        # Why: リンクや半角英数はIME状況を監視して半角を保証、全角文字は全角を保証
        if is_link_or_url(text) or should_input_as_halfwidth(text):
            ensure_ime_state(target_state=False, timeout=0.6)
            use_clip = True
        elif should_input_as_fullwidth(text):
            ensure_ime_state(target_state=True, timeout=0.6)
        elif ime_mode == "off":
            ensure_ime_state(target_state=False, timeout=0.6)
        elif ime_mode == "on":
            ensure_ime_state(target_state=True, timeout=0.6)

        if use_clip is None:
            use_clip = (
                is_link_or_url(text)
                or should_input_as_halfwidth(text)
                or any(ord(c) > 0x7F for c in text)
                or "\n" in text
                or len(text) > 4
            )

        clip_ok = use_clip and _set_clipboard_text(text)
        if clip_ok:
            time.sleep(0.03)
            keyboard.press(Key.ctrl)
            keyboard.press("v")
            keyboard.release("v")
            keyboard.release(Key.ctrl)
            time.sleep(0.04)
            keyboard.press(Key.right)
            keyboard.release(Key.right)
            time.sleep(0.08)
        else:
            _send_unicode_string(text)
            time.sleep(0.08)

    return excel_app_cache


def execute_press_key_action(args: Dict[str, Any], keyboard: KeyboardController) -> None:
    """単一キーまたは修飾キー組み合わせ(例: ctrl+c)を押下・解放する。"""
    key_str = args.get("key", "")
    if not key_str:
        return

    try:
        if "+" in key_str:
            keys = key_str.split("+")
            pressed = []
            for k in keys:
                k_name = k.lower().replace("key.", "")
                if k_name in ["win", "windows"]:
                    k_name = "cmd"
                key_obj = getattr(Key, k_name, k_name)
                keyboard.press(key_obj)
                pressed.append(key_obj)
            for key_obj in reversed(pressed):
                keyboard.release(key_obj)
        else:
            key_name = key_str.lower()
            if key_name in ["win", "windows"]:
                key_name = "cmd"

            if hasattr(Key, key_name):
                special_key = getattr(Key, key_name)
                keyboard.press(special_key)
                keyboard.release(special_key)
            else:
                keyboard.press(key_str)
                keyboard.release(key_str)
    except Exception as e:
        logger.warning(f"Failed to press key {key_str}: {e}")
