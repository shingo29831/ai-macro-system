# Role: 連続するキー入力ログをセッションとして集約し、UIA確定テキストを優先適用して精度の高い文字列マクロアクションを生成するモジュール
from typing import List, Dict, Any, Optional
import logging
import urllib.parse

logger = logging.getLogger(__name__)

class TypingSessionAggregator:
    """
    分断されたキーボード入力イベントを分析・集約し、最良のコンテキスト（UIA > OCR > Raw Key）
    を選択して1つのまとまった type_text アクションに最適化するクラス。
    """
    def __init__(self, session_timeout_ms: int = 2000):
        self.session_timeout_ms = session_timeout_ms



    def aggregate_events(self, raw_events: List[Dict[str, Any]]) -> List[Dict[str, Any]]:
        """
        イベントリスト全体を走査し、キー入力をセッションとしてまとめた新しいイベントリストを返す。
        """
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

        # 全イベントからUIAテキストの履歴を収集（分断されたテキストの結合判定に使用）
        # 大文字小文字の違いを吸収するため、小文字をキーとする辞書を作成
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
            
            # 正しいフィールドパスからウィンドウ名を取得
            current_window = event.get("window_name", "")
            current_ts = event.get("timestamp", 0)
            
            if current_session:
                last_event = current_session[-1]
                last_window = last_event.get("window_name", "")
                last_ts = last_event.get("timestamp", 0)
                
                # 1. ウィンドウ名が変わった場合
                if current_window and last_window and current_window != last_window:
                    self._flush_session(current_session, aggregated_events)
                
                # 2. タイムアウト（時間差）によるセッション分割の導入
                elif current_ts > 0 and last_ts > 0 and (current_ts - last_ts) > self.session_timeout_ms:
                    # ただし、バックスペースの場合は直前の入力を消す意図があるため分割しない
                    if str(event.get("semantic_role", "")).lower() != "backspace":
                        self._flush_session(current_session, aggregated_events)

                # 3. 画面の大きな変化（ページ遷移など）によるセッション分割
                # ただし、連続入力中（500ms未満）はサジェスト表示等の画面変化とみなして分割しない
                elif _parse_diff(event.get("diff_val") or event.get("diffRatio") or event.get("Diff") or event.get("diff")) > 15.0:
                    if current_ts == 0 or last_ts == 0 or (current_ts - last_ts) > 500:
                        self._flush_session(current_session, aggregated_events)

            # 特殊キーの判定（確定や移動、削除など）
            role_lower = str(event.get("semantic_role", "")).lower()
            
            # shift+文字 などの大文字入力コンボは特殊キー（分断対象）ではなく文字入力の一部とみなす
            is_shift_char = "+" in role_lower and "shift" in role_lower and len(role_lower.split("+")[-1]) == 1
            
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
            
            # コンボキー（shift+space等）はIME切り替えやショートカットとみなし、セッションを継続させる
            is_ime_toggle = "+" in role_lower and any(k in role_lower for k in ["space", "grave", "kanji"])
            is_typing_combo = (action == "key_combo" and not is_shift_char) or is_ime_toggle

            # IMEオフのEnterは送信/実行を意味するため、ここでセッションを区切る
            if role_lower == "enter" and not ime_active:
                current_session.append(event)
                self._flush_session(current_session, aggregated_events)
                continue

            # セッションの継続条件: 文字入力、UIAスキャン、確定キー(Enter/Tab)、または入力中のコンボキー
            if is_text_input or is_uia_scan or is_confirm_key or is_typing_combo:
                current_session.append(event)
                continue
            elif is_mouse_move:
                # マウス移動はセッションに追加せず、フラッシュもさせない（無視してスキップ）
                aggregated_events.append(event)
                continue

            # クリックやTab以外の特殊キーなどが来た場合、セッションをフラッシュ
            if current_session:
                self._flush_session(current_session, aggregated_events)

            aggregated_events.append(event)

        # ループ終了時に残っているセッションをフラッシュ
        if current_session:
            self._flush_session(current_session, aggregated_events)

        # 後処理: UIAテキスト履歴を利用した分断セッションの結合
        # 例: type_text("he") -> click -> type_text("llo world") を
        # click -> type_text("hello world") に結合・順序補正する
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
                        
                       # 結合したテキストが global_uia_texts_map に存在するかチェック（大文字小文字を区別しない）
                        combined_lower = combined_text.lower()
                        if combined_lower in global_uia_texts_map:
                            best_match_idx = j
                            best_combined_text = global_uia_texts_map[combined_lower]
                            best_intervening = list(intervening_events)
                            best_events_to_merge = list(events_to_merge)
                    elif action in ["mouse_click", "mouse_move", "mouse_hover", "wait"]:
                        intervening_events.append(next_event)
                    else:
                        # キー入力以外の操作（特殊キーなど）が挟まったら結合を諦める
                        break
                    
                    j += 1
                
                if best_match_idx != -1:
                    logger.info(f"[TypingAggregator] UIA履歴を利用して分断されたテキストを結合・順序補正します: '{best_combined_text}'")
                    # 間にあったクリック等を先に出力（ユーザーの意図した順序）
                    final_events.extend(best_intervening)
                    
                    # 結合されたテキストイベントを出力
                    merged_event = best_events_to_merge[0].copy()
                    merged_event["semantic_role"] = best_combined_text
                    fallback_events = []
                    for e in best_events_to_merge:
                        fallback_events.extend(e.get("fallback_events", []))
                    merged_event["fallback_events"] = fallback_events
                    
                    final_events.append(merged_event)
                    i = best_match_idx
                else:
                    # 結合しなかった場合でも、単一のテキストとしてUIA履歴に大文字小文字の正解があれば補正する
                    single_text = event.get("semantic_role", "")
                    single_lower = single_text.lower()
                    if single_lower in global_uia_texts_map and single_text != global_uia_texts_map[single_lower]:
                        corrected_text = global_uia_texts_map[single_lower]
                        logger.info(f"[TypingAggregator] UIA履歴を利用して大文字小文字を補正します: '{single_text}' -> '{corrected_text}'")
                        event["semantic_role"] = corrected_text
                    final_events.append(event)
            else:
                final_events.append(event)
                
            i += 1

        return final_events



    def _flush_session(self, session: List[Dict[str, Any]], output_list: List[Dict[str, Any]]) -> None:
        """
        蓄積されたセッション内のイベントを評価し、単一の最適な文字列入力イベントに置換して出力リストへ追加する。
        """
        import difflib

        if not session:
            return

        # uia_scanのみのセッションなど、実質的なキー入力がない場合はそのまま出力して終了
        has_key_input = any(e.get("raw_action") in ["key_down", "key_press", "type_text", "key_combo"] for e in session)
        if not has_key_input:
            output_list.extend(session)
            session.clear()
            return

        # 単一のイベントの場合のフィルタリング処理
        if len(session) == 1:
            single_event = session[0]
            action = single_event.get("raw_action")
            role_lower = str(single_event.get("semantic_role", "")).lower()
            
            if action == "uia_scan":
                output_list.append(single_event)
                session.clear()
                return

            # 画面変化率（Diff）を取得
            diff_val = 0.0
            diff_str = single_event.get("diff_val") or single_event.get("diffRatio") or single_event.get("Diff") or single_event.get("diff")
            if isinstance(diff_str, (int, float)):
                diff_val = float(diff_str)
            elif isinstance(diff_str, str):
                try:
                    diff_val = float(diff_str.replace("%", "").strip())
                except ValueError:
                    pass

            # 削除キーや確定キーはDiffが小さくても保持する
            is_essential_key = role_lower in ["space", "backspace", "delete", "enter", "tab", "esc"]
            
            # ショートカットキーの判定（ctrl, alt, win, cmdを含む）
            is_shortcut = action == "key_combo" and any(mod in role_lower for mod in ["ctrl", "alt", "win", "cmd"])
            
            # ゴミコンボキーの判定（shiftのみで3キー以上同時押しなど、例: shift+h+k+space）
            is_garbage_combo = action == "key_combo" and not is_shortcut and len(role_lower.split("+")) >= 3
            
            if is_garbage_combo:
                logger.info(f"[TypingAggregator] 無効なタイピングコンボキーを破棄します: {role_lower}")
                session.clear()
                return

            # Diffが極端に小さい（0.1%未満）場合、必須キーやショートカットでなければ誤入力として破棄
            if diff_val < 0.1 and not is_essential_key and not is_shortcut:
                logger.info(f"[TypingAggregator] 画面変化がない({diff_val}%)ため、不要なキー入力として破棄します: {role_lower}")
                session.clear()
                return
                
            output_list.append(single_event)
            session.clear()
            return

        target_event_id = session[0].get("event_id", "unknown")
        logger.info(f"[TypingAggregator] セッション集約を開始 (イベント数: {len(session)}, 開始ID: {target_event_id})")

        # セッションの末尾にある特殊キー（Enter, Tabなど）や uia_scan を抽出して分離する
        # これらは type_text の後に独立したキーイベントとして実行させるため
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
            # すべて特殊キーやuia_scanだった場合はそのまま出力して終了
            output_list.extend(trailing_events)
            return

        # -------------------------------------------------------------------------
        # 優先順位 2 (事前計算): 生キーログ結合 ＋ ローマ字/かな変換 (Fallback Text)
        # UIAレスキューの候補選択時の類似度スコアリングにも使用する
        # -------------------------------------------------------------------------
        fallback_text = ""
        current_chunk = ""
        current_ime_state = None
        
        # 制御キーや修飾キーの除外リスト
        ignore_exact_keys = {"tab", "enter", "delete", "esc", "shift", "ctrl", "alt", "win", "cmd"}
        ignore_modifiers = ["shift", "ctrl", "alt", "win", "cmd"]
        
        for item in session:
            action = item.get("raw_action", "")
            role = str(item.get("semantic_role", ""))
            r_lower = role.lower()
            ime_active = item.get("ime_active", False)
            
            is_combo = action == "key_combo" or "+" in r_lower
            char = ""
            
            # shift+文字 の場合は大文字として抽出する
            if is_combo and "shift" in r_lower and len(r_lower.split("+")[-1]) == 1:
                char = r_lower.split("+")[-1].upper()
                is_combo = False
                
            if is_combo:
                # コンボキーはIME切り替えやショートカットとみなし、文字としては結合しない
                # ただし、IME状態の切り替えフラグとしては機能させるため、ここでチャンクを区切る
                if current_ime_state is not None:
                    if current_ime_state and current_chunk:
                        try:
                            from core.recorder.romaji_converter import to_hiragana
                            current_chunk = to_hiragana(current_chunk)
                        except ImportError:
                            pass
                    fallback_text += current_chunk
                    current_chunk = ""
                    # コンボキー自体の ime_active はあてにならないことがあるため、次の文字入力で更新させる
                    current_ime_state = None
                continue
                
            if is_combo:
                # コンボキーはIME切り替えやショートカットとみなし、文字としては結合しない
                # ただし、IME状態の切り替えフラグとしては機能させるため、ここでチャンクを区切る
                if current_ime_state is not None:
                    if current_ime_state and current_chunk:
                        try:
                            from core.recorder.romaji_converter import to_hiragana
                            current_chunk = to_hiragana(current_chunk)
                        except ImportError:
                            pass
                    fallback_text += current_chunk
                    current_chunk = ""
                    # コンボキー自体の ime_active はあてにならないことがあるため、次の文字入力で更新させる
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
                # IME状態が変わったら、これまでのチャンクを処理して fallback_text に追加
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

        # 最後のチャンクを処理
        if current_chunk:
            if current_ime_state:
                try:
                    from core.recorder.romaji_converter import to_hiragana
                    current_chunk = to_hiragana(current_chunk)
                except ImportError:
                    pass
            fallback_text += current_chunk

        # -------------------------------------------------------------------------
        # 優先順位 1: UIA（アプリ固有コンテキスト）からの確定文字の一括レスキュー
        # -------------------------------------------------------------------------
        def _extract_search_query(url_or_text: str) -> tuple[Optional[str], bool]:
            """テキストと、それがURLからの抽出（確定クエリ）かどうかのフラグを返す"""
            if not url_or_text.startswith("http"):
                # URLっぽい文字列（.com/ などを含む）はサジェストとみなして弾く
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
            # 検索クエリが含まれない純粋なURLの場合は、サジェストの誤検知とみなして採用しない
            return None, False

        uia_candidates = []
        confirmed_queries = []
        
        # 抽出対象は session 本体と、分離した trailing_events (Enter, Tab, uia_scan) の両方
        all_events_in_session = session + trailing_events
        for item in reversed(all_events_in_session):
            # パターンA: uia_scan イベントからの抽出（タブ補完後の文字など）
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

            # パターンB: app_context からの抽出（Enter確定時の文字など）
            app_ctx = item.get("app_context") or item.get("AppSpecificContext") or item.get("appSpecificContext")
            if isinstance(app_ctx, dict):
                ctrl_type = str(app_ctx.get("control_type", "")).lower()
                # ButtonControl や WindowControl などのテキストは無視する（「キャンセル」等の誤検知防止）
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
            # URLから抽出された検索クエリは確定済みとみなし、類似度比較をスキップして最優先で採用する
            uia_rescued_text = confirmed_queries[0]
            logger.info(f"[TypingAggregator] URLから確定済みの検索クエリ '{uia_rescued_text}' を抽出・最優先で採用しました")
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
                        # 漢字変換を考慮し、候補の先頭部分が一致していればスコアを底上げ
                        if cand_lower and fb_lower.startswith(cand_lower[:3]):
                            ratio += 0.2
                        # 部分一致のスコアが完全包含(1.0)を上回らないように上限を設定
                        ratio = min(0.99, ratio)
                        
                    if ratio > best_ratio:
                        best_ratio = ratio
                        best_candidate = cand
                
                # 類似度が極端に低い場合（例: 0.25未満）は、全く無関係なテキスト（プレースホルダー等）とみなして採用しない
                # 短い文字列でのスペース一致等による誤爆を防ぐため閾値を高めに設定
                if best_ratio >= 0.25:
                    # キー入力とUIAの類似度が高い場合、UIA側の大文字小文字（Shift/CapsLockの状態が反映された正確なテキスト）を採用する
                    uia_rescued_text = best_candidate
                    logger.info(f"[TypingAggregator] UIAレスキュー成功: 候補の中から類似度最大({best_ratio:.2f})の '{uia_rescued_text}' を採用")
                else:
                    # キー入力（例: honnyaku）と画面テキスト（例: Hello , world）が全く異なる場合は、
                    # 画面テキストは前回の入力の残骸（ゴミ）とみなして破棄し、実際のキー入力を優先する
                    logger.info(f"[TypingAggregator] UIAレスキュー候補はありましたが、入力キーとの類似度が低すぎるため破棄します (best_ratio: {best_ratio:.2f}, cand: '{best_candidate}')")
            else:
                # fallback_text が空（特殊キーのみなど）の場合は、最初に見つかった候補（最新）を採用
                uia_rescued_text = uia_candidates[0]
                logger.info(f"[TypingAggregator] UIAレスキュー成功(fallbackなし): '{uia_rescued_text}' を採用")

        # 最終採用テキストの決定 (UIA > Fallback Key Log)
        has_suggest_selection = any(
            item.get("raw_action") in ["key_down", "key_press"] and 
            str(item.get("semantic_role", "")).lower() in ["tab", "down", "up"]
            for item in session + trailing_events
        )

        any_ime_active = any(item.get("ime_active", False) for item in session)

        if confirmed_queries:
            final_text = uia_rescued_text
            logger.info(f"[TypingAggregator] 確定クエリ '{final_text}' を最優先で採用します")
        elif not any_ime_active and fallback_text and not has_suggest_selection:
            # IMEオフでサジェスト選択操作もない場合はユーザーの生入力を最優先（サジェストの自動誤採用を防止）
            final_text = fallback_text
            logger.info(f"[TypingAggregator] IMEオフかつサジェスト選択なしのため、生入力 '{final_text}' を優先採用します")
        else:
            final_text = uia_rescued_text if uia_rescued_text else fallback_text

        if final_text:
            # 1. 完全一致の重複スキップ
            if output_list and output_list[-1].get("raw_action") == "type_text" and output_list[-1].get("semantic_role") == final_text:
                logger.info(f"[TypingAggregator] 重複する TYPE_TEXT ('{final_text}') をスキップします。")
                session.clear()
                return

            # 2. UIAレスキュー残骸（遅延による末尾の物理キー入力漏れ）の自動間引き・スキップ処理
            # 例: prev="hello world" に対して、不必要な細切れセッションから curr="rld" が生じた場合、スキップする
            if output_list and output_list[-1].get("raw_action") == "type_text":
                prev_text = str(output_list[-1].get("semantic_role", ""))
                prev_text_lower = prev_text.lower()
                final_text_lower = final_text.lower()
                
                is_suffix_or_sub = False
                if len(final_text_lower) < len(prev_text_lower):
                    if prev_text_lower.endswith(final_text_lower) or final_text_lower in prev_text_lower:
                        is_suffix_or_sub = True
                
                # 日本語変換確定後のキーストローク残骸（例: "google翻訳" に対する "h" 等）をスキップ
                if not is_suffix_or_sub and any_ime_active:
                    # 確定テキストがひらがな・漢字交じりで、現在文字列が短いアルファベット・部分キーの場合
                    if len(final_text_lower) <= 3 and final_text_lower.isascii() and final_text_lower.isalpha():
                        # ローマ字配列や変換確定時の遅延キーストローク残骸とみなす
                        is_suffix_or_sub = True
                        
                if is_suffix_or_sub:
                    logger.info(f"[TypingAggregator] UIAレスキューの残骸キー入力 '{final_text}' (直前: '{prev_text}') を自動スキップします。")
                    session.clear()
                    return

            # 集約された1つの代表イベントを生成
            representative_event = session[0].copy()
            representative_event["raw_action"] = "type_text"
            representative_event["semantic_role"] = final_text
            representative_event["ui_type"] = "text"
            
            # 実行エンジンの自己修復・フォールバック用にセッション内の全イベントIDを記録
            representative_event["fallback_events"] = [item.get("event_id") for item in session if item.get("event_id")]
            
            # 不要な1文字ごとのOCR画像パスやノイズをクリア
            representative_event["diff_val"] = 0.0
            
            output_list.append(representative_event)
            logger.info(f"[TypingAggregator] セッションを1つのアクションへ統合完了: TYPE_TEXT -> '{final_text}'")
        else:
            logger.warning(f"[TypingAggregator] セッション ({target_event_id}) から有効なテキストを抽出できませんでした。生イベントを復元します。")
            output_list.extend(session)

        # 分離しておいた末尾の特殊キーイベントを復元して追加（uia_scanは実行アクションではないため除外）
        trailing_special_keys = []
        
        for e in trailing_events:
            if e.get("raw_action") == "uia_scan":
                continue
                
            role_lower = str(e.get("semantic_role", "")).lower()
            
            if final_text:
                # 文字入力が確定した場合、それに付随する tab(補完) は不要なため除外する
                # ※ enter は検索実行などのトリガーになるため除外せずに残す
                if role_lower == "tab":
                    logger.info("[TypingAggregator] 文字入力に付随する 'tab' (補完操作) をマクロから除外します")
                    continue

            trailing_special_keys.append(e)

        output_list.extend(trailing_special_keys)
        
        session.clear()