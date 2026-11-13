"""Module: @role: キー入力セッション集約で用いるコンテキスト伝播、ノイズ除外、ローマ字復元、検索クエリ解決および連続入力マージを担当する。"""

import re
import urllib.parse
from typing import Any, Dict, List, Optional, Tuple

IGNORE_EXACT_KEYS = {
    "tab", "enter", "delete", "esc", "shift", "ctrl", "alt",
    "win", "cmd", "left_click", "right_click", "middle_click",
}
IGNORE_MODIFIERS = ["shift", "ctrl", "alt", "win", "cmd"]
ESSENTIAL_KEYS = {"space", "backspace", "delete", "enter", "tab", "esc"}
BROWSER_TITLE_KEYWORDS = ["firefox", "chrome", "edge"]


def parse_diff_value(val: Any) -> float:
    if isinstance(val, (int, float)):
        return float(val)
    if isinstance(val, str):
        try:
            return float(val.replace("%", "").strip())
        except ValueError:
            return 0.0
    return 0.0


def get_app_context(event: Dict[str, Any]) -> Dict[str, Any]:
    ctx = event.get("app_context") or event.get("AppSpecificContext") or event.get("appSpecificContext")
    return ctx if isinstance(ctx, dict) else {}


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


def _convert_romaji_chunk(chunk: str) -> str:
    if not chunk:
        return chunk
    try:
        from core.recorder.romaji_converter import to_hiragana
        # Why: アルファベット末尾を含む入力(kaisaA等)をひらがなと大文字英字に適切分離
        alpha_tail = re.search(r"[A-Za-z]+$", chunk)
        if alpha_tail:
            tail_idx = alpha_tail.start()
            return to_hiragana(chunk[:tail_idx]) + chunk[tail_idx:]
        return to_hiragana(chunk)
    except ImportError:
        return chunk


def build_fallback_text(session: List[Dict[str, Any]]) -> str:
    fallback_text = ""
    current_chunk = ""
    current_ime_state: Optional[bool] = None

    for item in session:
        action = item.get("raw_action", "")
        role = str(item.get("semantic_role", ""))
        r_lower = role.lower()
        ime_active = bool(item.get("ime_active", False))

        is_combo = action == "key_combo" or "+" in r_lower
        char = ""

        # Why: 複合コンボキー(shift+a+enter+tab等)から本来のアルファベット文字を安全抽出
        if is_combo and "shift" in r_lower:
            alpha_parts = [p for p in r_lower.split("+") if len(p) == 1 and p.isalpha()]
            if alpha_parts:
                char = alpha_parts[0].upper()
                is_combo = False

        if is_combo:
            if current_ime_state is not None:
                if current_ime_state and current_chunk:
                    try:
                        from core.recorder.romaji_converter import to_hiragana
                        current_chunk = to_hiragana(current_chunk)
                    except ImportError:
                        pass
                fallback_text += current_chunk
                current_chunk = ""
                current_ime_state = None
            continue

        if not char:
            if r_lower == "backspace":
                if current_chunk:
                    current_chunk = current_chunk[:-1]
                elif fallback_text:
                    fallback_text = fallback_text[:-1]
                continue
            elif r_lower == "space":
                char = " "
            elif r_lower in IGNORE_EXACT_KEYS or r_lower.startswith("key."):
                continue
            elif len(r_lower) > 1 and any(mod in r_lower for mod in IGNORE_MODIFIERS):
                continue
            else:
                char = role

        if current_ime_state is None:
            current_ime_state = ime_active

        if ime_active != current_ime_state:
            if current_ime_state and current_chunk:
                try:
                    from core.recorder.romaji_converter import to_hiragana
                    current_chunk = to_hiragana(current_chunk)
                except ImportError:
                    pass
            fallback_text += current_chunk
            current_chunk = char
            current_ime_state = ime_active
        else:
            current_chunk += char

    if current_chunk:
        if current_ime_state:
            current_chunk = _convert_romaji_chunk(current_chunk)
        fallback_text += current_chunk

    return fallback_text


def extract_search_query(url_or_text: str) -> Tuple[Optional[str], bool]:
    if not url_or_text.startswith("http"):
        if any(tld in url_or_text for tld in [".com", ".co.jp", ".net", ".org"]):
            return None, False
        return url_or_text, False
    try:
        parsed = urllib.parse.urlsplit(url_or_text)
        qs = urllib.parse.parse_qs(parsed.query)
        for key in ["q", "query", "p", "wd", "word", "search_query", "text"]:
            if key in qs and qs[key]:
                return urllib.parse.unquote_plus(qs[key][0]), True
    except Exception:
        pass
    return None, False


def extract_query_from_title(title: str) -> Optional[str]:
    if not title:
        return None
    patterns = [
        r"^(.+?)\s*[-—–―]\s*(?:Google\s*検索|Google\s*Search|Yahoo!検索|Bing(?:\s*検索)?|DuckDuckGo)",
        r"^「(.+?)」の検索結果",
        r"^(.+?)\s*[-—–―]\s*(?:検索|Search)",
    ]
    for pat in patterns:
        m = re.search(pat, title, re.IGNORECASE)
        if m:
            q = m.group(1).strip()
            if q and q.lower() not in ["検索", "search"]:
                return q
    return None


def resolve_session_final_text(
    session: List[Dict[str, Any]],
    trailing_events: List[Dict[str, Any]],
    future_events: Optional[List[Dict[str, Any]]],
    fallback_text: str,
    has_suggest_selection: bool,
    any_ime_active: bool,
) -> str:
    confirmed_queries: List[str] = []
    latest_uia_text = ""

    for item in reversed(session + trailing_events):
        if item.get("raw_action") == "uia_scan":
            uia_info = item.get("content", {}).get("uia_info", {})
            val = uia_info.get("value") or uia_info.get("name")
            candidates = uia_info.get("candidates") or []
            if candidates and has_suggest_selection and not val:
                val = candidates[0]
            if val and str(val).strip():
                extracted, is_url_query = extract_search_query(str(val).strip())
                if extracted:
                    if is_url_query and extracted not in confirmed_queries:
                        confirmed_queries.append(extracted)
                    elif not is_url_query and not latest_uia_text:
                        latest_uia_text = extracted

        app_ctx = get_app_context(item)
        if app_ctx:
            title_q = extract_query_from_title(str(app_ctx.get("element_name") or ""))
            if title_q and title_q not in confirmed_queries:
                confirmed_queries.append(title_q)

            ctrl_type = str(app_ctx.get("control_type", "")).lower()
            if any(t in ctrl_type for t in ["button", "window", "listitem"]):
                continue

            val = app_ctx.get("value") or app_ctx.get("text") or app_ctx.get("url")
            if val and str(val).strip():
                extracted, is_url_query = extract_search_query(str(val).strip())
                if extracted:
                    if is_url_query and extracted not in confirmed_queries:
                        confirmed_queries.insert(0, extracted)
                    elif not is_url_query and not latest_uia_text:
                        latest_uia_text = extracted

    confirmed_future_queries: List[str] = []
    future_elem_text = ""
    if future_events:
        first_ctx = get_app_context(session[0])
        target_elem = first_ctx.get("element_name", "")
        target_sel = first_ctx.get("css_selector", "")
        for f_evt in future_events[:15]:
            f_win = f_evt.get("window_name") or f_evt.get("WindowName", "")
            title_q = extract_query_from_title(f_win)
            if title_q and title_q not in confirmed_future_queries:
                confirmed_future_queries.append(title_q)

            f_act = f_evt.get("raw_action", "")
            f_ctx = get_app_context(f_evt)
            f_name = f_ctx.get("element_name", "")
            f_sel = f_ctx.get("css_selector", "")
            f_val = f_ctx.get("value") or f_ctx.get("text")

            if f_act in ["click", "mouse_click"]:
                if (target_elem and f_name and f_name != target_elem) or (target_sel and f_sel and f_sel != target_sel):
                    break

            if f_val and isinstance(f_val, str) and f_val.strip() and not future_elem_text:
                if (target_elem and f_name == target_elem) or (target_sel and f_sel == target_sel):
                    future_elem_text = f_val.strip()
            if not future_elem_text and target_sel:
                for snap_dict in [f_ctx.get("form_snapshot"), f_ctx.get("committed_values")]:
                    if isinstance(snap_dict, dict) and snap_dict.get(target_sel):
                        future_elem_text = str(snap_dict[target_sel]).strip()
                        break

            for url_candidate in [f_ctx.get("url"), f_ctx.get("value"), f_ctx.get("text"), f_ctx.get("element_name")]:
                if url_candidate and isinstance(url_candidate, str):
                    extracted, is_url_query = extract_search_query(url_candidate)
                    if is_url_query and extracted and extracted not in confirmed_future_queries:
                        confirmed_future_queries.append(extracted)

            if f_ctx.get("query") and f_ctx["query"] not in confirmed_future_queries:
                confirmed_future_queries.append(f_ctx["query"])

    has_actual_chars = any(
        str(item.get("semantic_role", "")).lower() not in IGNORE_EXACT_KEYS
        and not str(item.get("semantic_role", "")).lower().startswith("key.")
        and len(str(item.get("semantic_role", ""))) == 1
        for item in session
    )

    matched_future_query: Optional[str] = None
    current_input = (latest_uia_text or fallback_text or "").strip().lower()
    if confirmed_future_queries and (has_actual_chars or any_ime_active):
        for fq in confirmed_future_queries:
            fq_lower = fq.lower()
            matches_text = bool(current_input and (current_input in fq_lower or fq_lower.startswith(current_input)))
            matches_suggest_or_ime = (has_suggest_selection or any_ime_active) and (has_actual_chars or len(current_input) > 0)
            if not current_input or matches_text or matches_suggest_or_ime:
                matched_future_query = fq
                break

    if confirmed_queries:
        return confirmed_queries[0]
    if matched_future_query:
        return matched_future_query
    if (has_suggest_selection or any_ime_active) and latest_uia_text:
        return latest_uia_text
    if latest_uia_text and fallback_text and (fallback_text in latest_uia_text or latest_uia_text in fallback_text):
        return latest_uia_text
    if future_elem_text:
        return future_elem_text
    if not any_ime_active and fallback_text and not has_suggest_selection:
        return fallback_text
    return latest_uia_text if latest_uia_text else fallback_text


def merge_consecutive_type_events(
    aggregated_events: List[Dict[str, Any]],
    global_uia_texts_map: Dict[str, str],
) -> List[Dict[str, Any]]:
    final_events: List[Dict[str, Any]] = []
    i = 0
    while i < len(aggregated_events):
        event = aggregated_events[i]
        if event.get("raw_action") != "type_text":
            final_events.append(event)
            i += 1
            continue

        events_to_merge = [event]
        intervening_events: List[Dict[str, Any]] = []
        last_valid_text = str(event.get("semantic_role", ""))
        curr_win = event.get("window_name", "")
        curr_elem = get_app_context(event).get("element_name", "")

        j = i + 1
        while j < len(aggregated_events):
            next_event = aggregated_events[j]
            action = next_event.get("raw_action")

            if action == "type_text":
                next_win = next_event.get("window_name", "")
                next_elem = get_app_context(next_event).get("element_name", "")
                # Why: 同一要素への分割入力は断片を結合し最終的な完成文字列へ復元
                if curr_win == next_win and curr_elem == next_elem:
                    events_to_merge.append(next_event)
                    nxt_text = str(next_event.get("semantic_role", "")).strip()
                    if last_valid_text and nxt_text and not nxt_text.startswith(last_valid_text):
                        last_valid_text = last_valid_text + nxt_text
                    else:
                        last_valid_text = nxt_text or last_valid_text
                else:
                    break
            elif action in ["mouse_move", "mouse_hover", "mouse_click", "mouse_scroll"]:
                next_elem = get_app_context(next_event).get("element_name", "")
                if action == "mouse_click" and next_elem != curr_elem:
                    break
                intervening_events.append(next_event)
            else:
                break
            j += 1

        if len(events_to_merge) > 1:
            final_events.extend(intervening_events)
            merged_event = events_to_merge[0].copy()
            merged_event["semantic_role"] = last_valid_text
            fallback_events: List[Any] = []
            for e in events_to_merge:
                fallback_events.extend(e.get("fallback_events", []))
            merged_event["fallback_events"] = fallback_events
            final_events.append(merged_event)
            i = j - 1
        else:
            single_text = str(event.get("semantic_role", ""))
            single_lower = single_text.lower()
            if single_lower in global_uia_texts_map and single_text != global_uia_texts_map[single_lower]:
                event["semantic_role"] = global_uia_texts_map[single_lower]
            final_events.append(event)

        i += 1
    return final_events
