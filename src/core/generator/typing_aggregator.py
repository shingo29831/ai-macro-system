"""Module: @role: 一連のキー入力・IME入力・UIAイベントを時系列解析し、単一のテキスト入力やキー操作セッションとして集約する。"""

import logging
import re
import unicodedata
from typing import Any, Dict, List, Optional

from core.generator.typing_session_helpers import (
    ESSENTIAL_KEYS,
    build_fallback_text,
    collect_global_uia_texts,
    filter_session_events,
    get_app_context,
    get_event_diff_val,
    merge_consecutive_type_events,
    propagate_click_context,
    resolve_session_final_text,
)

logger = logging.getLogger(__name__)

_SPECIAL_KEY_NAMES = {
    "enter", "tab", "esc", "escape", "up", "down", "left", "right",
    "left_click", "right_click", "middle_click",
    "win", "cmd", "windows", "ctrl", "alt", "shift", "caps_lock",
    "home", "end", "page_up", "page_down", "insert", "delete", "print_screen",
    "f1", "f2", "f3", "f4", "f5", "f6", "f7", "f8", "f9", "f10", "f11", "f12",
}
_URL_DOMAIN_REGEX = re.compile(r"^[a-zA-Z0-9][-a-zA-Z0-9]*\.[a-zA-Z0-9][-a-zA-Z0-9.]*(/[^\s]*)?$")


class TypingSessionAggregator:
    def __init__(self, session_timeout_ms: int = 2000) -> None:
        self.session_timeout_ms = session_timeout_ms

    def aggregate_events(self, raw_events: List[Dict[str, Any]]) -> List[Dict[str, Any]]:
        if not raw_events:
            return []

        propagate_click_context(raw_events)
        global_uia_texts_map = collect_global_uia_texts(raw_events)

        aggregated_events: List[Dict[str, Any]] = []
        current_session: List[Dict[str, Any]] = []

        for i, event in enumerate(raw_events):
            action = event.get("raw_action", "")
            current_window = event.get("window_name", "")
            current_ts = event.get("timestamp", 0)

            if current_session:
                self._check_and_flush_boundary(
                    current_session, event, action, current_window, current_ts, aggregated_events, raw_events[i:]
                )

            role_lower = str(event.get("semantic_role", "")).lower()
            combo_parts = [p for p in role_lower.split("+") if p]
            combo_alphas = [p for p in combo_parts if len(p) == 1 and p.isalpha()]
            # Why: 複合コンボ(shift+a+tab等)でも英字1字が含まれる場合は大文字入力として救出
            is_shift_char = action == "key_combo" and "shift" in role_lower and len(combo_alphas) == 1

            is_special_key = action in ["key_down", "key_press", "key_combo"] and (
                role_lower.startswith("key.")
                or role_lower in _SPECIAL_KEY_NAMES
                or ("+" in role_lower and not is_shift_char)
            )
            is_text_input = action in ["type_text", "key_down", "key_press", "key_combo"] and not is_special_key
            is_uia_scan = action == "uia_scan"
            is_confirm_key = is_special_key and role_lower in ["enter", "tab"]
            is_mouse_move = action in ["move", "mouse_move", "mouse_hover"]
            is_ime_toggle = "+" in role_lower and any(k in role_lower for k in ["space", "grave", "kanji"])
            is_typing_combo = (action == "key_combo" and not is_shift_char) or is_ime_toggle

            if role_lower == "enter":
                current_session.append(event)
                self._flush_session(current_session, aggregated_events, future_events=raw_events[i + 1:])
                continue

            if is_text_input or is_uia_scan or is_confirm_key or is_typing_combo:
                current_session.append(event)
                continue

            if is_mouse_move:
                if current_session:
                    last_ev = current_session[-1]
                    lx, ly = last_ev.get("cursor_x", last_ev.get("x", 0)), last_ev.get("cursor_y", last_ev.get("y", 0))
                    cx, cy = event.get("cursor_x", event.get("x", 0)), event.get("cursor_y", event.get("y", 0))
                    dist = ((cx - lx) ** 2 + (cy - ly) ** 2) ** 0.5
                    curr_ctx = get_app_context(event)
                    has_new_target = bool(curr_ctx.get("element_name") or curr_ctx.get("css_selector"))
                    if dist > 35 or has_new_target:
                        self._flush_session(current_session, aggregated_events, future_events=raw_events[i:])
                    else:
                        continue
                aggregated_events.append(event)
                continue

            if current_session:
                self._flush_session(current_session, aggregated_events, future_events=raw_events[i:])
            aggregated_events.append(event)

        if current_session:
            self._flush_session(current_session, aggregated_events, future_events=None)

        return merge_consecutive_type_events(aggregated_events, global_uia_texts_map)

    def _check_and_flush_boundary(
        self,
        current_session: List[Dict[str, Any]],
        event: Dict[str, Any],
        action: str,
        current_window: str,
        current_ts: int,
        aggregated_events: List[Dict[str, Any]],
        future_events: List[Dict[str, Any]],
    ) -> None:
        last_event = current_session[-1]
        last_window = last_event.get("window_name", "")
        last_ts = last_event.get("timestamp", 0)
        last_element = get_app_context(last_event).get("element_name", "")
        curr_element = get_app_context(event).get("element_name", "")
        diff_val = get_event_diff_val(event)
        time_diff = current_ts - last_ts if current_ts > 0 and last_ts > 0 else 0

        # Why: サジェスト展開による画面差分や要素名変化で入力セッションが細切れになるのを防止
        is_ongoing_typing = action in ["key_down", "key_press", "key_combo"] and time_diff < 1500
        if current_window and last_window and current_window != last_window:
            self._flush_session(current_session, aggregated_events, future_events=future_events)
        elif not is_ongoing_typing and diff_val > 15.0 and time_diff > 500:
            self._flush_session(current_session, aggregated_events, future_events=future_events)
        elif time_diff > 3000:
            self._flush_session(current_session, aggregated_events, future_events=future_events)
        elif not is_ongoing_typing and last_element and curr_element and last_element != curr_element:
            if diff_val > 5.0 or time_diff > 2000:
                self._flush_session(current_session, aggregated_events, future_events=future_events)

    def _flush_session(
        self,
        session: List[Dict[str, Any]],
        output_list: List[Dict[str, Any]],
        future_events: Optional[List[Dict[str, Any]]] = None,
    ) -> None:
        if not session:
            return

        if not any(e.get("raw_action") in ["key_down", "key_press", "type_text", "key_combo"] for e in session):
            output_list.extend(session)
            session.clear()
            return

        filtered_session = filter_session_events(session)
        if not filtered_session:
            session.clear()
            return
        session[:] = filtered_session

        if len(session) == 1:
            single_event = session[0]
            action = single_event.get("raw_action")
            role_lower = str(single_event.get("semantic_role", "")).lower()
            if action == "uia_scan":
                output_list.append(single_event)
                session.clear()
                return

            diff_val = get_event_diff_val(single_event)
            is_essential_key = role_lower in ESSENTIAL_KEYS
            is_shortcut = action == "key_combo" and any(mod in role_lower for mod in ["ctrl", "alt", "win", "cmd"])
            if diff_val < 0.1 and not is_essential_key and not is_shortcut:
                session.clear()
                return
            output_list.append(single_event)
            session.clear()
            return

        trailing_events: List[Dict[str, Any]] = []
        while session:
            last_event = session[-1]
            action = last_event.get("raw_action", "")
            role_lower = str(last_event.get("semantic_role", "")).lower()
            is_special = action in ["key_down", "key_press"] and (
                role_lower.startswith("key.") or role_lower in ["enter", "tab", "esc", "up", "down", "left", "right"]
            )
            if is_special or action == "uia_scan":
                trailing_events.insert(0, session.pop())
            else:
                break

        if not session:
            output_list.extend(trailing_events)
            return

        fallback_text = build_fallback_text(session)
        has_suggest_selection = any(
            item.get("raw_action") in ["key_down", "key_press"]
            and str(item.get("semantic_role", "")).lower() in ["tab", "down", "up"]
            for item in session + trailing_events
        )
        any_ime_active = any(item.get("ime_active", False) for item in session)

        final_text = resolve_session_final_text(
            session=session,
            trailing_events=trailing_events,
            future_events=future_events,
            fallback_text=fallback_text,
            has_suggest_selection=has_suggest_selection,
            any_ime_active=any_ime_active,
        )

        if final_text:
            # Why: リンクが全角で記録された場合に全角半角判定を行い半角URLへ正規化
            norm_candidate = unicodedata.normalize("NFKC", final_text).strip()
            norm_lower = norm_candidate.lower()
            if norm_lower.startswith(("http://", "https://", "www.", "ftp://")) or _URL_DOMAIN_REGEX.match(norm_lower):
                final_text = norm_candidate

            if not self._try_merge_with_previous_output(output_list, session, final_text):
                representative_event = session[0].copy()
                representative_event["raw_action"] = "type_text"
                representative_event["semantic_role"] = final_text
                representative_event["ui_type"] = "text"
                representative_event["fallback_events"] = [item.get("event_id") for item in session if item.get("event_id")]
                representative_event["diff_val"] = 0.0
                output_list.append(representative_event)
        else:
            output_list.extend(session)

        trailing_special_keys: List[Dict[str, Any]] = []
        for e in trailing_events:
            if e.get("raw_action") == "uia_scan":
                continue
            role_lower = str(e.get("semantic_role", "")).lower()
            if final_text:
                if role_lower == "tab":
                    continue
                # Why: サジェスト選択後や画面遷移を促す確定Enterは検索実行キーとして確実に保護
                if role_lower in ["enter", "return"] or any(k in role_lower for k in ["enter", "return"]):
                    has_future_nav = bool(
                        future_events and any(f.get("window_name") != session[0].get("window_name") for f in future_events[:8])
                    )
                    if not (has_suggest_selection or has_future_nav) and (any_ime_active or e.get("ime_active")):
                        continue
            trailing_special_keys.append(e)

        output_list.extend(trailing_special_keys)
        session.clear()

    @staticmethod
    def _try_merge_with_previous_output(
        output_list: List[Dict[str, Any]],
        session: List[Dict[str, Any]],
        final_text: str,
    ) -> bool:
        if not output_list or output_list[-1].get("raw_action") != "type_text":
            return False

        prev_text = str(output_list[-1].get("semantic_role", ""))
        if prev_text == final_text:
            session.clear()
            return True

        prev_text_lower = prev_text.lower()
        final_text_lower = final_text.lower()
        is_suffix_or_sub = len(final_text_lower) < len(prev_text_lower) and (
            prev_text_lower.endswith(final_text_lower) or final_text_lower in prev_text_lower
        )

        # Why: 同一要素への連続入力断片は切り捨てず安全に連結してユーザー入力を完全再現
        prev_ctx = get_app_context(output_list[-1])
        curr_ctx = get_app_context(session[0])
        same_target = (
            (prev_ctx.get("css_selector") and prev_ctx.get("css_selector") == curr_ctx.get("css_selector"))
            or (prev_ctx.get("element_name") and prev_ctx.get("element_name") == curr_ctx.get("element_name"))
        )
        if same_target and prev_text and final_text and not is_suffix_or_sub:
            if not prev_text.endswith(final_text) and final_text not in prev_text:
                output_list[-1]["semantic_role"] = prev_text + final_text
                output_list[-1]["fallback_events"].extend([item.get("event_id") for item in session if item.get("event_id")])
                session.clear()
                return True

        if is_suffix_or_sub:
            session.clear()
            return True
        return False
