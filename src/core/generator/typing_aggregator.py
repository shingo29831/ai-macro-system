"""Module: @role: 一連のキー入力・IME入力・UIAイベントを時系列解析し、単一のテキスト入力やキー操作セッションとして集約する。"""

import difflib
import logging
import re
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
                
                diff_val = _parse_diff(event.get("diff_val") or event.get("diffRatio") or event.get("Diff") or event.get("diff"))
                time_diff = current_ts - last_ts if current_ts > 0 and last_ts > 0 else 0
                
                # Why: サジェスト展開による画面差分や要素名変化で入力セッションが細切れになるのを防止
                is_ongoing_typing = action in ["key_down", "key_press", "key_combo"] and time_diff < 1500
                if current_window and last_window and current_window != last_window:
                    self._flush_session(current_session, aggregated_events, future_events=raw_events[i:])
                elif not is_ongoing_typing and diff_val > 15.0 and time_diff > 500:
                    self._flush_session(current_session, aggregated_events, future_events=raw_events[i:])
                elif time_diff > 3000:
                    self._flush_session(current_session, aggregated_events, future_events=raw_events[i:])
                elif not is_ongoing_typing and last_element and curr_element and last_element != curr_element:
                    if diff_val > 5.0 or time_diff > 2000:
                        self._flush_session(current_session, aggregated_events, future_events=raw_events[i:])

            role_lower = str(event.get("semantic_role", "")).lower()
            
            is_shift_char = action == "key_combo" and "shift" in role_lower and len(role_lower.split("+")) == 2 and len(role_lower.split("+")[1]) == 1
            
            # Why: 修飾キーや機能キーが文字入力セッションへ誤混入するのを防止
            special_key_names = {
                "enter", "tab", "esc", "escape", "up", "down", "left", "right",
                "left_click", "right_click", "middle_click",
                "win", "cmd", "windows", "ctrl", "alt", "shift", "caps_lock",
                "home", "end", "page_up", "page_down", "insert", "delete", "print_screen",
                "f1", "f2", "f3", "f4", "f5", "f6", "f7", "f8", "f9", "f10", "f11", "f12"
            }
            is_special_key = action in ["key_down", "key_press", "key_combo"] and (
                role_lower.startswith("key.") or 
                role_lower in special_key_names or
                ("+" in role_lower and not is_shift_char)
            )
            is_text_input = action in ["type_text", "key_down", "key_press", "key_combo"] and not is_special_key
            is_uia_scan = action == "uia_scan"
            is_confirm_key = is_special_key and role_lower in ["enter", "tab"]
            ime_active = event.get("ime_active", False)
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
            elif is_mouse_move:
                # Why: タイピング継続中の微小なマウス移動はセッションを分断させず破棄
                if current_session:
                    continue
                aggregated_events.append(event)
                continue

            if current_session:
                self._flush_session(current_session, aggregated_events, future_events=raw_events[i:])

            aggregated_events.append(event)

        if current_session:
            self._flush_session(current_session, aggregated_events, future_events=None)

        final_events = []
        i = 0
        while i < len(aggregated_events):
            event = aggregated_events[i]
            
            if event.get("raw_action") == "type_text":
                events_to_merge = [event]
                intervening_events = []
                last_valid_text = event.get("semantic_role", "")
                
                j = i + 1
                while j < len(aggregated_events):
                    next_event = aggregated_events[j]
                    action = next_event.get("raw_action")
                    
                    if action == "type_text":
                        curr_win = event.get("window_name", "")
                        next_win = next_event.get("window_name", "")
                        
                        curr_ctx = event.get("app_context") or event.get("AppSpecificContext") or event.get("appSpecificContext") or {}
                        next_ctx = next_event.get("app_context") or next_event.get("AppSpecificContext") or next_event.get("appSpecificContext") or {}
                        
                        curr_elem = curr_ctx.get("element_name", "")
                        next_elem = next_ctx.get("element_name", "")
                        
                        # 同じウィンドウ・同じ要素に対する連続した入力は、最後のテキストで上書きマージする
                        if curr_win == next_win and curr_elem == next_elem:
                            events_to_merge.append(next_event)
                            last_valid_text = next_event.get("semantic_role", "")
                        else:
                            break
                    elif action in ["mouse_move", "mouse_hover", "mouse_click", "mouse_scroll"]:
                        next_ctx = next_event.get("app_context") or next_event.get("AppSpecificContext") or next_event.get("appSpecificContext") or {}
                        next_elem = next_ctx.get("element_name", "")
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
                    fallback_events = []
                    for e in events_to_merge:
                        fallback_events.extend(e.get("fallback_events", []))
                    merged_event["fallback_events"] = fallback_events
                    final_events.append(merged_event)
                    i = j - 1
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

    def _flush_session(
        self, 
        session: List[Dict[str, Any]], 
        output_list: List[Dict[str, Any]], 
        future_events: Optional[List[Dict[str, Any]]] = None
    ) -> None:
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
        last_ime_active = None
        
        for item in session:
            action = item.get("raw_action", "")
            role_lower = str(item.get("semantic_role", "")).lower()
            ime_active = item.get("ime_active", False)
            
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
            is_combo = action == "key_combo"
            is_shortcut = is_combo and any(mod in role_lower for mod in ["ctrl", "alt", "win", "cmd"])
            is_ime_toggle = "+" in role_lower and any(k in role_lower for k in ["space", "grave", "kanji"])
            
            # IMEトグルキー自体はマクロコマンドとして不要なので除外
            if is_ime_toggle:
                last_ime_active = ime_active
                continue
            
            is_valid = False
            
            has_text_change = last_text is not None and current_text != last_text and current_text != ""
            has_screen_diff = diff_val >= 0.1
            has_ime_change = last_ime_active is not None and ime_active != last_ime_active
            
            if is_combo:
                if has_text_change or has_ime_change:
                    is_valid = True
                elif has_screen_diff:
                    # 画面差分がある場合でも、修飾キーを含まない3キー以上のコンボはノイズとみなす
                    if not is_shortcut and len(role_lower.split("+")) >= 3:
                        is_valid = False
                    else:
                        is_valid = True
                elif is_shortcut:
                    # ctrl+c などのショートカットは画面変化がなくても有効とする
                    is_valid = True
            else:
                if last_text is None:
                    is_valid = True
                elif has_text_change:
                    is_valid = True
                elif has_screen_diff:
                    is_valid = True
                elif is_essential_key:
                    is_valid = True
                elif action in ["key_press", "key_down"] and len(role_lower) == 1:
                    is_valid = True

            if is_valid:
                filtered_session.append(item)
                if current_text != "":
                    last_text = current_text
                    
            last_ime_active = ime_active

        if not filtered_session:
            session.clear()
            return
            
        session[:] = filtered_session
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
        
        ignore_exact_keys = {"tab", "enter", "delete", "esc", "shift", "ctrl", "alt", "win", "cmd", "left_click", "right_click", "middle_click"}
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
                parsed = urllib.parse.urlsplit(url_or_text)
                qs = urllib.parse.parse_qs(parsed.query)
                for key in ["q", "query", "p", "wd", "word", "search_query", "text"]:
                    if key in qs and qs[key]:
                        # Why: URLエンコードされた日本語クエリを安全に復元
                        return urllib.parse.unquote_plus(qs[key][0]), True
            except Exception:
                pass
            return None, False

        def _extract_query_from_title(title: str) -> Optional[str]:
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

        confirmed_queries = []
        latest_uia_text = ""
        
        all_events_in_session = session + trailing_events
        for item in reversed(all_events_in_session):
            if item.get("raw_action") == "uia_scan":
                uia_info = item.get("content", {}).get("uia_info", {})
                val = uia_info.get("value") or uia_info.get("name")
                candidates = uia_info.get("candidates") or []
                # Why: Tab補完時は展開された候補リストの先頭アイテムを優先採用
                if candidates and has_suggest_selection and not val:
                    val = candidates[0]
                if val and len(str(val).strip()) > 0:
                    extracted, is_url_query = _extract_search_query(str(val).strip())
                    if extracted:
                        if is_url_query and extracted not in confirmed_queries:
                            confirmed_queries.append(extracted)
                        elif not is_url_query and not latest_uia_text:
                            latest_uia_text = extracted

            app_ctx = item.get("app_context") or item.get("AppSpecificContext") or item.get("appSpecificContext")
            if isinstance(app_ctx, dict):
                elem_name = str(app_ctx.get("element_name") or "")
                title_q = _extract_query_from_title(elem_name)
                if title_q and title_q not in confirmed_queries:
                    confirmed_queries.append(title_q)

                ctrl_type = str(app_ctx.get("control_type", "")).lower()
                if "button" in ctrl_type or "window" in ctrl_type or "listitem" in ctrl_type:
                    continue
                    
                val = app_ctx.get("value")
                if not val or str(val).strip() == "":
                    val = app_ctx.get("text")
                if not val or str(val).strip() == "":
                    val = app_ctx.get("url")
                    
                if val and len(str(val).strip()) > 0:
                    extracted, is_url_query = _extract_search_query(str(val).strip())
                    if extracted:
                        if is_url_query and extracted not in confirmed_queries:
                            confirmed_queries.insert(0, extracted)
                        # Why: 一番最後に確定された最新のUIA要素文字列のみを採用し入力途中の巻き戻りを防止
                        elif not is_url_query and not latest_uia_text:
                            latest_uia_text = extracted

        confirmed_future_queries = []
        if future_events:
            for f_evt in future_events[:15]:
                f_win = f_evt.get("window_name") or f_evt.get("WindowName", "")
                title_q = _extract_query_from_title(f_win)
                if title_q and title_q not in confirmed_future_queries:
                    confirmed_future_queries.append(title_q)

                f_ctx = f_evt.get("app_context") or f_evt.get("AppSpecificContext") or f_evt.get("appSpecificContext") or {}
                for url_candidate in [f_ctx.get("url"), f_ctx.get("value"), f_ctx.get("text"), f_ctx.get("element_name")]:
                    if url_candidate and isinstance(url_candidate, str):
                        extracted, is_url_query = _extract_search_query(url_candidate)
                        if is_url_query and extracted and extracted not in confirmed_future_queries:
                            confirmed_future_queries.append(extracted)

                if f_ctx.get("query") and f_ctx["query"] not in confirmed_future_queries:
                    confirmed_future_queries.append(f_ctx["query"])

        has_suggest_selection = any(
            item.get("raw_action") in ["key_down", "key_press"] and 
            str(item.get("semantic_role", "")).lower() in ["tab", "down", "up"]
            for item in session + trailing_events
        )
        any_ime_active = any(item.get("ime_active", False) for item in session)

        # Why: 物理的な文字入力が一切ないセッション（Winキー等の単体連打）でのテキスト捏造を阻止
        has_actual_chars = any(
            str(item.get("semantic_role", "")).lower() not in ignore_exact_keys and 
            not str(item.get("semantic_role", "")).lower().startswith("key.") and
            len(str(item.get("semantic_role", ""))) == 1
            for item in session
        )

        matched_future_query = None
        current_input = (latest_uia_text or fallback_text or "").strip().lower()
        if confirmed_future_queries and (has_actual_chars or any_ime_active):
            for fq in confirmed_future_queries:
                fq_lower = fq.lower()
                matches_text = bool(current_input and (current_input in fq_lower or fq_lower.startswith(current_input)))
                # Why: Tab補完やIME変換で入力仮名と確定漢字が一致しないケースを検索遷移から救済
                matches_suggest_or_ime = (has_suggest_selection or any_ime_active) and (has_actual_chars or len(current_input) > 0)
                if not current_input or matches_text or matches_suggest_or_ime:
                    matched_future_query = fq
                    break

        if confirmed_queries:
            # Why: Tab補完やサジェストで選択された確定クエリを未確定ローマ字バッファより最優先
            final_text = confirmed_queries[0]
        elif matched_future_query:
            final_text = matched_future_query
        elif (has_suggest_selection or any_ime_active) and latest_uia_text:
            final_text = latest_uia_text
        elif latest_uia_text and fallback_text and (fallback_text in latest_uia_text or latest_uia_text in fallback_text):
            final_text = latest_uia_text
        elif not any_ime_active and fallback_text and not has_suggest_selection:
            final_text = fallback_text
        else:
            final_text = latest_uia_text if latest_uia_text else fallback_text

        if final_text:
            # Why: リンクが全角で記録された場合に全角半角判定を行い半角URLへ正規化
            import unicodedata
            norm_candidate = unicodedata.normalize('NFKC', final_text).strip()
            norm_lower = norm_candidate.lower()
            if norm_lower.startswith(('http://', 'https://', 'www.', 'ftp://')) or re.match(r'^[a-zA-Z0-9][-a-zA-Z0-9]*\.[a-zA-Z0-9][-a-zA-Z0-9.]*(/[^\s]*)?$', norm_lower):
                final_text = norm_candidate

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