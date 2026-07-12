# Role: 連続するキー入力ログをセッションとして集約し、UIA確定テキストを優先適用して精度の高い文字列マクロアクションを生成するモジュール
from typing import List, Dict, Any, Optional
import logging

logger = logging.getLogger(__name__)

class TypingSessionAggregator:
    """
    分断されたキーボード入力イベントを分析・集約し、最良のコンテキスト（UIA > OCR > Raw Key）
    を選択して1つのまとまった type_text アクションに最適化するクラス。
    """
    def __init__(self, session_timeout_ms: int = 600):
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
            
            # 特殊キーの判定（確定や移動、削除など）
            role_lower = str(event.get("semantic_role", "")).lower()
            is_special_key = action in ["key_down", "key_press"] and (
                role_lower.startswith("key.") or 
                role_lower in ["enter", "tab", "esc", "up", "down", "left", "right"]
            )
            is_text_input = action in ["type_text", "key_down", "key_press"] and not is_special_key
            is_uia_scan = action == "uia_scan"
            is_confirm_key = is_special_key and role_lower in ["enter", "tab"]

            # マウス移動は入力セッションを中断しないようにする
            is_mouse_move = action in ["mouse_move", "mouse_hover"]

            # セッションの継続条件: 文字入力、UIAスキャン、または確定キー(Enter/Tab)
            # タイムアウト条件を撤廃し、間にクリックなどの別アクションが挟まらない限り同一セッションとする
            if is_text_input or is_uia_scan or is_confirm_key:
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
        # 優先順位 1: UIA（アプリ固有コンテキスト）からの確定文字の一括レスキュー
        # -------------------------------------------------------------------------
        uia_rescued_text = ""
        # 抽出対象は session 本体と、分離した trailing_events (Enter, Tab, uia_scan) の両方
        all_events_in_session = session + trailing_events
        for item in reversed(all_events_in_session):
            # パターンA: uia_scan イベントからの抽出（タブ補完後の文字など）
            if item.get("raw_action") == "uia_scan":
                uia_info = item.get("content", {}).get("uia_info", {})
                val = uia_info.get("value") or uia_info.get("name")
                if val and len(str(val).strip()) > 0:
                    uia_rescued_text = str(val).strip()
                    logger.info(f"[TypingAggregator] UIAレスキュー成功(uia_scan): 確定文字列 '{uia_rescued_text}' を採用")
                    break

            # パターンB: app_context からの抽出（Enter確定時の文字など）
            # ログのキー名揺れに対応 (app_context, AppSpecificContext, appSpecificContext)
            app_ctx = item.get("app_context") or item.get("AppSpecificContext") or item.get("appSpecificContext")
            if isinstance(app_ctx, dict):
                # Edit要素やValuePatternから取れた確定文字列を探す
                val = app_ctx.get("value") or app_ctx.get("text") or app_ctx.get("url")
                if val and len(str(val).strip()) > 0:
                    uia_rescued_text = str(val).strip()
                    logger.info(f"[TypingAggregator] UIAレスキュー成功(app_context): 確定文字列 '{uia_rescued_text}' を採用")
                    break

        # -------------------------------------------------------------------------
        # 優先順位 2: UIAが取れなかった場合の生キーログ結合 ＋ ローマ字/かな変換
        # -------------------------------------------------------------------------
        fallback_text = ""
        any_ime_active = any(item.get("ime_active", False) for item in session)
        
        # 制御キーや修飾キーの除外リスト
        ignore_exact_keys = {"tab", "enter", "delete", "esc", "shift", "ctrl", "alt", "win", "cmd"}
        ignore_modifiers = ["shift", "ctrl", "alt", "win", "cmd"]
        
        for item in session:
            role = str(item.get("semantic_role", ""))
            r_lower = role.lower()
            
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

        # 最終採用テキストの決定 (UIA > Fallback Key Log)
        final_text = uia_rescued_text if uia_rescued_text else fallback_text

        if final_text:
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
        trailing_special_keys = [e for e in trailing_events if e.get("raw_action") != "uia_scan"]
        output_list.extend(trailing_special_keys)
        
        session.clear()