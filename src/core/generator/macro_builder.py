"""Module: @role: 最適化されたイベント情報からWorkflowモデルとExecutableMacroを構築し、JSONファイルとして保存するモジュール。"""

import json
import logging
from pathlib import Path
from typing import Any, Callable, Dict, List, Optional

from core.generator.executable_macro_builder import build_executable_commands
from core.generator.workflow_builder import build_workflow_model
from models.data_types import IntegratedEvent

logger = logging.getLogger(__name__)


def build_and_save_macro(
    temp_workflow_info: List[Dict[str, Any]],
    integrated_events: List[IntegratedEvent],
    variables: Dict[str, str],
    workflow_id: str,
    target_dir: Path,
    check_cancel_callback: Optional[Callable[[], bool]] = None,
) -> None:
    workflow, ui_targets_dict = build_workflow_model(
        temp_workflow_info=temp_workflow_info,
        integrated_events=integrated_events,
        workflow_id=workflow_id,
        check_cancel_callback=check_cancel_callback,
    )

    with open(target_dir / "integrated.json", "w", encoding="utf-8") as f:
        json.dump([evt.model_dump() for evt in integrated_events], f, indent=4, ensure_ascii=False)

    with open(target_dir / "workflow.json", "w", encoding="utf-8") as f:
        f.write(workflow.model_dump_json(indent=4))

    with open(target_dir / "variables.json", "w", encoding="utf-8") as f:
        json.dump(variables, f, indent=4, ensure_ascii=False)

    with open(target_dir / "ui_targets.json", "w", encoding="utf-8") as f:
        json.dump(ui_targets_dict, f, indent=4, ensure_ascii=False)

    logger.info(f"[{workflow_id}] Successfully generated integrated, workflow v2.0, variables, and ui_targets.json.")

    commands_data = build_executable_commands(
        workflow_steps=workflow.steps,
        temp_workflow_info=temp_workflow_info,
        integrated_events=integrated_events,
        check_cancel_callback=check_cancel_callback,
    )
    exec_macro_dict = {
        "macro_id": workflow_id,
        "target_application": "auto_generated",
        "commands": commands_data,
    }
    with open(target_dir / "executable_macro.json", "w", encoding="utf-8") as f:
        json.dump(exec_macro_dict, f, indent=4, ensure_ascii=False)

    logger.info(f"[{workflow_id}] Successfully generated executable_macro.json deterministically.")
