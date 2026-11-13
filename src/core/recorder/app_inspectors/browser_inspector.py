"""Module: @role: ブラウザ特有のUIA情報（URLバー、検索ボックス等）を取得し、主要ブラウザからのURL・検索クエリ抽出およびDOM探索を担当する実装"""

import logging
import traceback
from typing import Any, Dict, List, Optional, Tuple

from .base_inspector import BaseInspector
from .browser_form_extractor import (
    BrowserFormTracker,
    extract_value_from_elem,
    extract_value_from_uia,
    infer_element_selectors,
)
from .browser_url_extractor import get_address_bar_info as _get_address_bar_info
from .browser_url_extractor import parse_url_details as _parse_url_details

logger = logging.getLogger(__name__)

_INPUT_CONTROL_TYPES = {"edit", "combobox", "checkbox", "spinner", "radiobutton", "listitem"}
_INPUT_TAGS = {"input", "select", "textarea", "button"}


class BrowserInspector(BaseInspector):
    """ブラウザ操作時のUIAコンテキスト・フォームスナップショット・URL情報を統合取得するインスペクター。"""

    _tracker = BrowserFormTracker()

    @classmethod
    def reset_state(cls) -> None:
        # Why: 記録セッション間での直前セレクタ・値キャッシュの混入・汚染を完全防止
        cls._tracker.reset()

    @classmethod
    def extract_form_snapshot(cls, window_info: Dict[str, Any]) -> Dict[str, Any]:
        return cls._tracker.extract_form_snapshot(window_info)

    @staticmethod
    def parse_url_details(raw_url: str) -> Dict[str, Any]:
        """URL文字列からドメイン、パス、検索クエリ(q=等)を安全にデコード・パースする"""
        return _parse_url_details(raw_url)

    def get_address_bar_info(self, window_info: Dict[str, Any], desktop: Any = None) -> Dict[str, Any]:
        """UIAを用いて主要ブラウザのアドレスバーから確定URLおよび検索クエリを取得する"""
        return _get_address_bar_info(window_info, extract_value_from_elem, desktop=desktop)

    def inspect(self, window_info: Dict[str, Any], x: Optional[int] = None, y: Optional[int] = None) -> Dict[str, Any]:
        result: Dict[str, Any] = {
            "app": "Browser",
            "element_name": "",
            "control_type": "",
            "text": "",
            "value": "",
            "url": "",
            "query": None,
            "url_details": {},
            "debug_log": [],
        }

        def log_debug(msg: str) -> None:
            logger.debug(f"[BrowserInspector] {msg}")
            result["debug_log"].append(msg)

        log_debug(f"インスペクト開始: 座標({x}, {y}), ウィンドウ: {window_info.get('title', 'Unknown')}")

        try:
            import pythoncom
            import pywinauto

            pythoncom.CoInitialize()
            desktop = pywinauto.Desktop(backend="uia")

            target_elements = self._resolve_target_elements(desktop, window_info, x, y, result, log_debug)
            extracted_text, primary_elem = self._extract_from_candidates(target_elements, x, y, result, log_debug)
            result["text"] = extracted_text
            result["value"] = extracted_text

            self._attach_form_commit_state(x, y, result)

            if primary_elem:
                try:
                    css_sel, xpath_sel = infer_element_selectors(
                        primary_elem,
                        result.get("element_name") or "",
                        extracted_text,
                    )
                    if css_sel:
                        result["css_selector"] = css_sel
                    if xpath_sel:
                        result["xpath"] = xpath_sel
                except Exception as e:
                    log_debug(f"セレクタ推定中にエラー: {e}")

            self._attach_address_bar_context(window_info, desktop, primary_elem, extracted_text, x, y, result)
            self._update_snapshot_and_tracking(window_info, primary_elem, x, y, result)

        except ImportError:
            error_msg = "pywinauto がインストールされていません"
            result["error"] = error_msg
            log_debug(error_msg)
        except Exception as e:
            error_msg = f"インスペクト例外: {str(e)}"
            result["error"] = error_msg
            log_debug(error_msg)
            traceback.print_exc()
        finally:
            try:
                import pythoncom

                pythoncom.CoUninitialize()
            except Exception:
                pass

        log_debug(f"インスペクト完了. URL: '{result.get('url')}', クエリ: '{result.get('query')}'")
        return result

    def _resolve_target_elements(
        self,
        desktop: Any,
        window_info: Dict[str, Any],
        x: Optional[int],
        y: Optional[int],
        result: Dict[str, Any],
        log_debug: Any,
    ) -> List[Tuple[str, Any]]:
        target_elements: List[Tuple[str, Any]] = []
        # Why: 負の座標や画面外でのdesktop.from_pointによる0x80070057パラメーター不正例外を防止
        if x is not None and y is not None and x >= 0 and y >= 0:
            try:
                elem = desktop.from_point(int(x), int(y))
                if elem:
                    try:
                        r = elem.rectangle()
                        if r.left <= x <= r.right and r.top <= y <= r.bottom:
                            target_elements.append(("PointElement", elem))
                            log_debug("座標からのUI要素特定に成功しました")
                        else:
                            log_debug(
                                f"PointElementが座標({x}, {y})を含まないためスキップ "
                                f"(rect: {r.left},{r.top},{r.right},{r.bottom})"
                            )
                    except Exception:
                        target_elements.append(("PointElement", elem))
            except Exception as e:
                log_debug(f"座標からの要素特定に失敗: {e}")
                self._fallback_uia_from_point(x, y, result, log_debug)
        else:
            self._resolve_focused_element(window_info, result, log_debug)
        return target_elements

    def _fallback_uia_from_point(self, x: int, y: int, result: Dict[str, Any], log_debug: Any) -> None:
        try:
            import uiautomation as auto

            u_ctrl = auto.ControlFromPoint(int(x), int(y))
            if u_ctrl and u_ctrl.NativeWindowHandle:
                val = extract_value_from_uia(u_ctrl)
                aid = getattr(u_ctrl, "AutomationId", "") or ""
                c_name = getattr(u_ctrl, "Name", "") or ""
                ct_name = getattr(u_ctrl, "ControlTypeName", "") or ""
                if aid or c_name:
                    result["element_name"] = c_name
                    result["control_type"] = ct_name.replace("Control", "")
                    result["text"] = val
                    result["value"] = val
                    if aid:
                        result["css_selector"] = f"#{aid}"
                        result["xpath"] = f"//*[@id='{aid}']"
                    log_debug(f"uiautomation座標特定成功: id='{aid}', name='{c_name}', val='{val}'")
        except Exception as e2:
            log_debug(f"uiautomation座標フォールバック失敗: {e2}")

    def _resolve_focused_element(
        self,
        window_info: Dict[str, Any],
        result: Dict[str, Any],
        log_debug: Any,
    ) -> None:
        # Why: キー入力時は現在フォーカス要素または直前編集要素から確定入力値を救出
        try:
            import uiautomation as auto

            focused = auto.GetFocusedControl()
            if focused:
                val = extract_value_from_uia(focused)
                auto_id = getattr(focused, "AutomationId", "") or ""
                ctrl_type = getattr(auto, "ControlTypeName", lambda ct: "Edit")(focused.ControlType)
                elem_name = getattr(focused, "Name", "") or getattr(focused, "CurrentName", "") or ""
                if val or auto_id or elem_name:
                    result["text"] = val
                    result["value"] = val
                    result["control_type"] = ctrl_type
                    result["element_name"] = elem_name
                    if auto_id:
                        result["css_selector"] = f"#{auto_id}"
                        result["xpath"] = f"//*[@id='{auto_id}']"
                    log_debug(f"フォーカス要素直接取得成功: id='{auto_id}', name='{elem_name}', val='{val}'")
        except Exception as ef:
            log_debug(f"フォーカス要素特定例外: {ef}")
            cur_hwnd = window_info.get("hwnd") or window_info.get("handle")
            fallback = BrowserInspector._tracker.get_fallback_for_hwnd(cur_hwnd)
            if fallback:
                result["css_selector"], result["value"] = fallback
                result["text"] = result["value"]

    def _extract_from_candidates(
        self,
        target_elements: List[Tuple[str, Any]],
        x: Optional[int],
        y: Optional[int],
        result: Dict[str, Any],
        log_debug: Any,
    ) -> Tuple[str, Any]:
        extracted_text = ""
        primary_elem = None
        for source_name, elem in target_elements:
            if not elem:
                continue
            if primary_elem is None:
                primary_elem = elem

            try:
                c_type = getattr(elem.element_info, "control_type", "") or ""
                e_name = elem.window_text() or ""
                log_debug(f"[{source_name}] 評価中 - Type: {c_type}, Name: '{e_name}'")
                if not result["control_type"]:
                    result["control_type"] = c_type
                if not result["element_name"]:
                    result["element_name"] = e_name
            except Exception:
                continue

            extracted_text = extract_value_from_elem(elem)
            if extracted_text:
                break

            try:
                for idx, child in enumerate(elem.children()[:20]):
                    # Why: 座標指定時はクリック地点を含まない無関係な子要素テキストの誤取得を完全防止
                    if x is not None and y is not None:
                        try:
                            r = child.rectangle()
                            if not (r.left <= x <= r.right and r.top <= y <= r.bottom):
                                continue
                        except Exception:
                            continue
                    child_text = extract_value_from_elem(child)
                    if child_text:
                        extracted_text = child_text
                        log_debug(f"[{source_name}] 子要素({idx})からテキストを救出")
                        try:
                            result["control_type"] = getattr(child.element_info, "control_type", "") or result["control_type"]
                        except Exception:
                            pass
                        break
            except Exception as e:
                log_debug(f"[{source_name}] 子要素走査例外: {e}")

            if extracted_text:
                break
        return extracted_text, primary_elem

    def _attach_form_commit_state(self, x: Optional[int], y: Optional[int], result: Dict[str, Any]) -> None:
        if x is not None and y is not None:
            committed_prev = BrowserInspector._tracker.commit_previous_element()
            if committed_prev:
                result["committed_previous_value"] = committed_prev
        if BrowserInspector._tracker.form_values_cache:
            result["committed_values"] = dict(BrowserInspector._tracker.form_values_cache)

    def _attach_address_bar_context(
        self,
        window_info: Dict[str, Any],
        desktop: Any,
        primary_elem: Any,
        extracted_text: str,
        x: Optional[int],
        y: Optional[int],
        result: Dict[str, Any],
    ) -> None:
        addr_info = self.get_address_bar_info(window_info, desktop=desktop)
        result["page_url"] = addr_info.get("url", "")

        # Why: クリック座標がアドレスバー内か判定し、Web内要素とアドレスバー操作を明確に分離
        is_address_bar = False
        addr_rect = addr_info.get("address_bar_rect")
        if addr_rect and x is not None and y is not None:
            if addr_rect["left"] <= x <= addr_rect["right"] and addr_rect["top"] <= y <= addr_rect["bottom"]:
                is_address_bar = True
        elif primary_elem:
            aid = getattr(primary_elem.element_info, "automation_id", "").lower()
            aname = (primary_elem.window_text() or "").lower()
            if any(k in aid for k in ("urlbar", "address", "omnibox")) or any(
                k in aname for k in ("アドレス", "address bar")
            ):
                is_address_bar = True
        elif x is None and y is None and addr_info.get("address_bar_text"):
            is_address_bar = True

        result["is_address_bar"] = is_address_bar
        if is_address_bar:
            result["url"] = addr_info.get("url", "")
            result["query"] = addr_info.get("query")
            result["url_details"] = addr_info.get("url_details", {})
            if not result["text"]:
                result["text"] = addr_info.get("address_bar_text", "")
                result["value"] = result["text"]
        else:
            # Why: 画面内テキストにURLが含まれるだけの非リンク誤認を防ぎHyperlink時のみURL設定
            is_link_control = "hyperlink" in str(result.get("control_type", "")).lower()
            if is_link_control and extracted_text and extracted_text.startswith(("http://", "https://")):
                result["url"] = extracted_text
            else:
                result["url"] = ""

    def _update_snapshot_and_tracking(
        self,
        window_info: Dict[str, Any],
        primary_elem: Any,
        x: Optional[int],
        y: Optional[int],
        result: Dict[str, Any],
    ) -> None:
        c_type_l = str(result.get("control_type", "")).lower()
        e_name_l = str(result.get("element_name", "")).lower()
        sel_l = str(result.get("css_selector", "")).lower()
        is_submit_action = (
            "button" in c_type_l
            or any(k in e_name_l for k in ("登録", "送信", "保存", "submit", "save"))
            or "submit" in sel_l
        )
        if is_submit_action or (x is not None and y is not None):
            snapshot = BrowserInspector.extract_form_snapshot(window_info)
            if snapshot:
                result["form_snapshot"] = snapshot

        # Why: コントロール種別およびタグ構造に基づく普遍的な入力要素判定
        is_input = (
            any(t in c_type_l for t in _INPUT_CONTROL_TYPES)
            or any(sel_l.startswith(tag) or f">{tag}" in sel_l for tag in _INPUT_TAGS)
            or "textbox" in c_type_l
            or "editable" in c_type_l
        )
        if is_input and primary_elem:
            BrowserInspector._tracker.record_input_element(
                element=primary_elem,
                selector=result.get("css_selector"),
                element_name=result.get("element_name"),
                hwnd=window_info.get("hwnd") or window_info.get("handle"),
            )
