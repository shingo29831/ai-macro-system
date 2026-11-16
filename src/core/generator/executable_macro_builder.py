"""Module: @role: Workflowステップ列から実行エンジン向けのコマンド列を構築し、待機時間補完とスクロール統合を行う。"""

import logging
from typing import Any, Callable, Dict, List, Optional

from models.data_types import IntegratedEvent, WorkflowStep
from core.generator.command_postprocessor import append_wait_if_needed, merge_scroll_commands
from core.generator.command_step_converter import NO_IMAGE_MATCH_COMMANDS, convert_step_to_command

logger = logging.getLogger(__name__)




def build_executable_commands(
    workflow_steps: List[WorkflowStep],
    temp_workflow_info: List[Dict[str, Any]],
    integrated_events: List[IntegratedEvent],
    check_cancel_callback: Optional[Callable[[], bool]] = None,
) -> List[Dict[str, Any]]:
    raw_commands_data: List[Dict[str, Any]] = []
    prev_timestamp: Optional[int] = None
    workflow_info_map = {item["event_id"]: item for item in temp_workflow_info}

    for step in workflow_steps:
        if check_cancel_callback and check_cancel_callback():
            raise InterruptedError("Generation cancelled by user")

        raw_event_id = step.fallback_raw_events[0] if step.fallback_raw_events else None
        base_id = raw_event_id.replace("_nav_click", "").split("_chk_")[0].split("_submit_")[0] if raw_event_id else None
        integ_evt = next((e for e in integrated_events if e.id in [raw_event_id, base_id]), None)

        cmd_type = step.action.command
        if not integ_evt and cmd_type not in NO_IMAGE_MATCH_COMMANDS:
            continue

        current_timestamp = integ_evt.timestamp if integ_evt else (prev_timestamp or 0)
        prev_timestamp = append_wait_if_needed(raw_commands_data, current_timestamp, prev_timestamp)

        cur_info = next(
            (workflow_info_map[eid] for eid in step.fallback_raw_events if eid in workflow_info_map),
            workflow_info_map.get(raw_event_id, {}),
        )
        cmd_entry = convert_step_to_command(step, cur_info, integ_evt, raw_event_id)
        if cmd_entry is not None:
            raw_commands_data.append(cmd_entry)

    return merge_scroll_commands(raw_commands_data)
