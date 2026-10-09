"""Module: @role: マクロ実行時の各種個別アクション（クリック、移動、スクロール、テキスト入力、キー押下）の物理再現およびUI自己修復を担当する。"""

import ctypes
import logging
import platform
import time
from pathlib import Path
from typing import Any, Callable, Dict, Optional, Tuple

import numpy as np
from pynput.keyboard import Controller as KeyboardController, Key
from pynput.mouse import Controller as MouseController, Button

from core.executor.input_helper import (
    get_window_offset as _get_window_offset,
    smooth_move as _smooth_move,
    send_unicode_string as _send_unicode_string,
    set_clipboard_text as _set_clipboard_text,
    wait_for_screen_settle as _wait_for_screen_settle,
    detect_unexpected_dialog as _detect_unexpected_dialog,
)
from core.executor.excel_handler import get_excel_application as _get_excel_application
from core.executor.target_healer import verify_and_heal_target
from core.executor.window_manager import activate_and_restore_window

logger = logging.getLogger(__name__)

MOUSEEVENTF_WHEEL = 0x0800
MOUSEEVENTF_HWHEEL = 0x1000
WHEEL_DELTA = 120


def ensure_window_foreground(last_win_args: Optional[Dict[str, Any]], workflow_id: str) -> None:
    if not last_win_args or platform.system() != "Windows":
        return
    mapped_hwnd = last_win_args.get("mapped_hwnd")
    hwnd = ctypes.windll.user32.GetForegroundWindow()
    root_hwnd = ctypes.windll.user32.GetAncestor(hwnd, 3)
    needs_activation = False

    # Why: 子ウィンドウやタブ内部フォーカス時もルートハンドル一致で余計な再前面化を防止
    if mapped_hwnd:
        if hwnd != mapped_hwnd and root_hwnd != mapped_hwnd:
            needs_activation = True
    else:
        window_title = last_win_args.get("window_title", "")
        app_name = window_title.split("—")[-1].split("-")[-1].strip()
        if app_name:
            length = ctypes.windll.user32.GetWindowTextLengthW(hwnd)
            buff = ctypes.create_unicode_buffer(length + 1)
            ctypes.windll.user32.GetWindowTextW(hwnd, buff, length + 1)
            current_fg_title = buff.value
            if app_name.lower() not in current_fg_title.lower():
                needs_activation = True

    if needs_activation:
        logger.info(f"[{workflow_id}] Target window is not in foreground. Activating...")
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


def verify_and_heal_target(
    workflow_id: str,
    target_id: str,
    raw_event_id: str,
    args: Dict[str, Any],
    target_dir: Path,
    last_win_args: Optional[Dict[str, Any]],
    status_callback: Optional[Callable[[str, bool], None]],
    check_stop_callback: Callable[[], None],
) -> Tuple[bool, Optional[Dict[str, Any]]]:
    needs_recovery = False
    crop_image_path = target_dir / "images" / f"{raw_event_id}_crop.png"
    recovery_info = None

    try:
        from core.recorder.screen_capturer import take_screenshot
        from core.healer.recovery_manager import attempt_recovery
        import cv2

        current_img_pil, _ = take_screenshot()
        check_stop_callback()

        if crop_image_path.exists():
            current_img_cv = cv2.cvtColor(np.array(current_img_pil), cv2.COLOR_RGB2BGR)
            template_cv = cv2.imread(str(crop_image_path), cv2.IMREAD_COLOR)

            if template_cv is not None:
                res = cv2.matchTemplate(current_img_cv, template_cv, cv2.TM_CCOEFF_NORMED)
                min_val, max_val, min_loc, max_loc = cv2.minMaxLoc(res)

                if max_val < 0.8:
                    logger.warning(f"[{workflow_id}] Template match failed (Confidence: {max_val:.2f}). Initiating Healer...")
                    needs_recovery = True
                else:
                    match_center_x = max_loc[0] + template_cv.shape[1] // 2
                    match_center_y = max_loc[1] + template_cv.shape[0] // 2
                    expected_x = args.get("x", 0)
                    expected_y = args.get("y", 0)
                    dist = ((match_center_x - expected_x) ** 2 + (match_center_y - expected_y) ** 2) ** 0.5

                    if dist > 20:
                        logger.warning(f"[{workflow_id}] Target UI drifted by {dist:.1f} pixels. Initiating Healer...")
                        needs_recovery = True
            else:
                needs_recovery = True
        else:
            logger.warning(f"[{workflow_id}] No crop image available. Initiating Healer...")
            needs_recovery = True

        if needs_recovery:
            check_stop_callback()
            if status_callback:
                status_callback("自己修復中...", True)

            target_hwnd = (last_win_args.get("mapped_hwnd") if last_win_args else 0) or 0
            recovery_result = attempt_recovery(workflow_id, target_id, window_hwnd=target_hwnd, raw_event_id=raw_event_id)
            check_stop_callback()

            recovery_info = recovery_result
            if recovery_result.get("success"):
                new_coords = recovery_result.get("new_coordinates")
                if new_coords:
                    args["x"] = new_coords["x"]
                    args["y"] = new_coords["y"]
                    logger.info(f"[{workflow_id}] Healer successfully updated coordinates to ({args['x']}, {args['y']}).")
                    if status_callback:
                        status_callback("実行中...", False)
                    return True, recovery_info
            else:
                logger.error(f"[{workflow_id}] Healer failed to recover target '{target_id}'.")
                if status_callback:
                    status_callback("実行中...", False)
                return False, recovery_info

        return True, None

    except Exception as e:
        logger.error(f"[{workflow_id}] Error during image validation/recovery: {e}")
        if status_callback:
            status_callback("実行中...", False)
        if "安全のため" in str(e):
            raise e
        return True, None


def execute_physical_action(
    method: str,
    args: Dict[str, Any],
    last_win_args: Optional[Dict[str, Any]],
    current_win_rect: Tuple[int, int, int, int],
    is_browser_target: bool,
    excel_app_cache: Any,
    workflow_id: str,
    mouse: MouseController,
    keyboard: KeyboardController,
    check_stop_callback: Callable[[], None],
) -> Tuple[Any, bool]:
    ensure_window_foreground(last_win_args, workflow_id)

    current_win_x, current_win_y, current_win_w, current_win_h = current_win_rect
    target_hwnd_for_offset = (last_win_args.get("mapped_hwnd") if last_win_args else None) or (
        ctypes.windll.user32.GetForegroundWindow() if platform.system() == "Windows" else None
    )
    off_x, off_y = _get_window_offset(target_hwnd_for_offset, current_win_x, current_win_y)

    if method == "click":
        x = args.get("x", 0) + off_x
        y = args.get("y", 0) + off_y
        button_str = args.get("button", "left")
        clicks = args.get("clicks", 1)
        excel_dest_cell = args.get("excel_dest_cell")
        selector = args.get("selector")
        elem_name = args.get("element_name")
        target_url = args.get("url")

        skip_physical = False
        if excel_dest_cell and platform.system() == "Windows":
            try:
                if excel_app_cache is None:
                    target_hwnd = (last_win_args.get("mapped_hwnd") if last_win_args else None) or ctypes.windll.user32.GetForegroundWindow()
                    excel_app_cache = _get_excel_application(target_hwnd)
                if excel_app_cache:
                    sheet = excel_app_cache.ActiveSheet
                    sheet.Range(excel_dest_cell).Select()
                    time.sleep(0.05)
                    skip_physical = True
            except Exception as e:
                logger.warning(f"[{workflow_id}] Failed to select Excel dest cell {excel_dest_cell}: {e}")
                excel_app_cache = None

        # Why: "div"等の汎用タグ単体セレクタは誤爆防止のため物理座標クリックへ委譲
        is_generic_tag = selector and selector.strip().lower() in ["div", "span", "p", "a", "li", "ul", "body"]
        # Why: ブラウザ専用セレクタ/UIA要素が存在する場合は高精度クリックを最優先実行
        if not skip_physical and (selector or elem_name or target_url) and is_browser_target and not is_generic_tag:
            try:
                from core.executor.browser_controller import BrowserController
                bc = BrowserController.get_instance()
                if bc._click_by_uia_or_selector(selector, last_win_args, timeout=0.6, element_name=elem_name, url=target_url, x=x, y=y):
                    logger.info(f"[{workflow_id}] High-precision browser click succeeded on '{elem_name or selector}'.")
                    skip_physical = True
            except Exception as b_err:
                logger.info(f"[{workflow_id}] High-precision browser click bypassed: {b_err}")

        if not skip_physical:
            btn = Button.right if button_str == "right" else Button.middle if button_str == "middle" else Button.left
            _smooth_move(int(x), int(y))
            time.sleep(0.06)
            mouse.click(btn, clicks)
            time.sleep(0.08)

    elif method == "move":
        x = args.get("x", 0) + off_x
        y = args.get("y", 0) + off_y
        excel_dest_cell = args.get("excel_dest_cell")
        selector = args.get("selector")
        elem_name = args.get("element_name")

        skip_physical = False
        if excel_dest_cell and platform.system() == "Windows":
            try:
                if excel_app_cache is None:
                    target_hwnd = (last_win_args.get("mapped_hwnd") if last_win_args else None) or ctypes.windll.user32.GetForegroundWindow()
                    excel_app_cache = _get_excel_application(target_hwnd)
                if excel_app_cache:
                    sheet = excel_app_cache.ActiveSheet
                    sheet.Range(excel_dest_cell).Select()
                    time.sleep(0.05)
                    skip_physical = True
            except Exception as e:
                logger.warning(f"[{workflow_id}] Failed to select Excel dest cell {excel_dest_cell}: {e}")
                excel_app_cache = None

        if not skip_physical:
            hovered = False
            # Why: ブラウザ操作時はセレクタ/UIA要素への高精度ホバー吸着を実行しドロップダウンメニュー展開を完全保証
            if is_browser_target and (selector or elem_name):
                try:
                    from core.executor.browser_controller import BrowserController
                    bc = BrowserController.get_instance()
                    if bc._hover_by_uia_or_selector(selector, last_win_args, timeout=0.8, element_name=elem_name, target_x=x, target_y=y):
                        logger.info(f"[{workflow_id}] High-precision browser hover succeeded on '{elem_name or selector}'.")
                        hovered = True
                except Exception as b_err:
                    logger.debug(f"[{workflow_id}] Browser hover bypassed: {b_err}")

            if not hovered:
                _smooth_move(int(x), int(y), duration=0.15)
                # Why: ブラウザのレンダラプロセスにWM_MOUSEMOVEを確実に受領させホバーメニューを展開
                if platform.system() == "Windows":
                    mouse.position = (int(x), int(y))
                    ctypes.windll.user32.mouse_event(0x0001, 1, 0, 0, 0)
                    ctypes.windll.user32.mouse_event(0x0001, -1, 0, 0, 0)
                time.sleep(0.55)

    elif method == "scroll":
        try:
            dx = float(args.get("dx") or 0.0)
            dy = float(args.get("dy") or 0.0)
        except (ValueError, TypeError):
            dx, dy = 0.0, 0.0
        x = args.get("x")
        y = args.get("y")

        if dx == 0.0 and dy == 0.0:
            return excel_app_cache, True

        # Why: 座標未指定・枠外時にウィンドウクライアント領域中央へ安全移動
        target_sx = int(x + off_x) if (x is not None and x > 20) else (current_win_x + current_win_w // 2 if current_win_w > 100 else 960)
        target_sy = int(y + off_y) if (y is not None and y > 20) else (current_win_y + current_win_h // 2 if current_win_h > 100 else 540)

        # Why: スクロール受領対象へ確実にカーソルを移動させホバー状態を確立
        _smooth_move(target_sx, target_sy, duration=0.08)
        time.sleep(0.05)

        if platform.system() == "Windows":
            from core.executor.os_env_controller import get_wheel_scroll_settings
            settings = get_wheel_scroll_settings()
            is_page = settings.get("is_page_scroll", False)

            if is_page and dy != 0.0:
                page_key = Key.page_down if dy < 0 else Key.page_up
                keyboard.press(page_key)
                keyboard.release(page_key)
                time.sleep(0.15)
            else:
                # Why: 環境設定に応じたスクロール係数を反映し物理ホイールを正確に送出
                lines_per_notch = settings.get("wheel_scroll_lines", 3)
                scroll_multiplier = max(1, min(5, lines_per_notch // 3)) if lines_per_notch > 0 else 1
                total_notches = max(1, int(round(abs(dy) * scroll_multiplier)))
                dir_y = 1 if dy > 0 else -1
                wheel_val = int(dir_y * WHEEL_DELTA)
                for _ in range(total_notches):
                    check_stop_callback()
                    ctypes.windll.user32.mouse_event(MOUSEEVENTF_WHEEL, 0, 0, ctypes.c_long(wheel_val).value, 0)
                    time.sleep(0.04)

                if dx != 0.0:
                    total_notches_x = max(1, int(round(abs(dx))))
                    dir_x = 1 if dx > 0 else -1
                    hwheel_val = int(dir_x * WHEEL_DELTA)
                    for _ in range(total_notches_x):
                        check_stop_callback()
                        ctypes.windll.user32.mouse_event(MOUSEEVENTF_HWHEEL, 0, 0, ctypes.c_long(hwheel_val).value, 0)
                        time.sleep(0.04)
            # Why: スクロール直後のレイアウト再計算と描画収束を待機
            time.sleep(0.2)
            _wait_for_screen_settle(timeout=0.6, settle_threshold=0.003)
        else:
            steps = max(1, int(round(abs(dy))))
            dir_y = 1 if dy > 0 else -1
            for _ in range(steps):
                mouse.scroll(0, dir_y)
                time.sleep(0.04)
            time.sleep(0.2)

    elif method == "type_text":
        raw_text = args.get("text", "")
        from core.executor.os_env_controller import is_link_or_url, normalize_text_width
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

        if text:
            skip_physical = False
            if excel_cell and platform.system() == "Windows":
                try:
                    if excel_app_cache is None:
                        target_hwnd = (last_win_args.get("mapped_hwnd") if last_win_args else None) or ctypes.windll.user32.GetForegroundWindow()
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
                    if bc._type_by_uia_or_selector(selector, text, clear_before, last_win_args, timeout=0.6, element_name=elem_name):
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
                    keyboard.press('a')
                    keyboard.release('a')
                    keyboard.release(Key.ctrl)
                    time.sleep(0.04)
                    keyboard.press(Key.backspace)
                    keyboard.release(Key.backspace)
                    time.sleep(0.04)

                from core.executor.os_env_controller import ensure_ime_state, should_input_as_halfwidth, should_input_as_fullwidth
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
                    use_clip = is_link_or_url(text) or should_input_as_halfwidth(text) or any(ord(c) > 0x7F for c in text) or "\n" in text or len(text) > 4

                clip_ok = use_clip and _set_clipboard_text(text)
                if clip_ok:
                    time.sleep(0.03)
                    keyboard.press(Key.ctrl)
                    keyboard.press('v')
                    keyboard.release('v')
                    keyboard.release(Key.ctrl)
                    time.sleep(0.04)
                    keyboard.press(Key.right)
                    keyboard.release(Key.right)
                    time.sleep(0.08)
                else:
                    _send_unicode_string(text)
                    time.sleep(0.08)

    elif method == "press_key":
        key_str = args.get("key", "")
        if key_str:
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

    return excel_app_cache, False
