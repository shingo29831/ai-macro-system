"""Module: @role: ブラウザ上の個別操作（クリック、ホバー、文字入力、URL遷移、セレクトボックス、チェックボックス）の実行制御を担当する。"""

import logging
import time
from typing import Any, Dict, Optional

from pynput.keyboard import Controller as KeyboardController
from pynput.mouse import Controller as MouseController

from core.executor.browser_form_actions import (
    execute_select_option_action,
    execute_set_checkbox_action,
)
from core.executor.browser_input_driver import BrowserInputDriver
from core.executor.browser_uia_finder import (
    SIMPLE_GENERIC_TAGS,
    find_uia_element,
    scroll_into_view,
)

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
        self._input = BrowserInputDriver(mouse, keyboard)

    def click_physical_coords(self, x: Optional[int], y: Optional[int], last_win_args: Optional[Dict[str, Any]] = None) -> bool:
        return self._input.click_physical_coords(x, y, last_win_args)

    def perform_typing_input(self, text: str, clear_before: bool = True) -> None:
        self._input.perform_typing_input(text, clear_before)

    def open_url(self, url: str) -> None:
        self._input.open_url(url)

    def close_tab(self) -> None:
        self._input.close_tab()

    def switch_tab(self) -> None:
        self._input.switch_tab()

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
            self._input.hover_screen_point(hx, hy, last_win_args)
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
            self._input.click_screen_point(coords[0], coords[1])
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
            self._input.click_screen_point(cx, cy)
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
            self._input.focus_address_bar()
            self.perform_typing_input(text, clear_before)
            return True

        effective_timeout = timeout_sec if timeout_sec is not None else timeout
        if not self.click_by_uia_or_selector(selector, last_win_args, timeout=effective_timeout, element_name=element_name):
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
            self._input.focus_address_bar()
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
        return execute_select_option_action(
            self._input,
            self._keyboard,
            args,
            selector,
            text,
            value,
            attr_name,
            timeout_sec,
            last_win_args,
            res_data,
        )

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
        return execute_set_checkbox_action(
            self._input,
            args,
            selector,
            text,
            value,
            attr_name,
            timeout_sec,
            last_win_args,
            res_data,
        )
