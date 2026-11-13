"""Module: @role: キー入力セッション集約で用いるコンテキスト伝播、ノイズ除外、ローマ字復元、検索クエリ解決および連続入力マージを担当する。"""

from typing import Any, Dict, List, Optional

from core.generator.typing_text_resolver import (
    IGNORE_EXACT_KEYS,
    IGNORE_MODIFIERS,
    _convert_romaji_chunk,
    build_fallback_text,
    extract_query_from_title,
    extract_search_query,
    get_app_context,
    merge_consecutive_type_events,
    resolve_session_final_text,
)

ESSENTIAL_KEYS = {"space", "backspace", "delete", "enter", "tab", "esc"}
BROWSER_TITLE_KEYWORDS = ["firefox", "chrome", "edge"]

__all__ = [
    "IGNORE_EXACT_KEYS",
    "IGNORE_MODIFIERS",
    "ESSENTIAL_KEYS",
    "BROWSER_TITLE_KEYWORDS",
    "parse_diff_value",
    "get_app_context",
    "get_event_diff_val",
    "propagate_click_context",
    "collect_global_uia_texts",
    "filter_session_events",
    "_convert_romaji_chunk",
    "build_fallback_text",
    "extract_search_query",
    "extract_query_from_title",
    "resolve_session_final_text",
    "merge_consecutive_type_events",
]


def parse_diff_value(val: Any) -> float:
    if isinstance(val, (int, float)):
        return float(val)
    if isinstance(val, str):
        try:
            return float(val.replace("%", "").strip())
        except ValueError:
            return 0.0
    return 0.0


def get_event_diff_val(event: Dict[str, Any]) -> float:
    return parse_diff_value(
        event.get("diff_val") or event.get("diffRatio") or event.get("Diff") or event.get("diff")
    )


def propagate_click_context(raw_events: List[Dict[str, Any]]) -> None:
    # Why: キー入力のフォーカス先は直前クリック要素のみから継承しホバー移動による汚染を根絶
    last_click_ctx: Dict[str, Any] = {}
    last_click_win = ""
    for ev in raw_events:
        ev_act = ev.get("raw_action", "")
        ev_win = ev.get("window_name", "")
        ctx = get_app_context(ev)
        elem = ctx.get("element_name") or ""
        sel = ctx.get("css_selector") or ""

        if ev_act in ["click", "mouse_click"]:
            if elem or sel or ctx.get("xpath"):
                last_click_ctx = ctx.copy()
                last_click_win = ev_win
        elif ev_act in ["key_down", "key_press", "type_text", "key_combo"] and last_click_ctx:
            is_win_match = (
                (ev_win == last_click_win)
                or not last_click_win
                or not ev_win
                or any(b in (ev_win + last_click_win).lower() for b in BROWSER_TITLE_KEYWORDS)
            )
            if is_win_match:
                e_ctx = ev.setdefault("app_context", {})
                e_sel = e_ctx.get("css_selector") or e_ctx.get("xpath")
                l_sel = last_click_ctx.get("css_selector") or last_click_ctx.get("xpath")
                if not e_sel or e_sel == l_sel:
                    for k in ["element_name", "css_selector", "xpath", "control_type"]:
                        if not e_ctx.get(k) and last_click_ctx.get(k):
                            e_ctx[k] = last_click_ctx[k]


def collect_global_uia_texts(raw_events: List[Dict[str, Any]]) -> Dict[str, str]:
    global_uia_texts_map: Dict[str, str] = {}
    for event in raw_events:
        app_ctx = get_app_context(event)
        if app_ctx:
            val = app_ctx.get("value") or app_ctx.get("text")
            if val and isinstance(val, str):
                val_str = val.strip()
                global_uia_texts_map[val_str.lower()] = val_str

        uia_info = event.get("content", {}).get("uia_info", {})
        if isinstance(uia_info, dict):
            val = uia_info.get("value") or uia_info.get("name")
            if val and isinstance(val, str):
                val_str = val.strip()
                global_uia_texts_map[val_str.lower()] = val_str
    return global_uia_texts_map


def filter_session_events(session: List[Dict[str, Any]]) -> List[Dict[str, Any]]:
    filtered_session: List[Dict[str, Any]] = []
    last_text: Optional[str] = None
    last_ime_active: Optional[bool] = None

    for item in session:
        action = item.get("raw_action", "")
        role_lower = str(item.get("semantic_role", "")).lower()
        ime_active = bool(item.get("ime_active", False))
        app_ctx = get_app_context(item)
        current_text = app_ctx.get("value") or app_ctx.get("text") or ""
        diff_val = get_event_diff_val(item)

        is_essential_key = role_lower in ESSENTIAL_KEYS
        is_combo = action == "key_combo"
        is_shortcut = is_combo and any(mod in role_lower for mod in ["ctrl", "alt", "win", "cmd"])
        is_ime_toggle = "+" in role_lower and any(k in role_lower for k in ["space", "grave", "kanji"])

        if is_ime_toggle:
            last_ime_active = ime_active
            continue

        is_valid = False
        has_text_change = last_text is not None and current_text != last_text and current_text != ""
        has_screen_diff = diff_val >= 0.1
        has_ime_change = last_ime_active is not None and ime_active != last_ime_active

        if is_combo:
            if has_text_change or has_ime_change or is_shortcut:
                is_valid = True
            elif has_screen_diff:
                is_noise_combo = (
                    not is_shortcut
                    and len(role_lower.split("+")) >= 3
                    and not any(p.isalpha() and len(p) == 1 for p in role_lower.split("+"))
                )
                is_valid = not is_noise_combo
        else:
            if (
                last_text is None
                or has_text_change
                or has_screen_diff
                or is_essential_key
                or (action in ["key_press", "key_down"] and len(role_lower) == 1)
            ):
                is_valid = True

        if is_valid:
            filtered_session.append(item)
            if current_text != "":
                last_text = current_text
        last_ime_active = ime_active

    return filtered_session
