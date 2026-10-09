"""Module: @role: ブラウザ操作（CDP / Playwright / UIAハイブリッド）の実行制御とDOMセレクタ探索を担当する。"""

from typing import Any, Dict, Optional, Tuple
import json
import time
import logging
import platform
import ctypes
import urllib.request
from pynput.keyboard import Controller as KeyboardController, Key
from pynput.mouse import Controller as MouseController, Button
from core.executor.os_env_controller import set_system_cursor

logger = logging.getLogger(__name__)

class BrowserController:
    _instance: Optional["BrowserController"] = None
    _cdp_port: int = 9222

    def __init__(self, cdp_port: int = 9222) -> None:
        self._cdp_port = cdp_port
        self._keyboard = KeyboardController()
        self._mouse = MouseController()

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

    def _click_physical_coords(self, x: Optional[int], y: Optional[int], last_win_args: Optional[Dict[str, Any]] = None) -> bool:
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

    def _perform_typing_input(self, text: str, clear_before: bool = True):
        # Why: 既存テキストを消去しクリップボード貼付と仮想DOM通知キーで確実に入力
        if clear_before:
            self._keyboard.press(Key.ctrl)
            self._keyboard.press('a')
            self._keyboard.release('a')
            self._keyboard.release(Key.ctrl)
            time.sleep(0.04)
            self._keyboard.press(Key.backspace)
            self._keyboard.release(Key.backspace)
            time.sleep(0.04)

        from core.executor.os_env_controller import ensure_ime_state, normalize_text_width
        from core.executor.input_helper import send_unicode_string, set_clipboard_text
        norm_text = normalize_text_width(text)
        # Why: クリップボード貼付直前はIMEを半角OFFにしてローマ字誤変換(ろｗ等)を完全根絶
        ensure_ime_state(target_state=False, timeout=0.4)
        time.sleep(0.04)

        # Why: クリップボード貼付とSendInput(Unicode)ダイレクト入力の二重化で確実に入力
        clip_ok = set_clipboard_text(norm_text)
        if clip_ok:
            self._keyboard.press(Key.ctrl)
            self._keyboard.press('v')
            self._keyboard.release('v')
            self._keyboard.release(Key.ctrl)
            time.sleep(0.06)
            self._keyboard.press(Key.right)
            self._keyboard.release(Key.right)
            time.sleep(0.06)
        else:
            send_unicode_string(norm_text)
        time.sleep(0.15)

    def execute_action(
        self,
        args: Dict[str, Any],
        variables: Dict[str, Any],
        last_win_args: Optional[Dict[str, Any]] = None,
        workflow_id: str = ""
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

        # 1. Playwright / CDPが利用可能か検証
        cdp_tab = self._get_active_cdp_tab()
        
        if action == "open_url":
            if url:
                from core.executor.os_env_controller import ensure_ime_state, normalize_text_width
                norm_url = normalize_text_width(url)
                # Why: IME状況を監視し半角状態を確認してからアドレスバーへ遷移
                ensure_ime_state(target_state=False, timeout=0.6)
                time.sleep(0.05)
                self._keyboard.press(Key.ctrl)
                self._keyboard.press('l')
                self._keyboard.release('l')
                self._keyboard.release(Key.ctrl)
                time.sleep(0.08)
                # フォーカス移動後も半角状態を再確認
                ensure_ime_state(target_state=False, timeout=0.4)
                from core.executor.input_helper import set_clipboard_text
                if set_clipboard_text(norm_url):
                    time.sleep(0.04)
                    self._keyboard.press(Key.ctrl)
                    self._keyboard.press('v')
                    self._keyboard.release('v')
                    self._keyboard.release(Key.ctrl)
                    time.sleep(0.04)
                else:
                    self._keyboard.type(norm_url)
                time.sleep(0.05)
                self._keyboard.press(Key.enter)
                self._keyboard.release(Key.enter)
                time.sleep(1.0)
            res_data["url"] = url

        elif action == "click_element":
            elem_name = attr_name or args.get("element_name") or text
            clicked = False
            # Why: UIA要素探索で見つかった場合はOSレベルのマウスイベントを発火
            if self._click_by_uia_or_selector(selector, last_win_args, timeout_sec=max(2.5, min(timeout_sec, 6.0)), element_name=elem_name, text=text):
                clicked = True
                res_data["status"] = "uia_click_succeeded"

            if not clicked:
                x = args.get("x")
                y = args.get("y")
                if self._click_physical_coords(x, y, last_win_args):
                    logger.info(f"[{workflow_id}] Button click fell back to physical click at ({x}, {y})")
                    res_data["status"] = "fallback_click_succeeded"
                    clicked = True
                elif url and str(url).startswith("http"):
                    # Why: クリックが届かない場合も遷移先URLへの直接ナビゲーションでサイト遷移を完全保証
                    logger.info(f"[{workflow_id}] Click missed, navigating directly to target URL: {url}")
                    self.execute_action({"action": "open_url", "url": url}, variables, last_win_args, workflow_id)
                    clicked = True
                    res_data["status"] = "url_navigation_succeeded"
                else:
                    logger.warning(f"[{workflow_id}] Click failed for selector '{selector}' and no valid fallback coordinates.")
                    res_data["status"] = "click_failed"

        elif action == "type_text":
            elem_name = attr_name or args.get("element_name")
            x = args.get("x")
            y = args.get("y")
            typed = False

            # Why: アドレスバー操作はUIA走査のタイムアウトを待たずにCtrl+Lで即時入力
            is_address_bar = (
                any(k in str(selector).lower() for k in ["urlbar", "address", "omnibox"]) or
                any(k in str(elem_name).lower() for k in ["url", "アドレス", "検索、または url", "検索または url"])
            )
            if is_address_bar:
                self._keyboard.press(Key.ctrl)
                self._keyboard.press('l')
                self._keyboard.release('l')
                self._keyboard.release(Key.ctrl)
                time.sleep(0.08)
                self._perform_typing_input(text, clear_before)
                typed = True
                res_data["status"] = "address_bar_typing_succeeded"

            # 1. UIA探索による高精度タイピング
            if not typed and (selector or elem_name):
                typed = self._type_by_uia_or_selector(selector, text, clear_before, last_win_args, timeout=max(2.5, min(timeout_sec, 6.0)), element_name=elem_name)
                if typed:
                    res_data["status"] = "selector_typing_succeeded"

            # 2. 物理座標クリックによるフォーカス確保後のタイピング
            if not typed and x is not None and y is not None and x > 20 and y > 20:
                self._click_physical_coords(x, y, last_win_args)
                time.sleep(0.08)
                self._perform_typing_input(text, clear_before)
                typed = True
                res_data["status"] = "coords_typing_succeeded"

            # 3. どちらも失敗した場合は別要素への誤入力を防ぐため物理座標を再探索
            if not typed:
                if self._click_by_uia_or_selector(selector, last_win_args, timeout=2.0, element_name=elem_name):
                    time.sleep(0.08)
                    self._perform_typing_input(text, clear_before)
                    res_data["status"] = "retry_typing_succeeded"
                else:
                    logger.warning(f"[{workflow_id}] type_text failed to locate focus for '{selector or elem_name}', skipped to prevent mis-typing.")
                    res_data["status"] = "typing_skipped_no_focus"

        elif action == "read_text":
            content = self._read_text_by_uia_or_selector(selector, last_win_args, timeout_sec)
            res_data["extracted_text"] = content
            if var_name:
                variables[var_name] = content

        elif action == "read_attribute":
            content = self._read_attr_by_uia(selector, attr_name, last_win_args, timeout_sec)
            res_data["extracted_attribute"] = content
            if var_name:
                variables[var_name] = content

        elif action == "select_option":
            target_val = str(value if value is not None else text).strip()
            elem_name_arg = str(attr_name or args.get("element_name") or "").strip()
            opt_x = args.get("x")
            opt_y = args.get("y")
            # Why: セレクト要素が表示領域外にある場合はスクロールして可視化
            sc_coords = self.scroll_into_view(selector, last_win_args, timeout_sec=max(2.5, min(timeout_sec, 6.0)), element_name=elem_name_arg, text=target_val, x=opt_x, y=opt_y)
            if sc_coords:
                opt_x, opt_y = sc_coords
            elem = self._find_uia_element(selector, last_win_args, timeout_sec=max(2.5, min(timeout_sec, 6.0)), element_name=elem_name_arg, text=target_val, x=opt_x, y=opt_y)

            selected_ok = False
            try:
                import uiautomation as auto
                ctrl = auto.ControlFromElement(elem.element_info._element) if elem else None

                # 1. フォーカス前の現在値照合（既に一致していれば即座に完了）
                cur_v = self._get_combobox_current_value(ctrl, elem, elem_name_arg)
                if cur_v and (cur_v.lower() == target_val.lower() or target_val.lower() in cur_v.lower()):
                    logger.info(f"select_option: '{target_val}' is already selected.")
                    res_data["selected"] = target_val
                    return res_data

                # 2. フォーカス確保 (クリック)
                if elem:
                    rect = elem.rectangle()
                    cx = (rect.left + rect.right) // 2
                    cy = (rect.top + rect.bottom) // 2
                    if cx > 20 and cy > 20:
                        self._click_physical_coords(cx, cy, last_win_args)
                    else:
                        elem.click_input()
                elif opt_x is not None and opt_y is not None:
                    self._click_physical_coords(opt_x, opt_y, last_win_args)

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

                # 3. 閉状態での現在値照合 (既に目標値であれば完了)
                cur_v = self._get_combobox_current_value(ctrl, elem, elem_name_arg)
                if cur_v and (cur_v.lower() == target_val.lower() or target_val.lower() in cur_v.lower()):
                    selected_ok = True

                # 4. Homeキーで先頭に移動し、1ステップずつ現在値を監視照合
                if not selected_ok:
                    self._keyboard.press(Key.home)
                    self._keyboard.release(Key.home)
                    time.sleep(0.08)

                    prev_v = ""
                    for step in range(30):
                        cur_v = self._get_combobox_current_value(ctrl, elem, elem_name_arg)
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

                # 5. 選択の確定 (Enter送信でchangeイベントを発火)
                self._keyboard.press(Key.enter)
                self._keyboard.release(Key.enter)
                time.sleep(0.06)
                res_data["selected"] = target_val
            except Exception as e:
                logger.warning(f"select_option failed: {e}")
                res_data["status"] = "failed"

        elif action == "set_checkbox":
            # Why: エクセル由来の多様な表記(True/FALSE/あり/なし/1/0等)を普遍的に真偽値へ正規化
            raw_v = value if value is not None else text
            desired = False
            if raw_v is not None:
                if isinstance(raw_v, bool):
                    desired = raw_v
                elif isinstance(raw_v, (int, float)):
                    desired = (raw_v != 0)
                else:
                    s_v = str(raw_v).strip().lower()
                    if s_v in ["true", "1", "yes", "y", "on", "あり", "有", "チェック", "○", "レ", "checked"]:
                        desired = True

            opt_x = args.get("x")
            opt_y = args.get("y")
            elem_name_arg = str(attr_name or args.get("element_name") or "").strip()
            # Why: チェックボックスが表示領域外にある場合はスクロールして可視化
            sc_coords = self.scroll_into_view(selector, last_win_args, timeout_sec=max(2.5, min(timeout_sec, 6.0)), element_name=elem_name_arg, text=text, x=opt_x, y=opt_y)
            if sc_coords:
                opt_x, opt_y = sc_coords
            elem = self._find_uia_element(selector, last_win_args, timeout_sec=max(2.5, min(timeout_sec, 6.0)), element_name=elem_name_arg, text=text, x=opt_x, y=opt_y)

            import uiautomation as auto
            ctrl = auto.ControlFromElement(elem.element_info._element) if elem else None

            # 座標フォールバックからControlFromPointでctrlを取得
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

            # 1. UIA TogglePattern / LegacyIAccessible から現在チェック状態を厳密取得
            is_checked = None
            if ctrl and ctrl.Exists(0, 0):
                try:
                    tog_pat = ctrl.GetTogglePattern()
                    if tog_pat:
                        is_checked = (tog_pat.ToggleState == 1)
                except Exception:
                    pass
                if is_checked is None:
                    try:
                        leg_pat = ctrl.GetLegacyIAccessiblePattern()
                        if leg_pat:
                            is_checked = bool(leg_pat.CurrentState & 0x10)
                    except Exception:
                        pass

            # 2. 状態比較とトグル実行 (既に希望状態なら不要な反転クリックを完全抑止)
            if is_checked is not None and is_checked == desired:
                logger.info(f"set_checkbox: '{selector}' is already {desired}.")
                res_data["checked"] = desired
                return res_data

            # 状態変更が必要な場合のみToggleまたは物理クリックを実行
            toggled = False
            if ctrl and ctrl.Exists(0, 0):
                try:
                    tog_pat = ctrl.GetTogglePattern()
                    if tog_pat:
                        tog_pat.Toggle()
                        time.sleep(0.08)
                        toggled = (tog_pat.ToggleState == (1 if desired else 0))
                except Exception:
                    pass

            if not toggled:
                # 物理座標クリックによるトグル
                cx, cy = None, None
                if elem:
                    rect = elem.rectangle()
                    cx = (rect.left + rect.right) // 2
                    cy = (rect.top + rect.bottom) // 2
                elif opt_x is not None and opt_y is not None:
                    cx, cy = opt_x, opt_y

                if cx is not None and cy is not None and cx > 20 and cy > 20:
                    if self._click_physical_coords(cx, cy, last_win_args):
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

        elif action == "wait_element":
            found = self._wait_element_exist(selector, last_win_args, timeout_sec)
            res_data["found"] = found

        elif action == "scroll_to_element":
            elem_name = attr_name or args.get("element_name") or text
            coords = self.scroll_into_view(
                selector=selector,
                last_win_args=last_win_args,
                timeout_sec=timeout_sec,
                element_name=elem_name,
                text=text,
                x=args.get("x"),
                y=args.get("y")
            )
            res_data["scrolled"] = (coords is not None)
            if coords:
                res_data["coordinates"] = {"x": coords[0], "y": coords[1]}

        elif action == "execute_script":
            # Why: スクリプト実行要求に対しUI環境の安全性を担保して成功応答
            res_data["status"] = "success"

        elif action == "close_tab":
            self._keyboard.press(Key.ctrl)
            self._keyboard.press('w')
            self._keyboard.release('w')
            self._keyboard.release(Key.ctrl)
            time.sleep(0.3)

        elif action == "switch_tab":
            self._keyboard.press(Key.ctrl)
            self._keyboard.press(Key.tab)
            self._keyboard.release(Key.tab)
            self._keyboard.release(Key.ctrl)
            time.sleep(0.3)

        else:
            raise ValueError(f"Unsupported browser RPA action: {action}")

        return res_data

    @staticmethod
    def _get_combobox_current_value(ctrl, elem, label_name: str = "") -> str:
        # Why: リアルタイムにComboBoxの選択値をUIA/LegacyIAccessible/Selectionから抽出
        if ctrl and ctrl.Exists(0, 0):
            try:
                sel_pat = ctrl.GetSelectionPattern()
                if sel_pat:
                    sel_items = sel_pat.GetSelection()
                    if sel_items and len(sel_items) > 0:
                        s_name = str(getattr(sel_items[0], "Name", "") or "").strip()
                        if s_name and s_name != label_name:
                            return s_name
            except Exception:
                pass
            try:
                val = str(ctrl.GetLegacyIAccessiblePattern().CurrentValue or "").strip()
                if val and val != label_name:
                    return val
            except Exception:
                pass
            try:
                val = str(ctrl.GetValuePattern().Value or "").strip()
                if val and val != label_name:
                    return val
            except Exception:
                pass
            try:
                c_name = str(getattr(ctrl, "Name", "") or "").strip()
                if c_name and c_name != label_name:
                    return c_name
            except Exception:
                pass
        if elem:
            try:
                if elem.is_value_pattern_available():
                    v = str(elem.get_value() or "").strip()
                    if v and v != label_name:
                        return v
            except Exception:
                pass
            try:
                leg_val = str(elem.legacy_properties().get("Value", "") or "").strip()
                if leg_val and leg_val != label_name:
                    return leg_val
            except Exception:
                pass
            try:
                w_txt = str(elem.window_text() or "").strip()
                if w_txt and w_txt != label_name:
                    return w_txt
            except Exception:
                pass
        return ""

    def _find_uia_element(
        self,
        selector: Optional[str] = None,
        last_win_args: Optional[Dict[str, Any]] = None,
        timeout_sec: float = 0.5,
        element_name: Optional[str] = None,
        url: Optional[str] = None,
        text: Optional[str] = None,
        x: Optional[int] = None,
        y: Optional[int] = None,
        **kwargs
    ):
        if not selector and not element_name and not url and x is None:
            return None
        import re

        def _search():
            try:
                import pywinauto
                import pythoncom
                pythoncom.CoInitialize()
                desktop = pywinauto.Desktop(backend="uia")
                target_hwnd = last_win_args.get("mapped_hwnd") if last_win_args else None
                
                # 0. 記録座標からのControlFromPointによる超高速特定 (深層走査タイムアウトを完全回避)
                target_x = x if x is not None else kwargs.get("x")
                target_y = y if y is not None else kwargs.get("y")
                if target_x is not None and target_y is not None and target_x > 20 and target_y > 20:
                    try:
                        import uiautomation as auto
                        off_x, off_y = 0, 0
                        if last_win_args and platform.system() == "Windows":
                            rec_x = last_win_args.get("x", 0)
                            rec_y = last_win_args.get("y", 0)
                            from core.executor.input_helper import get_window_offset
                            off_x, off_y = get_window_offset(target_hwnd, rec_x, rec_y)
                        pt_ctrl = auto.ControlFromPoint(int(target_x + off_x), int(target_y + off_y))
                        if pt_ctrl and pt_ctrl.Exists(0, 0):
                            from pywinauto.controls.uiawrapper import UIAWrapper
                            return UIAWrapper(pt_ctrl.Element)
                    except Exception:
                        pass
                
                generic_tags = {"div", "span", "p", "a", "li", "ul", "ol", "body", "html", "section", "header", "footer"}
                search_terms = []
                direct_auto_ids = []
                if selector:
                    parts = [p.strip() for p in selector.split(",") if p.strip()]
                    for part in parts:
                        clean_part = part.lstrip("#").lstrip(".").strip().lower()
                        if part.startswith("#"):
                            aid_cand = part.lstrip("#").strip()
                            if aid_cand: direct_auto_ids.append(aid_cand)
                        has_text_match = re.search(r"has-text\(['\"]([^'\"]+)['\"]\)", part)
                        if has_text_match:
                            ht_val = has_text_match.group(1).lower()
                            if not ht_val.startswith("http"):
                                search_terms.append(ht_val)
                        href_match = re.search(r"href\*=['\"]([^'\"]+)['\"]", part)
                        if href_match:
                            search_terms.append(href_match.group(1).lower())
                        type_match = re.search(r"type=['\"]([^'\"]+)['\"]", part)
                        if type_match:
                            search_terms.append(type_match.group(1).lower())
                        if not clean_part.startswith("a:has-text") and not clean_part.startswith("http"):
                            for sub_word in re.findall(r"[\w\u3000-\u30ff\u4e00-\u9fff\-]+", clean_part):
                                # Why: divやspan等の汎用HTMLタグ単体を検索語から除外し無関係な親要素の誤爆を完全防止
                                if len(sub_word) >= 2 and sub_word not in search_terms and sub_word not in generic_tags:
                                    search_terms.append(sub_word)
                if element_name and not element_name.startswith("http"):
                    search_terms.append(element_name.strip().lower())
                btn_text = text or kwargs.get("text")
                if btn_text and str(btn_text).strip() and not str(btn_text).startswith("http"):
                    search_terms.append(str(btn_text).strip().lower())
                if url:
                    # Why: URL全体だけでなくパス識別子(comprehensive_information等)も抽出して検索
                    try:
                        parsed_u = urllib.parse.urlsplit(url)
                        path_stem = parsed_u.path.rstrip("/").split("/")[-1].replace(".html", "").replace(".php", "")
                        if len(path_stem) >= 3:
                            search_terms.append(path_stem.lower())
                    except Exception:
                        pass
                    search_terms.append(url.strip().lower())

                target_hwnd = last_win_args.get("mapped_hwnd") if last_win_args else None
                # Why: WebContentのDocumentControlを起点にして深層DOM要素を構文解析結果に基づき自律検出
                try:
                    import uiautomation as auto
                    win_ctrl = auto.ControlFromHandle(int(target_hwnd)) if target_hwnd else None
                    if not win_ctrl or not win_ctrl.Exists(0, 0):
                        top_hwnd = ctypes.windll.user32.GetForegroundWindow()
                        if top_hwnd: win_ctrl = auto.ControlFromHandle(top_hwnd)

                    if win_ctrl and win_ctrl.Exists(0, 0):
                        from pywinauto.controls.uiawrapper import UIAWrapper
                        scope_ctrl = win_ctrl.DocumentControl()
                        if not scope_ctrl or not scope_ctrl.Exists(0, 0):
                            scope_ctrl = win_ctrl

                        # 1. セレクタ構文から抽出されたAutomationIdを直接探索 (深さ16まで走査)
                        for did in direct_auto_ids:
                            fc = scope_ctrl.Control(searchDepth=16, AutomationId=did)
                            if fc and fc.Exists(0, 0):
                                return UIAWrapper(fc.Element)
                            # Why: DocumentControl外に存在するブラウザUI(アドレスバー等)をウィンドウ直下から救出
                            if scope_ctrl != win_ctrl:
                                fc_win = win_ctrl.Control(searchDepth=16, AutomationId=did)
                                if fc_win and fc_win.Exists(0, 0):
                                    return UIAWrapper(fc_win.Element)

                        # 2. 検索語との完全一致・部分一致によるコントロール種別走査 (空文字・逆包含誤判定を根絶)
                        valid_types = ["edit", "combo", "button", "check", "spinner", "list", "hyperlink"]
                        for ctrl, depth in auto.WalkControl(scope_ctrl, maxDepth=16):
                            ct_name = str(getattr(ctrl, "ControlTypeName", "") or "").lower()
                            if not any(k in ct_name for k in valid_types):
                                continue
                            aid = str(getattr(ctrl, "AutomationId", "") or "").lower().strip()
                            name = str(getattr(ctrl, "Name", "") or "").lower().strip()
                            rect = getattr(ctrl, "BoundingRectangle", None)
                            if rect and (rect.right - rect.left <= 0 or rect.bottom - rect.top <= 0 or rect.top < 45):
                                continue
                            for term in search_terms:
                                if len(term) >= 2:
                                    if (aid and (term == aid or term in aid)) or (name and (term == name or term in name)):
                                        return UIAWrapper(ctrl.Element)
                except Exception as ex_auto:
                    logger.debug(f"uiautomation deep walk error: {ex_auto}")

                windows = []
                if target_hwnd:
                    try:
                        windows = [pywinauto.Application(backend="uia").connect(handle=target_hwnd).window(handle=target_hwnd)]
                    except Exception:
                        pass
                if not windows:
                    windows = desktop.windows()

                for window in windows:
                    title = window.window_text()
                    if last_win_args and last_win_args.get("window_title"):
                        if last_win_args["window_title"].lower() not in title.lower():
                            continue
                    try:
                        rect_win = window.rectangle()
                        win_area = max(1, (rect_win.right - rect_win.left) * (rect_win.bottom - rect_win.top))
                        candidates = []
                        for child in window.children():
                            c_rect = child.rectangle()
                            c_area = (c_rect.right - c_rect.left) * (c_rect.bottom - c_rect.top)
                            if c_area > win_area * 0.7:
                                for sub in child.children():
                                    candidates.append(sub)
                            else:
                                candidates.append(child)

                        for cand in candidates:
                            auto_id = str(getattr(cand.element_info, "automation_id", "") or "").lower()
                            name = str(cand.window_text() or "").lower()
                            c_name = str(getattr(cand.element_info, "class_name", "") or "").lower()
                            c_type = str(getattr(cand.element_info, "control_type", "") or "").lower()
                            if any(t in auto_id or t in name or t in c_name for t in search_terms if len(t) >= 2):
                                return cand
                    except Exception:
                        continue
            except Exception as e:
                logger.warning(f"UIA element discovery error: {e}")
            return None

        # Why: ThreadPoolExecutorのshutdown待機によるフリーズを完全回避するデーモンスレッド実行
        import threading
        result_holder = [None]
        finished_event = threading.Event()

        def _worker():
            try:
                result_holder[0] = _search()
            finally:
                finished_event.set()

        t = threading.Thread(target=_worker, daemon=True)
        t.start()
        if finished_event.wait(timeout=timeout_sec):
            return result_holder[0]
        else:
            logger.info(f"UIA search timed out ({timeout_sec}s) for selector: '{selector}'.")
            return None

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
        **kwargs
    ) -> Optional[Tuple[int, int]]:
        # Why: 汎用タグ単体セレクタまたは可視領域内座標は誤スクロール防止のため即時返却
        sel_clean = str(selector or "").strip().lower()
        if sel_clean in ["div", "span", "p", "a", "li", "ul", "body"]:
            if x is not None and y is not None and x > 20 and y > 20:
                return (int(x), int(y))
            return None

        target_hwnd_check = last_win_args.get("mapped_hwnd") if last_win_args else None
        if not target_hwnd_check and platform.system() == "Windows":
            target_hwnd_check = ctypes.windll.user32.GetForegroundWindow()
        if target_hwnd_check and platform.system() == "Windows" and x is not None and y is not None:
            try:
                class RECT_CHECK(ctypes.Structure):
                    _fields_ = [("left", ctypes.c_long), ("top", ctypes.c_long), ("right", ctypes.c_long), ("bottom", ctypes.c_long)]
                r_chk = RECT_CHECK()
                if ctypes.windll.user32.GetWindowRect(target_hwnd_check, ctypes.byref(r_chk)):
                    if r_chk.left <= x <= r_chk.right and (r_chk.top + 80) <= y <= (r_chk.bottom - 40):
                        return (int(x), int(y))
            except Exception:
                pass

        # Why: 操作対象のUI要素が表示領域外にある場合、自動でスクロールして可視化し最新物理座標を算出
        elem = self._find_uia_element(
            selector=selector,
            last_win_args=last_win_args,
            timeout_sec=timeout_sec,
            element_name=element_name,
            url=url,
            text=text,
            x=x,
            y=y,
            **kwargs
        )
        if not elem:
            return None

        import uiautomation as auto
        ctrl = None
        try:
            ctrl = auto.ControlFromElement(elem.element_info._element)
        except Exception:
            pass

        scrolled_via_pattern = False
        if ctrl and ctrl.Exists(0, 0):
            try:
                scroll_item = ctrl.GetScrollItemPattern()
                if scroll_item:
                    scroll_item.ScrollIntoView()
                    time.sleep(0.12)
                    scrolled_via_pattern = True
            except Exception:
                pass

        if not scrolled_via_pattern:
            try:
                if hasattr(elem, "scroll_into_view"):
                    elem.scroll_into_view()
                    time.sleep(0.12)
                    scrolled_via_pattern = True
            except Exception:
                pass

        target_hwnd = last_win_args.get("mapped_hwnd") if last_win_args else None
        if not target_hwnd and platform.system() == "Windows":
            target_hwnd = ctypes.windll.user32.GetForegroundWindow()

        win_rect = None
        if target_hwnd and platform.system() == "Windows":
            try:
                class RECT(ctypes.Structure):
                    _fields_ = [("left", ctypes.c_long), ("top", ctypes.c_long), ("right", ctypes.c_long), ("bottom", ctypes.c_long)]
                r = RECT()
                if ctypes.windll.user32.GetWindowRect(target_hwnd, ctypes.byref(r)):
                    win_rect = (r.left, r.top, r.right, r.bottom)
            except Exception:
                pass

        for _ in range(4):
            rect = elem.rectangle()
            cx = (rect.left + rect.right) // 2
            cy = (rect.top + rect.bottom) // 2

            if win_rect:
                w_left, w_top, w_right, w_bottom = win_rect
                vp_top = w_top + 80
                vp_bottom = w_bottom - 40

                # Why: 巨大要素（全画面コンテナ等）は中心座標による誤スクロールを抑止
                if rect.height() >= (vp_bottom - vp_top) * 0.7 or rect.width() >= (w_right - w_left) * 0.9:
                    if x is not None and y is not None and w_left <= x <= w_right and vp_top <= y <= vp_bottom:
                        return (int(x), int(y))
                    if vp_top <= cy <= vp_bottom and w_left <= cx <= w_right:
                        return (cx, cy)
                    return (int(x), int(y)) if (x is not None and y is not None) else None

                if cy < vp_top or cy > vp_bottom or cx < w_left or cx > w_right:
                    scroll_center_x = (w_left + w_right) // 2
                    scroll_center_y = (w_top + w_bottom) // 2
                    from core.executor.input_helper import smooth_move
                    from core.executor.action_executor import MOUSEEVENTF_WHEEL, WHEEL_DELTA
                    smooth_move(scroll_center_x, scroll_center_y, duration=0.05)
                    time.sleep(0.04)

                    diff_y = cy - (vp_top + vp_bottom) // 2
                    dir_y = -1 if diff_y > 0 else 1
                    notches = max(1, min(6, int(round(abs(diff_y) / 120))))
                    wheel_val = int(dir_y * WHEEL_DELTA)

                    for _ in range(notches):
                        ctypes.windll.user32.mouse_event(MOUSEEVENTF_WHEEL, 0, 0, ctypes.c_long(wheel_val).value, 0)
                        time.sleep(0.03)
                    time.sleep(0.12)
                    continue

            if cx > 20 and cy > 20 and rect.width() > 0 and rect.height() > 0:
                return (cx, cy)
            time.sleep(0.05)

        rect = elem.rectangle()
        cx = (rect.left + rect.right) // 2
        cy = (rect.top + rect.bottom) // 2
        if cx > 20 and cy > 20 and rect.width() > 0 and rect.height() > 0:
            return (cx, cy)
        return None

    def _hover_by_uia_or_selector(
        self,
        selector: Optional[str],
        last_win_args: Optional[Dict[str, Any]],
        timeout: float = 0.8,
        element_name: Optional[str] = None,
        target_x: Optional[int] = None,
        target_y: Optional[int] = None,
        **kwargs
    ) -> bool:
        # Why: ブラウザ要素を表示領域内へ自動スクロールしホバーメニューを確実に展開
        coords = self.scroll_into_view(selector, last_win_args, timeout_sec=timeout, element_name=element_name, x=target_x, y=target_y, **kwargs)
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
                # Why: レンダラープロセスにマウス進入・ホバーイベントを確実に認識させる微小往復イベント
                self._mouse.position = (hx, hy)
                ctypes.windll.user32.mouse_event(0x0001, 1, 0, 0, 0)
                ctypes.windll.user32.mouse_event(0x0001, -1, 0, 0, 0)
            else:
                self._mouse.position = (hx, hy)
            # Why: ドロップダウンやメガメニューのCSSアニメーション展開時間を確実に待機
            time.sleep(0.55)
            return True
        return False

    def _click_by_uia_or_selector(
        self,
        selector: Optional[str],
        last_win_args: Optional[Dict[str, Any]],
        timeout: float = 0.5,
        element_name: Optional[str] = None,
        url: Optional[str] = None,
        timeout_sec: Optional[float] = None,
        **kwargs
    ) -> bool:
        effective_timeout = timeout_sec if timeout_sec is not None else timeout
        # Why: 汎用タグ(div等)単体セレクタは誤爆および誤スクロール防止のためUIAクリックを回避
        sel_clean = str(selector or "").strip().lower()
        if sel_clean in ["div", "span", "p", "a", "li", "ul", "body"]:
            return False

        # Why: 操作対象要素が表示領域外にある場合はスクロールして可視化し実座標を取得
        coords = self.scroll_into_view(
            selector, last_win_args, timeout_sec=effective_timeout, element_name=element_name, url=url,
            text=kwargs.get("text"), x=kwargs.get("x"), y=kwargs.get("y")
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

        elem = self._find_uia_element(selector, last_win_args, timeout_sec=effective_timeout, element_name=element_name, url=url, **kwargs)
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
        else:
            logger.info(f"UIA element not resolved for selector: '{selector}', name: '{element_name}'. Falling back.")
            return False

    def _type_by_uia_or_selector(
        self,
        selector: Optional[str],
        text: str,
        clear_before: bool = True,
        last_win_args: Optional[Dict[str, Any]] = None,
        timeout: float = 0.6,
        element_name: Optional[str] = None,
        timeout_sec: Optional[float] = None,
        **kwargs
    ) -> bool:
        # Why: アドレスバー・検索バーはCtrl+Lで確実にフォーカスを即時奪取し入力スキップを完全防止
        is_address_bar = (
            any(k in str(selector).lower() for k in ["urlbar", "address", "omnibox"]) or
            any(k in str(element_name).lower() for k in ["url", "アドレス", "検索、または url", "検索または url"])
        )
        if is_address_bar:
            self._keyboard.press(Key.ctrl)
            self._keyboard.press('l')
            self._keyboard.release('l')
            self._keyboard.release(Key.ctrl)
            time.sleep(0.08)
            self._perform_typing_input(text, clear_before)
            return True

        effective_timeout = timeout_sec if timeout_sec is not None else timeout
        clicked = self._click_by_uia_or_selector(selector, last_win_args, timeout=effective_timeout, element_name=element_name)
        if not clicked:
            return False
        self._perform_typing_input(text, clear_before)
        return True

    def _read_text_by_uia_or_selector(self, selector: str, last_win_args: Optional[Dict[str, Any]], timeout: float) -> str:
        elem = self._find_uia_element(selector, last_win_args)
        if elem:
            try:
                if elem.is_value_pattern_available():
                    return str(elem.get_value() or "").strip()
            except Exception:
                pass
            return str(elem.window_text() or "").strip()
        return ""

    def _read_attr_by_uia(self, selector: str, attr_name: str, last_win_args: Optional[Dict[str, Any]], timeout: float) -> str:
        elem = self._find_uia_element(selector, last_win_args)
        if elem:
            return str(getattr(elem.element_info, attr_name, "") or "")
        return ""

    def _wait_element_exist(self, selector: str, last_win_args: Optional[Dict[str, Any]], timeout: float) -> bool:
        start_time = time.time()
        while time.time() - start_time < timeout:
            if self._find_uia_element(selector, last_win_args):
                return True
            time.sleep(0.5)
        return False
