"""Module: @role: ブラウザ操作におけるUIA要素探索、スクロール可視化、テキスト/属性取得および要素待機を担当する。"""

import ctypes
import logging
import platform
import re
import threading
import time
import urllib.parse
from typing import Any, Dict, Optional, Tuple

logger = logging.getLogger(__name__)

GENERIC_HTML_TAGS = {"div", "span", "p", "a", "li", "ul", "ol", "body", "html", "section", "header", "footer"}
SIMPLE_GENERIC_TAGS = {"div", "span", "p", "a", "li", "ul", "body"}


def get_combobox_current_value(ctrl: Any, elem: Any, label_name: str = "") -> str:
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


def find_uia_element(
    selector: Optional[str] = None,
    last_win_args: Optional[Dict[str, Any]] = None,
    timeout_sec: float = 0.5,
    element_name: Optional[str] = None,
    url: Optional[str] = None,
    text: Optional[str] = None,
    x: Optional[int] = None,
    y: Optional[int] = None,
    **kwargs: Any,
) -> Any:
    if not selector and not element_name and not url and x is None:
        return None

    def _search() -> Any:
        try:
            import pythoncom
            import pywinauto

            pythoncom.CoInitialize()
            desktop = pywinauto.Desktop(backend="uia")
            target_hwnd = last_win_args.get("mapped_hwnd") if last_win_args else None

            # Why: 記録座標からのControlFromPointで深層走査タイムアウトを回避し高速特定
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

            search_terms = []
            direct_auto_ids = []
            if selector:
                parts = [p.strip() for p in selector.split(",") if p.strip()]
                for part in parts:
                    clean_part = part.lstrip("#").lstrip(".").strip().lower()
                    if part.startswith("#"):
                        aid_cand = part.lstrip("#").strip()
                        if aid_cand:
                            direct_auto_ids.append(aid_cand)
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
                            # Why: 汎用HTMLタグ単体を検索語から除外し無関係な親要素誤爆を完全防止
                            if len(sub_word) >= 2 and sub_word not in search_terms and sub_word not in GENERIC_HTML_TAGS:
                                search_terms.append(sub_word)
            if element_name and not element_name.startswith("http"):
                search_terms.append(element_name.strip().lower())
            btn_text = text or kwargs.get("text")
            if btn_text and str(btn_text).strip() and not str(btn_text).startswith("http"):
                search_terms.append(str(btn_text).strip().lower())
            if url:
                # Why: URL全体だけでなくパス識別子も抽出して検索
                try:
                    parsed_u = urllib.parse.urlsplit(url)
                    path_stem = parsed_u.path.rstrip("/").split("/")[-1].replace(".html", "").replace(".php", "")
                    if len(path_stem) >= 3:
                        search_terms.append(path_stem.lower())
                except Exception:
                    pass
                search_terms.append(url.strip().lower())

            # Why: WebContentのDocumentControlを起点にして深層DOM要素を構文解析結果に基づき自律検出
            try:
                import uiautomation as auto

                win_ctrl = auto.ControlFromHandle(int(target_hwnd)) if target_hwnd else None
                if not win_ctrl or not win_ctrl.Exists(0, 0):
                    top_hwnd = ctypes.windll.user32.GetForegroundWindow()
                    if top_hwnd:
                        win_ctrl = auto.ControlFromHandle(top_hwnd)

                if win_ctrl and win_ctrl.Exists(0, 0):
                    from pywinauto.controls.uiawrapper import UIAWrapper

                    scope_ctrl = win_ctrl.DocumentControl()
                    if not scope_ctrl or not scope_ctrl.Exists(0, 0):
                        scope_ctrl = win_ctrl

                    for did in direct_auto_ids:
                        fc = scope_ctrl.Control(searchDepth=16, AutomationId=did)
                        if fc and fc.Exists(0, 0):
                            return UIAWrapper(fc.Element)
                        # Why: DocumentControl外のブラウザUI(アドレスバー等)をウィンドウ直下から救出
                        if scope_ctrl != win_ctrl:
                            fc_win = win_ctrl.Control(searchDepth=16, AutomationId=did)
                            if fc_win and fc_win.Exists(0, 0):
                                return UIAWrapper(fc_win.Element)

                    valid_types = ["edit", "combo", "button", "check", "spinner", "list", "hyperlink"]
                    for ctrl, _depth in auto.WalkControl(scope_ctrl, maxDepth=16):
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
                        if any(t in auto_id or t in name or t in c_name for t in search_terms if len(t) >= 2):
                            return cand
                except Exception:
                    continue
        except Exception as e:
            logger.warning(f"UIA element discovery error: {e}")
        return None

    # Why: ThreadPoolExecutorのshutdown待機によるフリーズを回避するデーモンスレッド実行
    result_holder = [None]
    finished_event = threading.Event()

    def _worker() -> None:
        try:
            result_holder[0] = _search()
        finally:
            finished_event.set()

    t = threading.Thread(target=_worker, daemon=True)
    t.start()
    if finished_event.wait(timeout=timeout_sec):
        return result_holder[0]
    logger.info(f"UIA search timed out ({timeout_sec}s) for selector: '{selector}'.")
    return None


def scroll_into_view(
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
    # Why: 汎用タグ単体セレクタまたは可視領域内座標は誤スクロール防止のため即時返却
    sel_clean = str(selector or "").strip().lower()
    if sel_clean in SIMPLE_GENERIC_TAGS:
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

    elem = find_uia_element(
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

            # Why: 巨大要素(全画面コンテナ等)は中心座標による誤スクロールを抑止
            if rect.height() >= (vp_bottom - vp_top) * 0.7 or rect.width() >= (w_right - w_left) * 0.9:
                if x is not None and y is not None and w_left <= x <= w_right and vp_top <= y <= vp_bottom:
                    return (int(x), int(y))
                if vp_top <= cy <= vp_bottom and w_left <= cx <= w_right:
                    return (cx, cy)
                return (int(x), int(y)) if (x is not None and y is not None) else None

            if cy < vp_top or cy > vp_bottom or cx < w_left or cx > w_right:
                scroll_center_x = (w_left + w_right) // 2
                scroll_center_y = (w_top + w_bottom) // 2
                from core.executor.action_executor import MOUSEEVENTF_WHEEL, WHEEL_DELTA
                from core.executor.input_helper import smooth_move

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


def read_text_by_uia_or_selector(selector: str, last_win_args: Optional[Dict[str, Any]], timeout: float) -> str:
    elem = find_uia_element(selector, last_win_args, timeout_sec=timeout)
    if elem:
        try:
            if elem.is_value_pattern_available():
                return str(elem.get_value() or "").strip()
        except Exception:
            pass
        return str(elem.window_text() or "").strip()
    return ""


def read_attr_by_uia(selector: str, attr_name: str, last_win_args: Optional[Dict[str, Any]], timeout: float) -> str:
    elem = find_uia_element(selector, last_win_args, timeout_sec=timeout)
    if elem:
        return str(getattr(elem.element_info, attr_name, "") or "")
    return ""


def wait_element_exist(selector: str, last_win_args: Optional[Dict[str, Any]], timeout: float) -> bool:
    start_time = time.time()
    while time.time() - start_time < timeout:
        if find_uia_element(selector, last_win_args):
            return True
        time.sleep(0.5)
    return False
