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
        last_timestamp = 0

        for i, event in enumerate(raw_events):
            action = event.get("raw_action", "")
            timestamp = event.get("timestamp", 0)
            
            # 特殊キーの判定（確定や移動、削除など）
            role_lower = str(event.get("semantic_role", "")).lower()
            is_special_key = action == "key_down" and (
                role_lower.startswith("key.") or 
                role_lower in ["enter", "tab", "esc", "up", "down", "left", "right"]
            )
            is_text_input = action in ["type_text", "key_down"] and not is_special_key

            # セッションの継続条件: 文字入力であり、かつ前回の入力からタイムアウト以内であること
            if is_text_input:
                if not current_session or (timestamp - last_timestamp <= self.session_timeout_ms):
                    current_session.append(event)
                    last_timestamp = timestamp
                    continue
                else:
                    # タイムアウトした場合は既存のセッションをフラッシュして新規開始
                    self._flush_session(current_session, aggregated_events)
                    current_session = [event]
                    last_timestamp = timestamp
                    continue

            # 文字入力以外のイベント（クリック、移動、Enter/Tab等の特殊キー）が来た場合
            if current_session:
                # EnterやTabは入力確定トリガーとして扱うため、セッションに含めて評価する
                if is_special_key and role_lower in ["enter", "tab"]:
                    current_session.append(event)
                    self._flush_session(current_session, aggregated_events)
                    continue
                else:
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

        # 単一のイベントで、かつ特殊な削除キーなどの場合はそのまま出力して終了
        if len(session) == 1 and str(session[0].get("semantic_role", "")).lower() not in ["space", "backspace", "delete"]:
            output_list.append(session[0])
            session.clear()
            return

        target_event_id = session[0].get("event_id", "unknown")
        logger.info(f"[TypingAggregator] セッション集約を開始 (イベント数: {len(session)}, 開始ID: {target_event_id})")

        # -------------------------------------------------------------------------
        # 優先順位 1: UIA（アプリ固有コンテキスト）からの確定文字の一括レスキュー
        # -------------------------------------------------------------------------
        uia_rescued_text = ""
        for item in reversed(session):
            app_ctx = item.get("app_context")
            if isinstance(app_ctx, dict):
                # Edit要素やValuePatternから取れた確定文字列を探す
                val = app_ctx.get("value") or app_ctx.get("text") or app_ctx.get("url")
                if val and len(str(val).strip()) > 0:
                    uia_rescued_text = str(val).strip()
                    logger.info(f"[TypingAggregator] UIAレスキュー成功: 確定文字列 '{uia_rescued_text}' を採用")
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

        if not final_text:
            logger.warning(f"[TypingAggregator] セッション ({target_event_id}) から有効なテキストを抽出できませんでした。生イベントを復元します。")
            output_list.extend(session)
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
        session.clear()