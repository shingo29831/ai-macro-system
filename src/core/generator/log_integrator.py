# src/core/generator/log_integrator.py
# Role: temp/ に保存された一時生データとローカルAIの解析結果を統合し、意味を理解した実行可能なワークフローを生成するオーケストレーター。

import json
import logging
from datetime import datetime
from typing import Callable, Optional, Dict, Any, List

from models.data_types import AppConfig
from core.generator.event_parser import parse_raw_event
from core.generator.typing_aggregator import TypingSessionAggregator
from core.generator.workflow_optimizer import optimize_workflow_events
from core.generator.macro_builder import build_and_save_macro

logger = logging.getLogger(__name__)

def filter_meaningful_raw_logs(raw_logs: List[Dict[str, Any]]) -> List[Dict[str, Any]]:
    """マクロで実行・再現する可能性のないノイズログ（過渡マウス移動、クリック直前移動、孤立制御キー、システムUI等）を完全排除する。"""
    if not raw_logs:
        return []

    valid_candidates: List[Dict[str, Any]] = []
    system_exact = ["検索", "スタート", "start", "search", "タスクバー", "taskbar", "cortana", "ジャンプ リスト"]
    system_contains = ["python", "unknown window", "マクロ生成中", "aiマクロ生成中", "ai macro system", "記録中", "停止中", "実行中", "設定", "ウィンドウの紐付け"]

    for entry in raw_logs:
        if not isinstance(entry, dict):
            continue
        raw_type = str(entry.get("Type", "")).lower()
        if any(term in raw_type for term in ["recording", "meta"]):
            continue

        win_name = entry.get("WindowName", "")
        w_lower = win_name.lower().strip()
        is_browser = any(b in w_lower for b in ["firefox", "chrome", "edge", "brave", "opera"])
        if not w_lower or (not is_browser and (w_lower in system_exact or any(sc in w_lower for sc in system_contains))):
            continue

        content = entry.get("Content") or {}
        if isinstance(content, dict):
            combo_str = str(content.get("combo") or content.get("key") or "").lower()
            # Why: IMEトグルキー(shift+space等)は実行エンジン自動IME制御と競合するため除外
            if "+" in combo_str and any(k in combo_str for k in ["space", "grave", "kanji"]):
                continue

        valid_candidates.append(entry)

    filtered: List[Dict[str, Any]] = []
    n = len(valid_candidates)
    i = 0
    while i < n:
        entry = valid_candidates[i]
        raw_type = str(entry.get("Type", "")).lower()

        if "move" in raw_type or "hover" in raw_type:
            next_non_move = None
            for j in range(i + 1, n):
                fut_type = str(valid_candidates[j].get("Type", "")).lower()
                if not ("move" in fut_type or "hover" in fut_type):
                    next_non_move = valid_candidates[j]
                    break

            # Why: 末尾の移動やクリック・ドラッグ直前・過渡期の移動はマクロ実行に不要なため排除
            if not next_non_move:
                i += 1
                continue

            fut_type = str(next_non_move.get("Type", "")).lower()
            if "click" in fut_type or "drag" in fut_type:
                i += 1
                continue

            diff_str = entry.get("Images", {}).get("Diff", "0.0%")
            diff_val = 0.0
            try:
                diff_val = float(str(diff_str).replace("%", ""))
            except Exception:
                pass
            if diff_val < 5.0:
                i += 1
                continue

        if "key" in raw_type:
            content = entry.get("Content") or {}
            key_name = str(content.get("key") or "").lower() if isinstance(content, dict) else ""
            if key_name in ["win", "windows", "cmd", "ctrl", "alt", "shift"]:
                next_act = valid_candidates[i + 1] if i + 1 < n else None
                if not next_act or next_act.get("WindowName") != entry.get("WindowName"):
                    i += 1
                    continue

        filtered.append(entry)
        i += 1

    return filtered

def generate_macro_workflow(
    workflow_id: str, 
    config: AppConfig, 
    progress_callback: Optional[Callable[[int, str], None]] = None,
    check_cancel_callback: Optional[Callable[[], bool]] = None
) -> None:
    logger.info(f"[{workflow_id}] Starting log integration and AI workflow generation...")
    
    generation_debug_log = {
        "workflow_id": workflow_id,
        "start_time": datetime.now().isoformat(),
        "stages": [],
        "errors": []
    }
    
    try:
        if progress_callback:
            progress_callback(0, "初期化中... ワークフローディレクトリの確認")
        generation_debug_log["stages"].append({"name": "Initialization", "status": "started"})

        from core.recorder.screen_capturer import get_macros_root
        macros_root = get_macros_root()
        target_dir = macros_root / workflow_id
        temp_dir = target_dir / "temp"
        input_logs_path = temp_dir / "input_logs.json"

        if not input_logs_path.exists():
            raise FileNotFoundError(f"Missing input_logs.json at {input_logs_path}")

        with open(input_logs_path, 'r', encoding='utf-8') as f:
            raw_logs = json.load(f)

        log_entries = []
        if isinstance(raw_logs, dict) and "Logs" in raw_logs:
            log_entries = raw_logs["Logs"]
        elif isinstance(raw_logs, dict) and "source_logs" in raw_logs and "Logs" in raw_logs["source_logs"]:
            log_entries = raw_logs["source_logs"]["Logs"]
        elif isinstance(raw_logs, list):
            log_entries = raw_logs
        else:
            raise ValueError(f"Unsupported JSON structure: {type(raw_logs)}")

        # Why: マクロで使わないノイズログ（過渡移動・クリック直前移動・孤立キー等）を事前完全排除
        log_entries = filter_meaningful_raw_logs(log_entries)
        total_events = len(log_entries)
        if progress_callback:
            progress_callback(2, f"入力ログの読み込み完了... ({total_events}件のイベントを処理します)")
        generation_debug_log["stages"].append({"name": "Load Logs", "event_count": total_events})

        integrated_events = []
        temp_workflow_info: List[Dict[str, Any]] = []

        for i, log_entry in enumerate(log_entries):
            if check_cancel_callback and check_cancel_callback():
                logger.info(f"[{workflow_id}] Generation cancelled by user.")
                raise InterruptedError("Generation cancelled by user")

            # Why: 単一イベント解析のエラーやAI推論障害による生成パイプライン全体のスタックを完全防止
            try:
                parsed_info = parse_raw_event(log_entry, i, total_events, workflow_id, macros_root, progress_callback)
            except Exception as parse_err:
                logger.warning(f"[{workflow_id}] Event parse failed for entry {i}: {parse_err}")
                parsed_info = None

            if parsed_info:
                integrated_events.append(parsed_info.pop("integrated_event"))
                temp_workflow_info.append(parsed_info)

        if progress_callback:
            progress_callback(70, "入力ログの最適化... 文字入力バッファの集約とUIAレスキュー")
        generation_debug_log["stages"].append({"name": "Typing Aggregation", "status": "started"})

        try:
            aggregator = TypingSessionAggregator(session_timeout_ms=2000)
            temp_workflow_info = aggregator.aggregate_events(temp_workflow_info)
            logger.info(f"[{workflow_id}] キー入力集約完了: 最適化後のステップ数 = {len(temp_workflow_info)}")
        except Exception as e:
            logger.error(f"[{workflow_id}] 文字入力集約処理でエラーが発生しました: {e}")

        if progress_callback:
            progress_callback(75, "ワークフローの最適化中 (Office連携, ループ解析, 変数化)...")
        generation_debug_log["stages"].append({"name": "Workflow Optimization", "status": "started"})
        
        temp_workflow_info, variables = optimize_workflow_events(temp_workflow_info, workflow_id, check_cancel_callback)

        if progress_callback:
            progress_callback(85, "ワークフロー生成中... アクションの最適化とマッピング")
        generation_debug_log["stages"].append({"name": "Macro Building", "status": "started"})

        build_and_save_macro(temp_workflow_info, integrated_events, variables, workflow_id, target_dir, check_cancel_callback)

        generation_debug_log["end_time"] = datetime.now().isoformat()
        generation_debug_log["status"] = "success"
        try:
            with open(target_dir / "generation_log.json", 'w', encoding='utf-8') as f:
                json.dump(generation_debug_log, f, indent=4, ensure_ascii=False)
        except Exception:
            pass

        if progress_callback:
            progress_callback(100, "完了")

    except Exception as e:
        generation_debug_log["errors"].append(str(e))
        generation_debug_log["status"] = "failed"
        try:
            with open(target_dir / "generation_log.json", 'w', encoding='utf-8') as f:
                json.dump(generation_debug_log, f, indent=4, ensure_ascii=False)
        except Exception:
            pass
        logger.error(f"[{workflow_id}] Failed to generate macro workflow: {e}")
        raise