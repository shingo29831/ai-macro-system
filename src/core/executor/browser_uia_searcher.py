"""Module: @role: ブラウザ操作におけるCSSセレクタ/URL解析と3段階UIA要素探索（座標・DocumentControl・pywinauto）を担当する。"""

import ctypes
import logging
import platform
import re
import threading
import time
import urllib.parse
from typing import Any, Dict, List, Optional, Tuple

from core.shared.window_utils import find_active_browser_document, get_browser_family

logger = logging.getLogger(__name__)

GENERIC_HTML_TAGS = {"div", "span", "p", "a", "li", "ul", "ol", "body", "html", "section", "header", "footer"}
_INTERACTIVE_CONTROL_TYPES = (
    "edit", "combo", "button", "check", "spinner", "list",
    "hyperlink", "menuitem", "tabitem", "listitem", "radio",
)
_FALLBACK_CONTROL_TYPES = ("text", "image", "group", "custom")


def _is_valid_doc_candidate(ctrl: Any, win_area: int, allow_fallback: bool = False) -> bool:
    if getattr(ctrl, "IsOffscreen", False):
        return False
    rect = getattr(ctrl, "BoundingRectangle", None)
    if not rect or (rect.right - rect.left) <= 0 or (rect.bottom - rect.top) <= 0 or rect.top < 45:
        return False
    ct_name = str(getattr(ctrl, "ControlTypeName", "") or "").lower()
    is_interactive = any(k in ct_name for k in _INTERACTIVE_CONTROL_TYPES)
    is_fallback = any(k in ct_name for k in _FALLBACK_CONTROL_TYPES)
    if not is_interactive and not (allow_fallback and is_fallback):
        return False
    if win_area > 0:
        elem_area = (rect.right - rect.left) * (rect.bottom - rect.top)
        if elem_area > win_area * 0.5:
            return False
        # Why: テキストやグループラッパー等の非対話コンテナが広範囲一致で誤クリックされるのを防止
        if not is_interactive and elem_area > win_area * 0.15:
            return False
    return True


def build_search_criteria(
    selector: Optional[str],
    element_name: Optional[str],
    url: Optional[str],
    btn_text: Optional[str],
) -> Tuple[List[str], List[str]]:
    """セレクタ・要素名・URL・テキストからUIA探索用のキーワード群とAutomationId候補を構築する。"""
    search_terms: List[str] = []
    direct_auto_ids: List[str] = []

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

    return search_terms, direct_auto_ids


def _find_by_point(
    target_x: Optional[int],
    target_y: Optional[int],
    target_hwnd: Optional[int],
    last_win_args: Optional[Dict[str, Any]],
    direct_auto_ids: Optional[List[str]] = None,
    search_terms: Optional[List[str]] = None,
) -> Any:
    if not (target_x is not None and target_y is not None and target_x > 20 and target_y > 20):
        return None
    try:
        import uiautomation as auto
        from pywinauto.controls.uiawrapper import UIAWrapper

        off_x, off_y = 0, 0
        if last_win_args and platform.system() == "Windows":
            from core.executor.input_helper import get_window_offset

            off_x, off_y = get_window_offset(target_hwnd, last_win_args.get("x", 0), last_win_args.get("y", 0))
        pt_ctrl = auto.ControlFromPoint(int(target_x + off_x), int(target_y + off_y))
        if pt_ctrl and pt_ctrl.Exists(0, 0):
            # Why: スクロールズレ時に座標上の無関係な別要素を誤返却しないようID/名称一致を検証
            if direct_auto_ids or search_terms:
                aid = str(getattr(pt_ctrl, "AutomationId", "") or "").strip().lower()
                name = str(getattr(pt_ctrl, "Name", "") or "").strip().lower()
                id_match = any(aid == d.lower() for d in (direct_auto_ids or []))
                term_match = any(
                    len(t) >= 2 and ((aid and t in aid) or (name and t in name))
                    for t in (search_terms or [])
                )
                if not (id_match or term_match):
                    return None
            return UIAWrapper(pt_ctrl.Element)
    except Exception:
        pass
    return None


def _find_in_document_tree(
    target_hwnd: Optional[int],
    direct_auto_ids: List[str],
    search_terms: List[str],
    window_title: str = "",
) -> Any:
    try:
        import uiautomation as auto
        from pywinauto.controls.uiawrapper import UIAWrapper

        win_ctrl = auto.ControlFromHandle(int(target_hwnd)) if target_hwnd else None
        if not win_ctrl or not win_ctrl.Exists(0, 0):
            top_hwnd = ctypes.windll.user32.GetForegroundWindow()
            if top_hwnd:
                win_ctrl = auto.ControlFromHandle(top_hwnd)

        if not (win_ctrl and win_ctrl.Exists(0, 0)):
            return None

        # Why: 複数タブ環境で先頭バックグラウンドタブではなくアクティブタブのDocumentControlを探索
        scope_ctrl = find_active_browser_document(win_ctrl, window_title=window_title)
        if not scope_ctrl or not scope_ctrl.Exists(0, 0):
            scope_ctrl = win_ctrl

        win_rect = getattr(win_ctrl, "BoundingRectangle", None)
        win_area = (
            max(1, (win_rect.right - win_rect.left) * (win_rect.bottom - win_rect.top))
            if win_rect
            else 0
        )

        for did in direct_auto_ids:
            fc = scope_ctrl.Control(searchDepth=32, AutomationId=did)
            if fc and fc.Exists(0, 0) and _is_valid_doc_candidate(fc, win_area, allow_fallback=True):
                return UIAWrapper(fc.Element)
            # Why: DocumentControl外のブラウザUI(アドレスバー等)をウィンドウ直下から救出
            if scope_ctrl != win_ctrl:
                fc_win = win_ctrl.Control(searchDepth=24, AutomationId=did)
                if fc_win and fc_win.Exists(0, 0):
                    return UIAWrapper(fc_win.Element)

        # Why: C++ネイティブ探索では対話型要素のみ即返却し非対話テキスト・親コンテナ誤爆を遮断
        fallback_native = None
        subname_interactive = None
        for term in search_terms:
            if len(term) < 2 or term.startswith("http"):
                continue
            exact_ctrl = scope_ctrl.Control(searchDepth=32, Name=term)
            if exact_ctrl and exact_ctrl.Exists(0, 0):
                if _is_valid_doc_candidate(exact_ctrl, win_area, allow_fallback=False):
                    return UIAWrapper(exact_ctrl.Element)
                if fallback_native is None and _is_valid_doc_candidate(exact_ctrl, win_area, allow_fallback=True):
                    fallback_native = exact_ctrl
            if len(term) >= 3 and subname_interactive is None:
                sub_ctrl = scope_ctrl.Control(searchDepth=32, SubName=term)
                if sub_ctrl and sub_ctrl.Exists(0, 0) and _is_valid_doc_candidate(sub_ctrl, win_area, allow_fallback=False):
                    subname_interactive = sub_ctrl

        if subname_interactive is not None:
            return UIAWrapper(subname_interactive.Element)

        best_interactive = None
        best_fallback = fallback_native
        walk_start = time.monotonic()
        for node_idx, (ctrl, _depth) in enumerate(auto.WalkControl(scope_ctrl, maxDepth=32)):
            if node_idx >= 2500 or (time.monotonic() - walk_start) > 1.8:
                break
            is_interactive = _is_valid_doc_candidate(ctrl, win_area, allow_fallback=False)
            if not is_interactive and not _is_valid_doc_candidate(ctrl, win_area, allow_fallback=True):
                continue
            aid = str(getattr(ctrl, "AutomationId", "") or "").lower().strip()
            name = str(getattr(ctrl, "Name", "") or "").lower().strip()
            for term in search_terms:
                if len(term) < 2:
                    continue
                exact_match = (aid and term == aid) or (name and term == name)
                partial_match = (aid and term in aid) or (name and term in name)
                if not (exact_match or partial_match):
                    continue
                if is_interactive:
                    if exact_match:
                        return UIAWrapper(ctrl.Element)
                    if best_interactive is None:
                        best_interactive = ctrl
                elif best_fallback is None:
                    best_fallback = ctrl

        chosen = best_interactive or best_fallback
        if chosen is not None:
            return UIAWrapper(chosen.Element)
    except Exception as ex_auto:
        logger.debug(f"uiautomation deep walk error: {ex_auto}")
    return None


def _find_in_pywinauto_windows(
    desktop: Any,
    target_hwnd: Optional[int],
    last_win_args: Optional[Dict[str, Any]],
    search_terms: List[str],
) -> Any:
    import pywinauto

    windows = []
    if target_hwnd:
        try:
            windows = [pywinauto.Application(backend="uia").connect(handle=target_hwnd).window(handle=target_hwnd)]
        except Exception:
            pass
    if not windows:
        windows = desktop.windows()

    expected_title = (last_win_args.get("window_title") or "").strip() if last_win_args else ""
    expected_family = get_browser_family(expected_title) if expected_title else ""

    for window in windows:
        title = window.window_text()
        if expected_title and expected_title.lower() not in title.lower():
            # Why: タブ遷移後のタイトル変化を許容しつつ単語境界ベースの同一ブラウザ種別に限定
            if not (expected_family and get_browser_family(title) == expected_family):
                continue
        try:
            rect_win = window.rectangle()
            win_area = max(1, (rect_win.right - rect_win.left) * (rect_win.bottom - rect_win.top))
            candidates = []
            for child in window.children():
                c_rect = child.rectangle()
                c_area = (c_rect.right - c_rect.left) * (c_rect.bottom - c_rect.top)
                if c_area > win_area * 0.7:
                    candidates.extend(child.children())
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
    return None


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
    """タイムアウト制御付きでブラウザUI要素を3段階UIA探索により特定する。"""
    if not selector and not element_name and not url and x is None:
        return None

    def _search() -> Any:
        try:
            import pythoncom
            import pywinauto

            pythoncom.CoInitialize()
            desktop = pywinauto.Desktop(backend="uia")
            target_hwnd = last_win_args.get("mapped_hwnd") if last_win_args else None

            search_terms, direct_auto_ids = build_search_criteria(
                selector, element_name, url, text or kwargs.get("text")
            )

            # Why: 記録座標からのControlFromPointで深層走査タイムアウトを回避しつつID/名称一致を保証
            target_x = x if x is not None else kwargs.get("x")
            target_y = y if y is not None else kwargs.get("y")
            pt_elem = _find_by_point(
                target_x,
                target_y,
                target_hwnd,
                last_win_args,
                direct_auto_ids=direct_auto_ids,
                search_terms=search_terms,
            )
            if pt_elem:
                return pt_elem

            win_title = str(last_win_args.get("window_title") or "") if last_win_args else ""
            # Why: WebContentのアクティブDocumentControlを起点にして深層DOM要素を構文解析結果に基づき自律検出
            doc_elem = _find_in_document_tree(target_hwnd, direct_auto_ids, search_terms, window_title=win_title)
            if doc_elem:
                return doc_elem

            return _find_in_pywinauto_windows(desktop, target_hwnd, last_win_args, search_terms)
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
