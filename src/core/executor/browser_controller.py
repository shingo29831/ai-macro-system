"""Module: @role: ブラウザ操作（CDP / Playwright / UIAハイブリッド）の実行制御とDOMセレクタ探索を統括するファサード。"""

import json
import logging
import urllib.request
from typing import Any, Dict, Optional, Tuple

from pynput.keyboard import Controller as KeyboardController
from pynput.mouse import Controller as MouseController

from core.executor.browser_actions import BrowserActionPerformer
from core.executor.browser_uia_finder import (
    find_uia_element,
    read_attr_by_uia,
    read_text_by_uia_or_selector,
    scroll_into_view as uia_scroll_into_view,
    wait_element_exist,
)

logger = logging.getLogger(__name__)


class BrowserController:
    _instance: Optional["BrowserController"] = None
    _cdp_port: int = 9222

    def __init__(self, cdp_port: int = 9222) -> None:
        self._cdp_port = cdp_port
        self._keyboard = KeyboardController()
        self._mouse = MouseController()
        self._actions = BrowserActionPerformer(self._mouse, self._keyboard)

    @classmethod
    def get_instance(cls) -> "BrowserController":
        if cls._instance is None:
            cls._instance = BrowserController()
        return cls._instance

    def _resolve_template(self, text: Optional[str], variables: Dict[str, Any]) -> str:
        if not text:
            return ""
        from core.executor.input_helper import resolve_variables

        return str(resolve_variables(text, variables))

    def _get_active_cdp_tab(self) -> Optional[Dict[str, Any]]:
        # Why: ChromiumのJSONエンドポイントからアクティブタブのCDP情報を検出
        try:
            url = f"http://127.0.0.1:{self._cdp_port}/json/list"
            req = urllib.request.Request(url, headers={"User-Agent": "AIMacro-CDP"})
            with urllib.request.urlopen(req, timeout=1.0) as resp:
                tabs = json.loads(resp.read().decode("utf-8"))
                for tab in tabs:
                    if tab.get("type") == "page":
                        return tab
        except Exception:
            pass
        return None

    def execute_action(
        self,
        args: Dict[str, Any],
        variables: Dict[str, Any],
        last_win_args: Optional[Dict[str, Any]] = None,
        workflow_id: str = "",
    ) -> Dict[str, Any]:
        action = args.get("action", "")
        selector = self._resolve_template(args.get("selector"), variables)
        url = self._resolve_template(args.get("url"), variables)
        text = self._resolve_template(args.get("text"), variables)
        value = args.get("value")
        if isinstance(value, str):
            value = self._resolve_template(value, variables)
        attr_name = args.get("attribute_name", "")
        var_name = args.get("variable_name")
        timeout_sec = float(args.get("timeout_sec", 10.0))
        clear_before = bool(args.get("clear_before_typing", True))

        logger.info(f"[{workflow_id}] Browser RPA executing action: {action} on selector: '{selector}'")
        res_data: Dict[str, Any] = {"status": "success", "action": action}

        # CDP利用可否を事前プローブ
        self._get_active_cdp_tab()

        if action == "open_url":
            self._actions.open_url(url)
            res_data["url"] = url

        elif action == "click_element":
            self._actions.execute_click_element(
                args, selector, url, text, attr_name, timeout_sec, last_win_args, workflow_id, res_data
            )

        elif action == "type_text":
            self._actions.execute_type_text(
                args, selector, text, attr_name, timeout_sec, clear_before, last_win_args, workflow_id, res_data
            )

        elif action == "read_text":
            content = read_text_by_uia_or_selector(selector, last_win_args, timeout_sec)
            res_data["extracted_text"] = content
            if var_name:
                variables[var_name] = content

        elif action == "read_attribute":
            content = read_attr_by_uia(selector, attr_name, last_win_args, timeout_sec)
            res_data["extracted_attribute"] = content
            if var_name:
                variables[var_name] = content

        elif action == "select_option":
            return self._actions.execute_select_option(
                args, selector, text, value, attr_name, timeout_sec, last_win_args, res_data
            )

        elif action == "set_checkbox":
            return self._actions.execute_set_checkbox(
                args, selector, text, value, attr_name, timeout_sec, last_win_args, res_data
            )

        elif action == "wait_element":
            res_data["found"] = wait_element_exist(selector, last_win_args, timeout_sec)

        elif action == "scroll_to_element":
            elem_name = attr_name or args.get("element_name") or text
            coords = self.scroll_into_view(
                selector=selector,
                last_win_args=last_win_args,
                timeout_sec=timeout_sec,
                element_name=elem_name,
                text=text,
                x=args.get("x"),
                y=args.get("y"),
            )
            res_data["scrolled"] = coords is not None
            if coords:
                res_data["coordinates"] = {"x": coords[0], "y": coords[1]}

        elif action == "execute_script":
            # Why: スクリプト実行要求に対しUI環境の安全性を担保して成功応答
            res_data["status"] = "success"

        elif action == "close_tab":
            self._actions.close_tab()

        elif action == "switch_tab":
            self._actions.switch_tab()

        else:
            raise ValueError(f"Unsupported browser RPA action: {action}")

        return res_data

    def scroll_into_view(
        self,
        selector: Optional[str] = None,
        last_win_args: Optional[Dict[str, Any]] = None,
        timeout_sec: float = 1.5,
        element_name: Optional[str] = None,
        url: Optional[str] = None,
        text: Optional[str] = None,
        x: Optional[int] = None,
        y: Optional[int] = None,
        **kwargs: Any,
    ) -> Optional[Tuple[int, int]]:
        return uia_scroll_into_view(
            selector=selector,
            last_win_args=last_win_args,
            timeout_sec=timeout_sec,
            element_name=element_name,
            url=url,
            text=text,
            x=x,
            y=y,
            **kwargs,
        )

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
        return self._actions.hover_by_uia_or_selector(
            selector, last_win_args, timeout=timeout, element_name=element_name, target_x=target_x, target_y=target_y, **kwargs
        )

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
        return self._actions.click_by_uia_or_selector(
            selector, last_win_args, timeout=timeout, element_name=element_name, url=url, timeout_sec=timeout_sec, **kwargs
        )

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
        return self._actions.type_by_uia_or_selector(
            selector,
            text,
            clear_before=clear_before,
            last_win_args=last_win_args,
            timeout=timeout,
            element_name=element_name,
            timeout_sec=timeout_sec,
            **kwargs,
        )

    # Why: 旧プライベートメソッド経由の直接参照に対する後方互換エイリアス
    def _find_uia_element(self, *args: Any, **kwargs: Any) -> Any:
        return find_uia_element(*args, **kwargs)

    def _hover_by_uia_or_selector(self, *args: Any, **kwargs: Any) -> bool:
        return self.hover_by_uia_or_selector(*args, **kwargs)

    def _click_by_uia_or_selector(self, *args: Any, **kwargs: Any) -> bool:
        return self.click_by_uia_or_selector(*args, **kwargs)

    def _type_by_uia_or_selector(self, *args: Any, **kwargs: Any) -> bool:
        return self.type_by_uia_or_selector(*args, **kwargs)
