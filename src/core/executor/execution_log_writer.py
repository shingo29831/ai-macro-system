"""Module: @role: マクロ実行ログの構造生成およびファイル永続化を担当する。"""

from datetime import datetime
from pathlib import Path
from typing import Any, Dict
import json
import logging
import time

logger = logging.getLogger(__name__)


def create_execution_log(workflow_id: str) -> Dict[str, Any]:
    """実行ログの初期辞書を生成する。"""
    return {
        "workflow_id": workflow_id,
        "start_time": datetime.now().isoformat(),
        "start_step": 0,
        "steps": [],
        "status": "running",
    }


def save_execution_log(target_dir: Path, execution_log: Dict[str, Any]) -> None:
    """実行ログを execution_logs/ 配下へJSON保存する。"""
    execution_log["end_time"] = datetime.now().isoformat()
    try:
        log_dir = target_dir / "execution_logs"
        log_dir.mkdir(parents=True, exist_ok=True)
        log_path = log_dir / f"run_log_{int(time.time())}.json"
        with open(log_path, "w", encoding="utf-8") as f:
            json.dump(execution_log, f, indent=4, ensure_ascii=False)
    except Exception as e:
        logger.error(f"Failed to save execution log: {e}")
