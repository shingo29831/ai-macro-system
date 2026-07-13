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
                    self._flush_session(current_session, aggregated_events)

                # 3. コントロールの種類が大きく変わった場合
                # タイピング中はUIAのフォーカスがサジェスト等に飛ぶことが多いため、
                # ウィンドウが同じでタイムアウトしていなければ、コントロールタイプの変更だけでは分断しない。

            # 特殊キーの判定（確定や移動、削除など）
            role_lower = str(event.get("semantic_role", "")).lower()
            is_special_key = action in ["key_down", "key_press"] and (
                role_lower.startswith("key.") or 
                role_lower in ["enter", "tab", "esc", "up", "down", "left", "right"] or
                "+" in role_lower
            )
            is_text_input = action in ["type_text", "key_down", "key_press"] and not is_special_key
            is_uia_scan = action == "uia_scan"
            is_confirm_key = is_special_key and role_lower in ["enter", "tab"]
            ime_active = event.get("ime_active", False)

            is_mouse_move = action in ["mouse_move", "mouse_hover"]
            
            # コンボキー（shift+space等）はIME切り替えやショートカットとみなし、セッションを継続させる
            is_typing_combo = action == "key_combo"

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

        return aggregated_events

    def _flush_session(self, session: List[Dict[str, Any]], output_list: List[Dict[str, Any]]) -> None:
        """
        蓄積されたセッション内のイベントを評価し、単一の最適な文字列入力イベントに置換して出力リストへ追加する。
        """
        import difflib

        if not session:
            return

        # uia_scanのみのセッションなど、実質的なキー入力がない場合はそのまま出力して終了
        has_key_input = any(e.get("raw_action") in ["key_down", "key_press", "type_text"] for e in session)
        if not has_key_input:
            output_list.extend(session)
            session.clear()
            return

        # 単一のイベントで、かつ特殊な削除キーなどの場合はそのまま出力して終了
        if len(session) == 1 and str(session[0].get("semantic_role", "")).lower() not in ["space", "backspace", "delete"] and session[0].get("raw_action") != "uia_scan":
            output_list.append(session[0])
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
        any_ime_active = any(item.get("ime_active", False) for item in session)
        
        # 制御キーや修飾キーの除外リスト
        ignore_exact_keys = {"tab", "enter", "delete", "esc", "shift", "ctrl", "alt", "win", "cmd"}
        ignore_modifiers = ["shift", "ctrl", "alt", "win", "cmd"]
        
        for item in session:
            action = item.get("raw_action", "")
            role = str(item.get("semantic_role", ""))
            r_lower = role.lower()
            
            if action == "key_combo":
                # コンボキーはIME切り替えやショートカットとみなし、文字としては結合しない
                continue
                
            if r_lower == "backspace":
                fallback_text = fallback_text[:-1]
            elif r_lower == "space":
                fallback_text += " "
            elif r_lower in ignore_exact_keys or r_lower.startswith("key."):
                # 特殊キー単体はテキストとして結合しない
                continue
            elif len(r_lower) > 1 and any(mod in r_lower for mod in ignore_modifiers):
                # shift+space などのコンボキー文字列はテキストとして結合しない
                continue
            else:
                fallback_text += role

        if any_ime_active and fallback_text:
            try:
                from core.recorder.romaji_converter import to_hiragana
                fallback_text = to_hiragana(fallback_text)
                logger.debug(f"[TypingAggregator] IMEアクティブ検知: ローマ字かな変換を適用 -> '{fallback_text}'")
            except ImportError:
                logger.warning("[TypingAggregator] romaji_converter が見つからないため変換をスキップしました")

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
                    # フォールバックテキストが候補に完全に含まれる場合は最高スコア
                    if fb_lower in cand_lower:
                        ratio = 1.0
                    else:
                        ratio = difflib.SequenceMatcher(None, fb_lower, cand_lower).ratio()
                        # 漢字変換を考慮し、候補の先頭部分が一致していればスコアを底上げ
                        if cand_lower and fb_lower.startswith(cand_lower[:3]):
                            ratio += 0.2
                        
                    if ratio > best_ratio:
                        best_ratio = ratio
                        best_candidate = cand
                
                # 類似度が極端に低い場合（例: 0.15未満）は、全く無関係なテキスト（プレースホルダー等）とみなして採用しない
                if best_ratio >= 0.15:
                    uia_rescued_text = best_candidate
                    logger.info(f"[TypingAggregator] UIAレスキュー成功: 候補の中から類似度最大({best_ratio:.2f})の '{uia_rescued_text}' を採用")
                else:
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

        if not any_ime_active and fallback_text and not has_suggest_selection:
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
        enter_skipped = False
        
        for e in trailing_events:
            if e.get("raw_action") == "uia_scan":
                continue
                
            role_lower = str(e.get("semantic_role", "")).lower()
            
            if final_text:
                # 文字入力が確定した場合、それに付随する tab(補完) や 最初の enter(IME確定) は不要なため除外する
                if role_lower == "tab":
                    logger.info("[TypingAggregator] 文字入力に付随する 'tab' (補完操作) をマクロから除外します")
                    continue
                if role_lower == "enter" and e.get("ime_active", False) and not enter_skipped:
                    logger.info("[TypingAggregator] 文字入力に付随する 'enter' (最初のIME確定操作) をマクロから除外します")
                    enter_skipped = True
                    continue

            trailing_special_keys.append(e)

        output_list.extend(trailing_special_keys)
        
        session.clear()