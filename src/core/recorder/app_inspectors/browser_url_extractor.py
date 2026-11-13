"""Module: @role: ブラウザURLのパース・クエリデコードおよびUIAを用いたアドレスバー情報取得を担当する。"""

import ctypes
import logging
import urllib.parse
from typing import Any, Callable, Dict

logger = logging.getLogger(__name__)

_KNOWN_ADDRESS_BAR_IDS = ("urlbar-input", "address-edit-box", "view_1020")
_QUERY_KEYS = ("q", "query", "p", "wd", "word", "search_query", "text")


def parse_url_details(raw_url: str) -> Dict[str, Any]:
    """URL文字列からドメイン、パス、検索クエリ(q=等)を安全にデコード・パースする。"""
    if not raw_url or not isinstance(raw_url, str):
        return {}
    text = raw_url.strip()
    if not text:
        return {}

    # Why: ブラウザUIで省略されるスキーム(https://)を補完して正規URL化
    candidate = text
    if not candidate.startswith(("http://", "https://", "about:", "chrome://", "edge://")):
        if "." in candidate.split("/")[0] or "?" in candidate:
            candidate = "https://" + candidate

    try:
        parsed = urllib.parse.urlsplit(candidate)
        if not parsed.netloc and parsed.scheme not in ("about", "chrome", "edge"):
            return {}

        query_dict = urllib.parse.parse_qs(parsed.query)
        search_query = None
        for key in _QUERY_KEYS:
            if key in query_dict and query_dict[key]:
                # Why: URLエンコード文字列(%E7%B9%94...)を日本語平文に確実に復元
                search_query = urllib.parse.unquote_plus(query_dict[key][0])
                break

        return {
            "raw_text": text,
            "full_url": candidate,
            "scheme": parsed.scheme,
            "domain": parsed.netloc,
            "path": parsed.path,
            "query": search_query,
            "params": {
                k: urllib.parse.unquote_plus(v[0])
                if len(v) == 1
                else [urllib.parse.unquote_plus(x) for x in v]
                for k, v in query_dict.items()
            },
        }
    except Exception:
        return {}


def _build_url_result(val: str, rect: Any = None) -> Dict[str, Any]:
    result: Dict[str, Any] = {
        "url": "",
        "query": None,
        "url_details": {},
        "address_bar_text": val,
    }
    if rect is not None:
        result["address_bar_rect"] = {
            "left": rect.left,
            "top": rect.top,
            "right": rect.right,
            "bottom": rect.bottom,
        }
    parsed = parse_url_details(val)
    if parsed:
        result["url"] = parsed.get("full_url", "")
        result["query"] = parsed.get("query")
        result["url_details"] = parsed
    return result


def get_address_bar_info(
    window_info: Dict[str, Any],
    extract_elem_value: Callable[[Any], str],
    desktop: Any = None,
) -> Dict[str, Any]:
    """UIAを用いて主要ブラウザのアドレスバーから確定URLおよび検索クエリを取得する。"""
    result: Dict[str, Any] = {"url": "", "query": None, "url_details": {}, "address_bar_text": ""}
    try:
        import pywinauto

        if desktop is None:
            desktop = pywinauto.Desktop(backend="uia")

        # Why: window_info内のキー名差異(hwnd / handle)とフォアグラウンドHWNDをフォールバック解決
        hwnd = window_info.get("hwnd") or window_info.get("handle")
        if not hwnd:
            try:
                hwnd = ctypes.windll.user32.GetForegroundWindow()
            except Exception:
                hwnd = None

        window = None
        if hwnd:
            try:
                window = desktop.window(handle=int(hwnd))
            except Exception as e:
                logger.debug(f"[BrowserUrlExtractor] HWNDからのウィンドウ特定失敗: {e}")

        if not window:
            return result

        # Why: Firefox(urlbar-input)やChromiumの定数IDが存在する場合は即座に特定
        for aid in _KNOWN_ADDRESS_BAR_IDS:
            try:
                edit = window.child_window(auto_id=aid, control_type="Edit")
                if edit.exists(timeout=0.1):
                    val = extract_elem_value(edit)
                    if val:
                        built = _build_url_result(val)
                        if built.get("url"):
                            return built
                        result["address_bar_text"] = val
            except Exception:
                pass

        # Why: 全子孫走査によるUIフリーズを回避し、上部ツールバー内のEditのみを高速走査
        top_edits = []
        try:
            for child in window.children():
                c_type = getattr(child.element_info, "control_type", "")
                if c_type in ("ToolBar", "Pane", "Custom"):
                    top_edits.extend(child.children(control_type="Edit"))
            if not top_edits:
                top_edits = window.children(control_type="Edit")
        except Exception:
            top_edits = []

        candidates = []
        for edit in top_edits[:10]:
            try:
                rect = edit.rectangle()
                if rect.top > 250:
                    continue
                auto_id = getattr(edit.element_info, "automation_id", "") or ""
                name = edit.window_text() or ""
                val = extract_elem_value(edit)

                score = 0
                if any(kw in auto_id.lower() for kw in ("url", "address", "omnibox")):
                    score += 50
                if any(kw in name.lower() for kw in ("アドレス", "address", "url", "検索")):
                    score += 40
                if val and any(ind in val for ind in ("http", "www.", ".com", ".jp")):
                    score += 30
                if val and score > 0:
                    candidates.append((score, val, rect))
            except Exception:
                continue

        if candidates:
            candidates.sort(key=lambda x: x[0], reverse=True)
            _, best_val, best_rect = candidates[0]
            return _build_url_result(best_val, best_rect)

    except Exception as e:
        logger.debug(f"[BrowserUrlExtractor] アドレスバー特定失敗: {e}")
    return result
