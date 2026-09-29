"""Module: @role: ブラウザ操作（CDP / Playwright / UIAハイブリッド）の実行制御とDOMセレクタ探索を担当する。"""

from typing import Any, Dict, Optional, Tuple
import json
import time
import logging
import platform
import ctypes
import urllib.request
from pynput.keyboard import Controller as KeyboardController, Key
from core.executor.os_env_controller import set_system_cursor

logger = logging.getLogger(__name__)

class BrowserController:
    _instance: Optional["BrowserController"] = None
    _cdp_port: int = 9222

    def __init__(self, cdp_port: int = 9222) -> None:
        self._cdp_port = cdp_port
        self._keyboard = KeyboardController()

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
                rec_x = last_win_args.get("x", 0)
                rec_y = last_win_args.get("y", 0)
                from core.executor.runner import _get_window_offset
                off_x, off_y = _get_window_offset(target_hwnd, rec_x, rec_y)
            except Exception:
                pass

        actual_x = int(x + off_x)
        actual_y = int(y + off_y)
        if platform.system() == "Windows":
            ctypes.windll.user32.SetCursorPos(actual_x, actual_y)
            ctypes.windll.user32.mouse_event(1, 0, 0, 0, 0)
            time.sleep(0.04)
            set_system_cursor("run_click")
            time.sleep(0.03)
            ctypes.windll.user32.mouse_event(2, 0, 0, 0, 0)
            ctypes.windll.user32.mouse_event(4, 0, 0, 0, 0)
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

        if _set_clipboard_text(norm_text):
            self._keyboard.press(Key.ctrl)
            self._keyboard.press('v')
            self._keyboard.release('v')
            self._keyboard.release(Key.ctrl)
            time.sleep(0.04)
            # Why: React/Vue等の仮想DOM inputイベントを右矢印キーで強制発火
            self._keyboard.press(Key.right)
            self._keyboard.release(Key.right)
            time.sleep(0.06)
        else:
            for char in norm_text:
                self._keyboard.type(char)
                time.sleep(0.02)
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
            clicked = self._click_by_uia_or_selector(selector, last_win_args, timeout=min(timeout_sec, 0.6), element_name=attr_name or args.get("element_name"), url=url)
            if not clicked:
                x = args.get("x")
                y = args.get("y")
                if self._click_physical_coords(x, y, last_win_args):
                    logger.info(f"[{workflow_id}] Selector click fell back to physical click at ({x}, {y})")
                    res_data["status"] = "fallback_click_succeeded"
                else:
                    logger.warning(f"[{workflow_id}] Click failed for selector '{selector}' and no valid fallback coordinates.")
                    res_data["status"] = "click_failed"

        elif action == "type_text":
            elem_name = attr_name or args.get("element_name")
            typed = self._type_by_uia_or_selector(selector, text, clear_before, last_win_args, timeout=min(timeout_sec, 0.6), element_name=elem_name)
            if not typed:
                x = args.get("x")
                y = args.get("y")
                # Why: UIA未検出時は物理座標をクリックして入力欄フォーカスを取り確実に入力
                self._click_physical_coords(x, y, last_win_args)
                self._perform_typing_input(text, clear_before)
                res_data["status"] = "fallback_typing_succeeded"

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
            target_val = str(value if value is not None else text)
            elem = self._find_uia_element(selector, last_win_args, timeout_sec=min(timeout_sec, 0.6), element_name=attr_name or args.get("element_name"))
            if elem:
                try:
                    elem.click_input()
                    time.sleep(0.1)
                    # Why: コンボボックス展開後に選択肢テキストを直接入力または確定
                    self._keyboard.type(target_val)
                    time.sleep(0.05)
                    self._keyboard.press(Key.enter)
                    self._keyboard.release(Key.enter)
                    res_data["selected"] = target_val
                except Exception as e:
                    logger.warning(f"select_option failed: {e}")
                    res_data["status"] = "failed"
            else:
                x = args.get("x")
                y = args.get("y")
                if self._click_physical_coords(x, y, last_win_args):
                    time.sleep(0.1)
                    self._keyboard.type(target_val)
                    time.sleep(0.05)
                    self._keyboard.press(Key.enter)
                    self._keyboard.release(Key.enter)
                    res_data["selected"] = target_val
                else:
                    res_data["status"] = "element_not_found"

        elif action == "set_checkbox":
            desired = True if value is None else (value in [True, "True", "true", 1, "1"])
            elem = self._find_uia_element(selector, last_win_args, timeout_sec=min(timeout_sec, 0.6), element_name=attr_name or args.get("element_name"))
            if elem:
                try:
                    toggle_state = getattr(elem.element_info, "toggle_state", None)
                    current_checked = toggle_state == 1 if toggle_state is not None else False
                    if current_checked != desired:
                        elem.click_input()
                        time.sleep(0.1)
                    res_data["checked"] = desired
                except Exception as e:
                    logger.warning(f"set_checkbox failed: {e}")
                    res_data["status"] = "failed"
            else:
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

    def _find_uia_element(
        self,
        selector: Optional[str] = None,
        last_win_args: Optional[Dict[str, Any]] = None,
        timeout_sec: float = 0.5,
        element_name: Optional[str] = None,
        url: Optional[str] = None
    ):
        if not selector and not element_name and not url:
            return None
        import concurrent.futures
        import re

        def _search():
            try:
                import pywinauto
                import pythoncom
                pythoncom.CoInitialize()
                desktop = pywinauto.Desktop(backend="uia")
                
                search_terms = []
                if selector:
                    clean_sel = selector.lstrip("#").lstrip(".").strip().lower()
                    has_text_match = re.search(r"has-text\(['\"]([^'\"]+)['\"]\)", selector)
                    if has_text_match:
                        ht_val = has_text_match.group(1).lower()
                        # Why: URLドメイン等ではなく有為なテキストの場合のみ検索語へ追加
                        if not ht_val.startswith("http"):
                            search_terms.append(ht_val)
                    href_match = re.search(r"href\*=['\"]([^'\"]+)['\"]", selector)
                    if href_match:
                        search_terms.append(href_match.group(1).lower())
                    if not clean_sel.startswith("a:has-text") and not clean_sel.startswith("http"):
                        search_terms.append(clean_sel)
                if element_name and not element_name.startswith("http"):
                    search_terms.append(element_name.strip().lower())
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
                        # Why: descendants()の全走査はブラウザCOMを永久ハングさせるため直下2階層に限定
                        for child in window.children():
                            auto_id = str(getattr(child.element_info, "automation_id", "") or "").lower()
                            name = str(child.window_text() or "").lower()
                            c_name = str(getattr(child.element_info, "class_name", "") or "").lower()
                            if any(t in auto_id or t in name or t in c_name for t in search_terms):
                                return child
                            for sub in child.children():
                                s_auto = str(getattr(sub.element_info, "automation_id", "") or "").lower()
                                s_name = str(sub.window_text() or "").lower()
                                if any(t in s_auto or t in s_name for t in search_terms):
                                    return sub
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
        elem = self._find_uia_element(selector, last_win_args, timeout_sec=min(effective_timeout, 0.5), element_name=element_name, url=url)
        if elem:
            rect = elem.rectangle()
            cx = (rect.left + rect.right) // 2
            cy = (rect.top + rect.bottom) // 2
            if platform.system() == "Windows":
                ctypes.windll.user32.SetCursorPos(cx, cy)
                ctypes.windll.user32.mouse_event(1, 0, 0, 0, 0)
                time.sleep(0.04)
                set_system_cursor("run_click")
                time.sleep(0.03)
                ctypes.windll.user32.mouse_event(2, 0, 0, 0, 0)
                ctypes.windll.user32.mouse_event(4, 0, 0, 0, 0)
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
