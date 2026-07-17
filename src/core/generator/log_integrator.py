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

            parsed_info = parse_raw_event(log_entry, i, total_events, workflow_id, macros_root, progress_callback)
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