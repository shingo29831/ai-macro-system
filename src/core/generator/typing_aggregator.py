import difflib
import logging
import urllib.parse
from typing import List, Dict, Any, Optional

logger = logging.getLogger(__name__)

class TypingSessionAggregator:
    def __init__(self, session_timeout_ms: int = 2000):
        self.session_timeout_ms = session_timeout_ms

    def aggregate_events(self, raw_events: List[Dict[str, Any]]) -> List[Dict[str, Any]]:
        if not raw_events:
            return []

        def _parse_diff(val: Any) -> float:
            if isinstance(val, (int, float)):
                return float(val)
            if isinstance(val, str):
                try:
                    return float(val.replace("%", "").strip())
                except ValueError:
                    return 0.0
            return 0.0

        global_uia_texts_map = {}
        for event in raw_events:
            app_ctx = event.get("app_context") or event.get("AppSpecificContext") or event.get("appSpecificContext")
            if isinstance(app_ctx, dict):
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

        aggregated_events: List[Dict[str, Any]] = []
        current_session: List[Dict[str, Any]] = []

        for i, event in enumerate(raw_events):
            action = event.get("raw_action", "")
            current_window = event.get("window_name", "")
            current_ts = event.get("timestamp", 0)
            
            if current_session:
                last_event = current_session[-1]
                last_window = last_event.get("window_name", "")
                last_ts = last_event.get("timestamp", 0)
                
                last_app_ctx = last_event.get("app_context") or last_event.get("AppSpecificContext") or last_event.get("appSpecificContext") or {}
                curr_app_ctx = event.get("app_context") or event.get("AppSpecificContext") or event.get("appSpecificContext") or {}
                
                last_element = last_app_ctx.get("element_name", "")
                curr_element = curr_app_ctx.get("element_name", "")
                
                if current_window and last_window and current_window != last_window:
                    self._flush_session(current_session, aggregated_events)
                elif last_element and curr_element and last_element != curr_element:
                    self._flush_session(current_session, aggregated_events)
                elif _parse_diff(event.get("diff_val") or event.get("diffRatio") or event.get("Diff") or event.get("diff")) > 15.0:
                    if current_ts == 0 or last_ts == 0 or (current_ts - last_ts) > 500:
                        self._flush_session(current_session, aggregated_events)

            role_lower = str(event.get("semantic_role", "")).lower()
            
            is_shift_char = action == "key_combo" and "shift" in role_lower and len(role_lower.split("+")) == 2 and len(role_lower.split("+")[1]) == 1
            
            is_special_key = action in ["key_down", "key_press", "key_combo"] and (
                role_lower.startswith("key.") or 
                role_lower in ["enter", "tab", "esc", "up", "down", "left", "right"] or
                ("+" in role_lower and not is_shift_char)
            )
            is_text_input = action in ["type_text", "key_down", "key_press", "key_combo"] and not is_special_key
            is_uia_scan = action == "uia_scan"
            is_confirm_key = is_special_key and role_lower in ["enter", "tab"]
            ime_active = event.get("ime_active", False)
            is_mouse_move = action in ["mouse_move", "mouse_hover"]
            
            is_ime_toggle = "+" in role_lower and any(k in role_lower for k in ["space", "grave", "kanji"])
            is_typing_combo = (action == "key_combo" and not is_shift_char) or is_ime_toggle

            if role_lower == "enter":
                current_session.append(event)
                self._flush_session(current_session, aggregated_events)
                continue

            if is_text_input or is_uia_scan or is_confirm_key or is_typing_combo:
                current_session.append(event)
                continue
            elif is_mouse_move:
                aggregated_events.append(event)
                continue

            if current_session:
                self._flush_session(current_session, aggregated_events)

            aggregated_events.append(event)

        if current_session:
            self._flush_session(current_session, aggregated_events)

        final_events = []
        i = 0
        while i < len(aggregated_events):
            event = aggregated_events[i]
            
            if event.get("raw_action") == "type_text":
                best_match_idx = -1
                best_combined_text = ""
                best_intervening = []
                best_events_to_merge = []
                
                text_parts = [event.get("semantic_role", "")]
                events_to_merge = [event]
                intervening_events = []
                
                j = i + 1
                while j < len(aggregated_events):
                    next_event = aggregated_events[j]
                    action = next_event.get("raw_action")
                    
                    if action == "type_text":
                        text_parts.append(next_event.get("semantic_role", ""))
                        events_to_merge.append(next_event)
                        combined_text = "".join(text_parts)
                        combined_lower = combined_text.lower()
                        
                        if combined_lower in global_uia_texts_map:
                            best_match_idx = j
                            best_combined_text = global_uia_texts_map[combined_lower]
                            best_intervening = list(intervening_events)
                            best_events_to_merge = list(events_to_merge)
                    elif action in ["mouse_click", "mouse_move", "mouse_hover", "wait"]:
                        intervening_events.append(next_event)
                    else:
                        break
                    
                    j += 1
                
                if best_match_idx != -1:
                    final_events.extend(best_intervening)
                    merged_event = best_events_to_merge[0].copy()
                    merged_event["semantic_role"] = best_combined_text
                    fallback_events = []
                    for e in best_events_to_merge:
                        fallback_events.extend(e.get("fallback_events", []))
                    merged_event["fallback_events"] = fallback_events
                    final_events.append(merged_event)
                    i = best_match_idx
                else:
                    single_text = event.get("semantic_role", "")
                    single_lower = single_text.lower()
                    if single_lower in global_uia_texts_map and single_text != global_uia_texts_map[single_lower]:
                        event["semantic_role"] = global_uia_texts_map[single_lower]
                    final_events.append(event)
            else:
                final_events.append(event)
                
            i += 1

        return final_events

    def _flush_session(self, session: List[Dict[str, Any]], output_list: List[Dict[str, Any]]) -> None:
        if not session:
            return

        has_key_input = any(e.get("raw_action") in ["key_down", "key_press", "type_text", "key_combo"] for e in session)
        if not has_key_input:
            output_list.extend(session)
            session.clear()
            return

        # --- 不要なログのフィルタリング ---
        filtered_session = []
        last_text = None
        for item in session:
            action = item.get("raw_action", "")
            role_lower = str(item.get("semantic_role", "")).lower()
            
            app_ctx = item.get("app_context") or item.get("AppSpecificContext") or item.get("appSpecificContext") or {}
            current_text = app_ctx.get("value") or app_ctx.get("text") or ""
            
            diff_val = 0.0
            diff_str = item.get("diff_val") or item.get("diffRatio") or item.get("Diff") or item.get("diff")
            if isinstance(diff_str, (int, float)):
                diff_val = float(diff_str)
            elif isinstance(diff_str, str):
                try:
                    diff_val = float(diff_str.replace("%", "").strip())
                except ValueError:
                    pass

            is_essential_key = role_lower in ["space", "backspace", "delete", "enter", "tab", "esc"]
            is_shortcut = action == "key_combo" and any(mod in role_lower for mod in ["ctrl", "alt", "win", "cmd"])
            is_ime_toggle = "+" in role_lower and any(k in role_lower for k in ["space", "grave", "kanji"])
            
            is_valid = False
            
            # 1. テキストエリアに変更がある場合
            if last_text is None:
                is_valid = True
            elif current_text != last_text and current_text != "":
                is_valid = True
            # 2. 画面に差分がある場合
            elif diff_val >= 0.1:
                is_valid = True
            # 3. IME切り替えやコピーなどの特殊操作の場合
            elif is_essential_key or is_shortcut or is_ime_toggle:
                is_valid = True
            # 4. 通常の文字入力で差分が0.0%になるケースを救済するため、
            #    actionがkey_press等で、role_lowerが1文字の場合は有効とする
            elif action in ["key_press", "key_down"] and len(role_lower) == 1:
                is_valid = True
            elif action == "key_combo" and "+" in role_lower:
                # shift+w などのコンボキーも有効とする
                is_valid = True
                
            if is_valid:
                filtered_session.append(item)
                if current_text != "":
                    last_text = current_text

        if not filtered_session:
            session.clear()
            return
            
        session = filtered_session
        # ----------------------------------

        if len(session) == 1:
            single_event = session[0]
            action = single_event.get("raw_action")
            role_lower = str(single_event.get("semantic_role", "")).lower()
            
            if action == "uia_scan":
                output_list.append(single_event)
                session.clear()
                return

            diff_val = 0.0
            diff_str = single_event.get("diff_val") or single_event.get("diffRatio") or single_event.get("Diff") or single_event.get("diff")
            if isinstance(diff_str, (int, float)):
                diff_val = float(diff_str)
            elif isinstance(diff_str, str):
                try:
                    diff_val = float(diff_str.replace("%", "").strip())
                except ValueError:
                    pass

            is_essential_key = role_lower in ["space", "backspace", "delete", "enter", "tab", "esc"]
            is_shortcut = action == "key_combo" and any(mod in role_lower for mod in ["ctrl", "alt", "win", "cmd"])
            is_garbage_combo = action == "key_combo" and not is_shortcut and len(role_lower.split("+")) >= 3
            
            if is_garbage_combo:
                session.clear()
                return

            if diff_val < 0.1 and not is_essential_key and not is_shortcut:
                session.clear()
                return
                
            output_list.append(single_event)
            session.clear()
            return

        trailing_events = []
        while session:
            last_event = session[-1]
            action = last_event.get("raw_action", "")
            role_lower = str(last_event.get("semantic_role", "")).lower()
            is_special = action in ["key_down", "key_press"] and (
                role_lower.startswith("key.") or 
                role_lower in ["enter", "tab", "esc", "up", "down", "left", "right"]
            )
            is_uia = action == "uia_scan"
            
            if is_special or is_uia:
                trailing_events.insert(0, session.pop())
            else:
                break

        if not session:
            output_list.extend(trailing_events)
            return

        fallback_text = ""
        current_chunk = ""
        current_ime_state = None
        
        ignore_exact_keys = {"tab", "enter", "delete", "esc", "shift", "ctrl", "alt", "win", "cmd"}
        ignore_modifiers = ["shift", "ctrl", "alt", "win", "cmd"]
        
        for item in session:
            action = item.get("raw_action", "")
            role = str(item.get("semantic_role", ""))
            r_lower = role.lower()
            ime_active = item.get("ime_active", False)
            
            is_combo = action == "key_combo" or "+" in r_lower
            char = ""
            
            if is_combo and "shift" in r_lower:
                parts = r_lower.split("+")
                if len(parts) == 2 and len(parts[1]) == 1 and parts[1].isalpha():
                    char = parts[1].upper()
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
                elif r_lower in ignore_exact_keys or r_lower.startswith("key."):
                    continue
                elif len(r_lower) > 1 and any(mod in r_lower for mod in ignore_modifiers):
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
                try:
                    from core.recorder.romaji_converter import to_hiragana
                    current_chunk = to_hiragana(current_chunk)
                except ImportError:
                    pass
            fallback_text += current_chunk

        def _extract_search_query(url_or_text: str) -> tuple[Optional[str], bool]:
            if not url_or_text.startswith("http"):
                if ".com" in url_or_text or ".co.jp" in url_or_text or ".net" in url_or_text or ".org" in url_or_text:
                    return None, False
                return url_or_text, False
            try:
                parsed = urllib.parse.urlparse(url_or_text)
                qs = urllib.parse.parse_qs(parsed.query)
                if 'q' in qs: return qs['q'][0], True
                elif 'p' in qs: return qs['p'][0], True
                elif 'text' in qs: return qs['text'][0], True
            except Exception:
                pass
            return None, False

        uia_candidates = []
        confirmed_queries = []
        
        all_events_in_session = session + trailing_events
        for item in reversed(all_events_in_session):
            if item.get("raw_action") == "uia_scan":
                uia_info = item.get("content", {}).get("uia_info", {})
                val = uia_info.get("value") or uia_info.get("name")
                if val and len(str(val).strip()) > 0:
                    extracted, is_url_query = _extract_search_query(str(val).strip())
                    if extracted:
                        if is_url_query and extracted not in confirmed_queries:
                            confirmed_queries.append(extracted)
                        elif not is_url_query and extracted not in uia_candidates:
                            uia_candidates.append(extracted)

            app_ctx = item.get("app_context") or item.get("AppSpecificContext") or item.get("appSpecificContext")
            if isinstance(app_ctx, dict):
                ctrl_type = str(app_ctx.get("control_type", "")).lower()
                if "button" in ctrl_type or "window" in ctrl_type or "listitem" in ctrl_type:
                    continue
                    
                val = app_ctx.get("value") or app_ctx.get("text") or app_ctx.get("url")
                if val and len(str(val).strip()) > 0:
                    extracted, is_url_query = _extract_search_query(str(val).strip())
                    if extracted:
                        if is_url_query and extracted not in confirmed_queries:
                            confirmed_queries.append(extracted)
                        elif not is_url_query and extracted not in uia_candidates:
                            uia_candidates.append(extracted)

        uia_rescued_text = ""
        if confirmed_queries:
            uia_rescued_text = confirmed_queries[0]
        elif uia_candidates:
            if fallback_text:
                best_candidate = None
                best_ratio = -1.0
                fb_lower = fallback_text.lower()
                
                for cand in uia_candidates:
                    cand_lower = cand.lower()
                    
                    if fb_lower == cand_lower:
                        ratio = 1.2
                    elif fb_lower in cand_lower:
                        ratio = 1.0
                    else:
                        ratio = difflib.SequenceMatcher(None, fb_lower, cand_lower).ratio()
                        if cand_lower and fb_lower.startswith(cand_lower[:3]):
                            ratio += 0.2
                        ratio = min(0.99, ratio)
                        
                    if ratio > best_ratio:
                        best_ratio = ratio
                        best_candidate = cand
                
                if best_ratio >= 0.25:
                    uia_rescued_text = best_candidate
            else:
                uia_rescued_text = uia_candidates[0]

        has_suggest_selection = any(
            item.get("raw_action") in ["key_down", "key_press"] and 
            str(item.get("semantic_role", "")).lower() in ["tab", "down", "up"]
            for item in session + trailing_events
        )

        any_ime_active = any(item.get("ime_active", False) for item in session)

        if confirmed_queries:
            final_text = uia_rescued_text
        elif not any_ime_active and fallback_text and not has_suggest_selection:
            final_text = fallback_text
        else:
            final_text = uia_rescued_text if uia_rescued_text else fallback_text

        if final_text:
            if output_list and output_list[-1].get("raw_action") == "type_text" and output_list[-1].get("semantic_role") == final_text:
                session.clear()
                return

            if output_list and output_list[-1].get("raw_action") == "type_text":
                prev_text = str(output_list[-1].get("semantic_role", ""))
                prev_text_lower = prev_text.lower()
                final_text_lower = final_text.lower()
                
                is_suffix_or_sub = False
                if len(final_text_lower) < len(prev_text_lower):
                    if prev_text_lower.endswith(final_text_lower) or final_text_lower in prev_text_lower:
                        is_suffix_or_sub = True
                
                if not is_suffix_or_sub and any_ime_active:
                    if len(final_text_lower) <= 3 and final_text_lower.isascii() and final_text_lower.isalpha():
                        is_suffix_or_sub = True
                        
                if is_suffix_or_sub:
                    session.clear()
                    return

            representative_event = session[0].copy()
            representative_event["raw_action"] = "type_text"
            representative_event["semantic_role"] = final_text
            representative_event["ui_type"] = "text"
            representative_event["fallback_events"] = [item.get("event_id") for item in session if item.get("event_id")]
            representative_event["diff_val"] = 0.0
            
            output_list.append(representative_event)
        else:
            output_list.extend(session)

        trailing_special_keys = []
        
        for e in trailing_events:
            if e.get("raw_action") == "uia_scan":
                continue
                
            role_lower = str(e.get("semantic_role", "")).lower()
            
            if final_text:
                if role_lower == "tab":
                    continue

            trailing_special_keys.append(e)

        output_list.extend(trailing_special_keys)
        session.clear()