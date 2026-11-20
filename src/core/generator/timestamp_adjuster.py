"""Module: @role: 画面変動率(diffRatio)の大きさに応じて、ワークフローステップの待機タイムアウト猶予を動的に補正する。"""

import logging
from models.data_types import Workflow, IntegratedEvent

logger = logging.getLogger(__name__)

BASE_DELAY_MULTIPLIER = 5.0
MIN_DIFF_THRESHOLD = 0.05


def adjust_workflow_timestamps(workflow: Workflow, integrated_data: list[IntegratedEvent]) -> Workflow:
    logger.info(f"[{workflow.workflow_ID}] Adjusting workflow step timeouts based on diffRatio...")

    event_dict = {evt.id: evt for evt in integrated_data if evt.id}
    accumulated_delay_ms = 0

    for step in workflow.steps:
        if accumulated_delay_ms > 0:
            base_timeout = step.action.parameters.timeout_ms or 0
            step.action.parameters.timeout_ms = base_timeout + accumulated_delay_ms

        integrated_evt = next(
            (event_dict[eid] for eid in (step.fallback_raw_events or []) if eid in event_dict),
            None,
        )
        if not integrated_evt:
            continue

        uis = integrated_evt.window.UIs
        if not uis or uis[0].action is None:
            continue

        diff_ratio = uis[0].action.diffRatio
        if diff_ratio >= MIN_DIFF_THRESHOLD:
            additional_delay_sec = diff_ratio * BASE_DELAY_MULTIPLIER
            additional_delay_ms = int(additional_delay_sec * 1000)
            if additional_delay_ms > 0:
                accumulated_delay_ms += additional_delay_ms
                logger.debug(
                    f"Step {step.step_id} ({integrated_evt.id}): diffRatio {diff_ratio:.2f} detected. "
                    f"Adding {additional_delay_sec:.2f}s delay allowance."
                )

    return workflow
