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
        # Why: runner側の包括的エイリアス・正規化解決エンジンへ完全統一
        if not text:
            return ""
        from core.executor.runner import _resolve_variables
        return str(_resolve_variables(text, variables))

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
                from core.executor.runner import _get_window_offset
                off_x, off_y = _get_window_offset(target_hwnd, rec_x, rec_y)
            except Exception:
                pass

        actual_x = int(x + off_x)
        actual_y = int(y + off_y)
        from core.executor.runner import _smooth_move
        _smooth_move(actual_x, actual_y)
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
        from core.executor.runner import _set_clipboard_text
        norm_text = normalize_text_width(text)
        # Why: クリップボード貼付直前はIMEを半角OFFにしてローマ字誤変換(ろｗ等)を完全根絶
        ensure_ime_state(target_state=False, timeout=0.4)
        time.sleep(0.04)

        # Why: クリップボード貼付とSendInput(Unicode)ダイレクト入力の二重化で確実に入力
        from core.executor.runner import _send_unicode_string
        clip_ok = _set_clipboard_text(norm_text)
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
            _send_unicode_string(norm_text)
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
                from core.executor.runner import _set_clipboard_text
                if _set_clipboard_text(norm_url):
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
                else:
                    logger.warning(f"[{workflow_id}] Click failed for selector '{selector}' and no valid fallback coordinates.")
                    res_data["status"] = "click_failed"

        elif action == "type_text":
            elem_name = attr_name or args.get("element_name")
            x = args.get("x")
            y = args.get("y")
            typed = False

            # 1. UIA探索による高精度タイピング
            if selector or elem_name:
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
                            from core.executor.runner import _get_window_offset
                            off_x, off_y = _get_window_offset(target_hwnd, rec_x, rec_y)
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
            desired = True if value is None else (value in [True, "True", "true", 1, "1"])
            elem = self._find_uia_element(selector, last_win_args, timeout_sec=max(2.5, min(timeout_sec, 6.0)), element_name=attr_name or args.get("element_name") or text)
            toggled = False
            if elem:
                try:
                    is_checked = False
                    if hasattr(elem, "is_toggle_pattern_available") and elem.is_toggle_pattern_available():
                        is_checked = elem.get_toggle_state() == 1
                    else:
                        leg_state = elem.legacy_properties().get("State", 0)
                        is_checked = bool(isinstance(leg_state, int) and (leg_state & 0x10))
                    if is_checked != desired:
                        rect = elem.rectangle()
                        cx = (rect.left + rect.right) // 2
                        cy = (rect.top + rect.bottom) // 2
                        if cx > 20 and cy > 20 and self._click_physical_coords(cx, cy, last_win_args):
                            toggled = True
                    else:
                        toggled = True
                    res_data["checked"] = desired
                except Exception as e:
                    logger.warning(f"set_checkbox UIA evaluation failed: {e}")
            if not toggled:
                x = args.get("x")
                y = args.get("y")
                if self._click_physical_coords(x, y, last_win_args):
                    res_data["checked"] = desired
                else:
                    res_data["status"] = "element_not_found"

        elif action == "wait_element":
            found = self._wait_element_exist(selector, last_win_args, timeout_sec)
            res_data["found"] = found

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
                            from core.executor.runner import _get_window_offset
                            off_x, off_y = _get_window_offset(target_hwnd, rec_x, rec_y)
                        pt_ctrl = auto.ControlFromPoint(int(target_x + off_x), int(target_y + off_y))
                        if pt_ctrl and pt_ctrl.Exists(0, 0):
                            from pywinauto.controls.uiawrapper import UIAWrapper
                            return UIAWrapper(pt_ctrl.Element)
                    except Exception:
                        pass
                
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
                                if len(sub_word) >= 2 and sub_word not in search_terms:
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
        elem = self._find_uia_element(selector, last_win_args, timeout_sec=effective_timeout, element_name=element_name, url=url, **kwargs)
        if elem:
            rect = elem.rectangle()
            cx = (rect.left + rect.right) // 2
            cy = (rect.top + rect.bottom) // 2
            # Why: 画面端・不可視座標への誤クリックを防ぎ物理座標フォールバックへ委譲
            if cx <= 20 or cy <= 20 or rect.width() <= 0 or rect.height() <= 0:
                logger.warning(f"UIA element has invalid rectangle ({cx}, {cy}). Falling back to physical coords.")
                return False
            if platform.system() == "Windows":
                from core.executor.runner import _smooth_move
                _smooth_move(cx, cy)
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
