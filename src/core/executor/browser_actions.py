"""Module: @role: ブラウザ上の個別操作（クリック、ホバー、文字入力、URL遷移、セレクトボックス、チェックボックス）の実行制御を担当する。"""

import ctypes
import logging
import platform
import time
from typing import Any, Dict, Optional

from pynput.keyboard import Controller as KeyboardController, Key
from pynput.mouse import Button, Controller as MouseController

from core.executor.browser_uia_finder import (
    SIMPLE_GENERIC_TAGS,
    find_uia_element,
    get_combobox_current_value,
    scroll_into_view,
)
from core.executor.os_env_controller import set_system_cursor

logger = logging.getLogger(__name__)


def is_address_bar_target(selector: Optional[str], element_name: Optional[str]) -> bool:
    return (
        any(k in str(selector).lower() for k in ["urlbar", "address", "omnibox"])
        or any(k in str(element_name).lower() for k in ["url", "アドレス", "検索、または url", "検索または url"])
    )


class BrowserActionPerformer:
    """ブラウザ上の各種ユーザーインタラクション（物理入力・UIA操作・フォーム操作）を実行するクラス"""

    def __init__(self, mouse: MouseController, keyboard: KeyboardController) -> None:
        self._mouse = mouse
        self._keyboard = keyboard

    def click_physical_coords(self, x: Optional[int], y: Optional[int], last_win_args: Optional[Dict[str, Any]] = None) -> bool:
        # Why: ウィンドウ移動オフセットを自動加算し物理座標を確実にクリック
        if x is None or y is None or (x <= 20 and y <= 20):
            return False
        off_x, off_y = 0, 0
        if last_win_args and platform.system() == "Windows":
            try:
                target_hwnd = last_win_args.get("mapped_hwnd") or ctypes.windll.user32.GetForegroundWindow()
                if target_hwnd:
                    ctypes.windll.user32.SetForegroundWindow(target_hwnd)
                    time.sleep(0.05)
                rec_x = last_win_args.get("x", 0)
                rec_y = last_win_args.get("y", 0)
                from core.executor.input_helper import get_window_offset

                off_x, off_y = get_window_offset(target_hwnd, rec_x, rec_y)
            except Exception:
                pass

        actual_x = int(x + off_x)
        actual_y = int(y + off_y)
        from core.executor.input_helper import smooth_move

        smooth_move(actual_x, actual_y)
        time.sleep(0.04)
        set_system_cursor("run_click")
        self._mouse.click(Button.left, 1)
        time.sleep(0.06)
        set_system_cursor("run_idle")
        time.sleep(0.1)
        return True

    def perform_typing_input(self, text: str, clear_before: bool = True) -> None:
        # Why: 既存テキストを消去しクリップボード貼付と仮想DOM通知キーで確実に入力
        if clear_before:
            self._keyboard.press(Key.ctrl)
            self._keyboard.press("a")
            self._keyboard.release("a")
            self._keyboard.release(Key.ctrl)
            time.sleep(0.04)
            self._keyboard.press(Key.backspace)
            self._keyboard.release(Key.backspace)
            time.sleep(0.04)

        from core.executor.input_helper import send_unicode_string, set_clipboard_text
        from core.executor.os_env_controller import ensure_ime_state, normalize_text_width

        norm_text = normalize_text_width(text)
        # Why: クリップボード貼付直前はIMEを半角OFFにしてローマ字誤変換を完全根絶
        ensure_ime_state(target_state=False, timeout=0.4)
        time.sleep(0.04)

        # Why: クリップボード貼付とSendInput(Unicode)ダイレクト入力の二重化で確実に入力
        if set_clipboard_text(norm_text):
            self._keyboard.press(Key.ctrl)
            self._keyboard.press("v")
            self._keyboard.release("v")
            self._keyboard.release(Key.ctrl)
            time.sleep(0.06)
            self._keyboard.press(Key.right)
            self._keyboard.release(Key.right)
            time.sleep(0.06)
        else:
            send_unicode_string(norm_text)
        time.sleep(0.15)

    def open_url(self, url: str) -> None:
        if not url:
            return
        from core.executor.input_helper import set_clipboard_text
        from core.executor.os_env_controller import ensure_ime_state, normalize_text_width

        norm_url = normalize_text_width(url)
        # Why: IME状況を監視し半角状態を確認してからアドレスバーへ遷移
        ensure_ime_state(target_state=False, timeout=0.6)
        time.sleep(0.05)
        self._keyboard.press(Key.ctrl)
        self._keyboard.press("l")
        self._keyboard.release("l")
        self._keyboard.release(Key.ctrl)
        time.sleep(0.08)
        ensure_ime_state(target_state=False, timeout=0.4)
        if set_clipboard_text(norm_url):
            time.sleep(0.04)
            self._keyboard.press(Key.ctrl)
            self._keyboard.press("v")
            self._keyboard.release("v")
            self._keyboard.release(Key.ctrl)
            time.sleep(0.04)
        else:
            self._keyboard.type(norm_url)
        time.sleep(0.05)
        self._keyboard.press(Key.enter)
        self._keyboard.release(Key.enter)
        time.sleep(1.0)

    def close_tab(self) -> None:
        self._keyboard.press(Key.ctrl)
        self._keyboard.press("w")
        self._keyboard.release("w")
        self._keyboard.release(Key.ctrl)
        time.sleep(0.3)

    def switch_tab(self) -> None:
        self._keyboard.press(Key.ctrl)
        self._keyboard.press(Key.tab)
        self._keyboard.release(Key.tab)
        self._keyboard.release(Key.ctrl)
        time.sleep(0.3)

    def hover_by_uia_or_selector(
        self,
        selector: Optional[str],
        last_win_args: Optional[Dict[str, Any]],
        timeout: float = 0.8,
        element_name: Optional[str] = None,
        target_x: Optional[int] = None,
        target_y: Optional[int] = None,
        **kwargs: Any,
    ) -> bool:
        # Why: ブラウザ要素を表示領域内へ自動スクロールしホバーメニューを確実に展開
        coords = scroll_into_view(
            selector, last_win_args, timeout_sec=timeout, element_name=element_name, x=target_x, y=target_y, **kwargs
        )
        hx, hy = coords if coords else (None, None)
        if hx is None and target_x is not None and target_y is not None and target_x > 20 and target_y > 20:
            hx, hy = int(target_x), int(target_y)

        if hx is not None and hy is not None:
            if platform.system() == "Windows":
                target_hwnd = last_win_args.get("mapped_hwnd") if last_win_args else None
                if target_hwnd:
                    ctypes.windll.user32.SetForegroundWindow(target_hwnd)
                    time.sleep(0.04)
                from core.executor.input_helper import smooth_move

                smooth_move(hx, hy, steps=12, duration=0.15)
                # Why: レンダラープロセスにマウス進入・ホバーを認識させる微小往復イベント
                self._mouse.position = (hx, hy)
                ctypes.windll.user32.mouse_event(0x0001, 1, 0, 0, 0)
                ctypes.windll.user32.mouse_event(0x0001, -1, 0, 0, 0)
            else:
                self._mouse.position = (hx, hy)
            # Why: ドロップダウンやメガメニューのCSSアニメーション展開時間を確実に待機
            time.sleep(0.55)
            return True
        return False

    def click_by_uia_or_selector(
        self,
        selector: Optional[str],
        last_win_args: Optional[Dict[str, Any]],
        timeout: float = 0.5,
        element_name: Optional[str] = None,
        url: Optional[str] = None,
        timeout_sec: Optional[float] = None,
        **kwargs: Any,
    ) -> bool:
        effective_timeout = timeout_sec if timeout_sec is not None else timeout
        # Why: 汎用タグ(div等)単体セレクタは誤爆および誤スクロール防止のため回避
        sel_clean = str(selector or "").strip().lower()
        if sel_clean in SIMPLE_GENERIC_TAGS:
            return False

        coords = scroll_into_view(
            selector,
            last_win_args,
            timeout_sec=effective_timeout,
            element_name=element_name,
            url=url,
            text=kwargs.get("text"),
            x=kwargs.get("x"),
            y=kwargs.get("y"),
        )
        if coords:
            cx, cy = coords
            if platform.system() == "Windows":
                from core.executor.input_helper import smooth_move

                smooth_move(cx, cy)
                time.sleep(0.04)
                set_system_cursor("run_click")
                time.sleep(0.03)
                self._mouse.click(Button.left, 1)
                time.sleep(0.06)
                set_system_cursor("run_idle")
            time.sleep(0.2)
            return True

        elem = find_uia_element(selector, last_win_args, timeout_sec=effective_timeout, element_name=element_name, url=url, **kwargs)
        if elem:
            rect = elem.rectangle()
            cx = (rect.left + rect.right) // 2
            cy = (rect.top + rect.bottom) // 2
            if cx <= 20 or cy <= 20 or rect.width() <= 0 or rect.height() <= 0:
                logger.warning(f"UIA element has invalid rectangle ({cx}, {cy}). Falling back to physical coords.")
                return False
            if platform.system() == "Windows":
                from core.executor.input_helper import smooth_move

                smooth_move(cx, cy)
                time.sleep(0.04)
                set_system_cursor("run_click")
                time.sleep(0.03)
                self._mouse.click(Button.left, 1)
                time.sleep(0.06)
                set_system_cursor("run_idle")
            time.sleep(0.2)
            return True

        logger.info(f"UIA element not resolved for selector: '{selector}', name: '{element_name}'. Falling back.")
        return False

    def type_by_uia_or_selector(
        self,
        selector: Optional[str],
        text: str,
        clear_before: bool = True,
        last_win_args: Optional[Dict[str, Any]] = None,
        timeout: float = 0.6,
        element_name: Optional[str] = None,
        timeout_sec: Optional[float] = None,
        **kwargs: Any,
    ) -> bool:
        # Why: アドレスバー・検索バーはCtrl+Lで確実にフォーカスを即時奪取
        if is_address_bar_target(selector, element_name):
            self._keyboard.press(Key.ctrl)
            self._keyboard.press("l")
            self._keyboard.release("l")
            self._keyboard.release(Key.ctrl)
            time.sleep(0.08)
            self.perform_typing_input(text, clear_before)
            return True

        effective_timeout = timeout_sec if timeout_sec is not None else timeout
        clicked = self.click_by_uia_or_selector(selector, last_win_args, timeout=effective_timeout, element_name=element_name)
        if not clicked:
            return False
        self.perform_typing_input(text, clear_before)
        return True

    def execute_click_element(
        self,
        args: Dict[str, Any],
        selector: str,
        url: str,
        text: str,
        attr_name: str,
        timeout_sec: float,
        last_win_args: Optional[Dict[str, Any]],
        workflow_id: str,
        res_data: Dict[str, Any],
    ) -> None:
        elem_name = attr_name or args.get("element_name") or text
        clicked = False
        if self.click_by_uia_or_selector(
            selector, last_win_args, timeout_sec=max(2.5, min(timeout_sec, 6.0)), element_name=elem_name, text=text
        ):
            clicked = True
            res_data["status"] = "uia_click_succeeded"

        if not clicked:
            x = args.get("x")
            y = args.get("y")
            if self.click_physical_coords(x, y, last_win_args):
                logger.info(f"[{workflow_id}] Button click fell back to physical click at ({x}, {y})")
                res_data["status"] = "fallback_click_succeeded"
            elif url and str(url).startswith("http"):
                # Why: クリックが届かない場合も遷移先URLへの直接ナビゲーションでサイト遷移を保証
                logger.info(f"[{workflow_id}] Click missed, navigating directly to target URL: {url}")
                self.open_url(url)
                res_data["status"] = "url_navigation_succeeded"
            else:
                logger.warning(f"[{workflow_id}] Click failed for selector '{selector}' and no valid fallback coordinates.")
                res_data["status"] = "click_failed"

    def execute_type_text(
        self,
        args: Dict[str, Any],
        selector: str,
        text: str,
        attr_name: str,
        timeout_sec: float,
        clear_before: bool,
        last_win_args: Optional[Dict[str, Any]],
        workflow_id: str,
        res_data: Dict[str, Any],
    ) -> None:
        elem_name = attr_name or args.get("element_name")
        x = args.get("x")
        y = args.get("y")
        typed = False

        # Why: アドレスバー操作はUIA走査のタイムアウトを待たずにCtrl+Lで即時入力
        if is_address_bar_target(selector, elem_name):
            self._keyboard.press(Key.ctrl)
            self._keyboard.press("l")
            self._keyboard.release("l")
            self._keyboard.release(Key.ctrl)
            time.sleep(0.08)
            self.perform_typing_input(text, clear_before)
            typed = True
            res_data["status"] = "address_bar_typing_succeeded"

        if not typed and (selector or elem_name):
            typed = self.type_by_uia_or_selector(
                selector, text, clear_before, last_win_args, timeout=max(2.5, min(timeout_sec, 6.0)), element_name=elem_name
            )
            if typed:
                res_data["status"] = "selector_typing_succeeded"

        if not typed and x is not None and y is not None and x > 20 and y > 20:
            self.click_physical_coords(x, y, last_win_args)
            time.sleep(0.08)
            self.perform_typing_input(text, clear_before)
            typed = True
            res_data["status"] = "coords_typing_succeeded"

        if not typed:
            if self.click_by_uia_or_selector(selector, last_win_args, timeout=2.0, element_name=elem_name):
                time.sleep(0.08)
                self.perform_typing_input(text, clear_before)
                res_data["status"] = "retry_typing_succeeded"
            else:
                logger.warning(f"[{workflow_id}] type_text failed to locate focus for '{selector or elem_name}', skipped to prevent mis-typing.")
                res_data["status"] = "typing_skipped_no_focus"

    def execute_select_option(
        self,
        args: Dict[str, Any],
        selector: str,
        text: str,
        value: Any,
        attr_name: str,
        timeout_sec: float,
        last_win_args: Optional[Dict[str, Any]],
        res_data: Dict[str, Any],
    ) -> Dict[str, Any]:
        target_val = str(value if value is not None else text).strip()
        elem_name_arg = str(attr_name or args.get("element_name") or "").strip()
        opt_x = args.get("x")
        opt_y = args.get("y")

        sc_coords = scroll_into_view(
            selector, last_win_args, timeout_sec=max(2.5, min(timeout_sec, 6.0)), element_name=elem_name_arg, text=target_val, x=opt_x, y=opt_y
        )
        if sc_coords:
            opt_x, opt_y = sc_coords
        elem = find_uia_element(
            selector, last_win_args, timeout_sec=max(2.5, min(timeout_sec, 6.0)), element_name=elem_name_arg, text=target_val, x=opt_x, y=opt_y
        )

        selected_ok = False
        try:
            import uiautomation as auto

            ctrl = auto.ControlFromElement(elem.element_info._element) if elem else None

            cur_v = get_combobox_current_value(ctrl, elem, elem_name_arg)
            if cur_v and (cur_v.lower() == target_val.lower() or target_val.lower() in cur_v.lower()):
                logger.info(f"select_option: '{target_val}' is already selected.")
                res_data["selected"] = target_val
                return res_data

            if elem:
                rect = elem.rectangle()
                cx = (rect.left + rect.right) // 2
                cy = (rect.top + rect.bottom) // 2
                if cx > 20 and cy > 20:
                    self.click_physical_coords(cx, cy, last_win_args)
                else:
                    elem.click_input()
            elif opt_x is not None and opt_y is not None:
                self.click_physical_coords(opt_x, opt_y, last_win_args)

            time.sleep(0.12)

            # Why: 展開状態では確定までValueが変化しないためEscで閉じフォーカスを保持
            self._keyboard.press(Key.esc)
            self._keyboard.release(Key.esc)
            time.sleep(0.08)

            if not ctrl:
                try:
                    ctrl = auto.GetFocusedControl()
                    if not ctrl or not ctrl.Exists(0, 0):
                        target_hwnd = last_win_args.get("mapped_hwnd") if last_win_args else None
                        rec_x = last_win_args.get("x", 0) if last_win_args else 0
                        rec_y = last_win_args.get("y", 0) if last_win_args else 0
                        from core.executor.input_helper import get_window_offset

                        off_x, off_y = get_window_offset(target_hwnd, rec_x, rec_y)
                        ctrl = auto.ControlFromPoint(int(opt_x + off_x), int(opt_y + off_y))
                except Exception:
                    pass

            cur_v = get_combobox_current_value(ctrl, elem, elem_name_arg)
            if cur_v and (cur_v.lower() == target_val.lower() or target_val.lower() in cur_v.lower()):
                selected_ok = True

            if not selected_ok:
                self._keyboard.press(Key.home)
                self._keyboard.release(Key.home)
                time.sleep(0.08)

                prev_v = ""
                for step in range(30):
                    cur_v = get_combobox_current_value(ctrl, elem, elem_name_arg)
                    if cur_v and (cur_v.lower() == target_val.lower() or target_val.lower() in cur_v.lower()):
                        logger.info(f"select_option: matched '{target_val}' at step {step} (detected: '{cur_v}')")
                        selected_ok = True
                        break
                    if cur_v and cur_v == prev_v and step > 1:
                        break
                    prev_v = cur_v
                    self._keyboard.press(Key.down)
                    self._keyboard.release(Key.down)
                    time.sleep(0.08)

            self._keyboard.press(Key.enter)
            self._keyboard.release(Key.enter)
            time.sleep(0.06)
            res_data["selected"] = target_val
        except Exception as e:
            logger.warning(f"select_option failed: {e}")
            res_data["status"] = "failed"

        return res_data

    def execute_set_checkbox(
        self,
        args: Dict[str, Any],
        selector: str,
        text: str,
        value: Any,
        attr_name: str,
        timeout_sec: float,
        last_win_args: Optional[Dict[str, Any]],
        res_data: Dict[str, Any],
    ) -> Dict[str, Any]:
        # Why: エクセル由来の多様な表記を普遍的に真偽値へ正規化
        raw_v = value if value is not None else text
        desired = False
        if raw_v is not None:
            if isinstance(raw_v, bool):
                desired = raw_v
            elif isinstance(raw_v, (int, float)):
                desired = raw_v != 0
            else:
                s_v = str(raw_v).strip().lower()
                if s_v in ["true", "1", "yes", "y", "on", "あり", "有", "チェック", "○", "レ", "checked"]:
                    desired = True

        opt_x = args.get("x")
        opt_y = args.get("y")
        elem_name_arg = str(attr_name or args.get("element_name") or "").strip()

        sc_coords = scroll_into_view(
            selector, last_win_args, timeout_sec=max(2.5, min(timeout_sec, 6.0)), element_name=elem_name_arg, text=text, x=opt_x, y=opt_y
        )
        if sc_coords:
            opt_x, opt_y = sc_coords
        elem = find_uia_element(
            selector, last_win_args, timeout_sec=max(2.5, min(timeout_sec, 6.0)), element_name=elem_name_arg, text=text, x=opt_x, y=opt_y
        )

        import uiautomation as auto

        ctrl = auto.ControlFromElement(elem.element_info._element) if elem else None
        if not ctrl and opt_x is not None and opt_y is not None:
            try:
                target_hwnd = last_win_args.get("mapped_hwnd") if last_win_args else None
                rec_x = last_win_args.get("x", 0) if last_win_args else 0
                rec_y = last_win_args.get("y", 0) if last_win_args else 0
                from core.executor.input_helper import get_window_offset

                off_x, off_y = get_window_offset(target_hwnd, rec_x, rec_y)
                ctrl = auto.ControlFromPoint(int(opt_x + off_x), int(opt_y + off_y))
            except Exception:
                pass

        is_checked = None
        if ctrl and ctrl.Exists(0, 0):
            try:
                tog_pat = ctrl.GetTogglePattern()
                if tog_pat:
                    is_checked = tog_pat.ToggleState == 1
            except Exception:
                pass
            if is_checked is None:
                try:
                    leg_pat = ctrl.GetLegacyIAccessiblePattern()
                    if leg_pat:
                        is_checked = bool(leg_pat.CurrentState & 0x10)
                except Exception:
                    pass

        # Why: 既に希望状態であれば不要な反転クリックを完全抑止
        if is_checked is not None and is_checked == desired:
            logger.info(f"set_checkbox: '{selector}' is already {desired}.")
            res_data["checked"] = desired
            return res_data

        toggled = False
        if ctrl and ctrl.Exists(0, 0):
            try:
                tog_pat = ctrl.GetTogglePattern()
                if tog_pat:
                    tog_pat.Toggle()
                    time.sleep(0.08)
                    toggled = tog_pat.ToggleState == (1 if desired else 0)
            except Exception:
                pass

        if not toggled:
            cx, cy = None, None
            if elem:
                rect = elem.rectangle()
                cx = (rect.left + rect.right) // 2
                cy = (rect.top + rect.bottom) // 2
            elif opt_x is not None and opt_y is not None:
                cx, cy = opt_x, opt_y

            if cx is not None and cy is not None and cx > 20 and cy > 20:
                if self.click_physical_coords(cx, cy, last_win_args):
                    toggled = True

        if not toggled and elem:
            try:
                elem.click_input()
                toggled = True
            except Exception:
                pass

        time.sleep(0.06)
        res_data["checked"] = desired
        if not toggled and is_checked is None:
            res_data["status"] = "element_not_found"
        return res_data
