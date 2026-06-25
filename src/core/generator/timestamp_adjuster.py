# @role: 画面変動率(diffRatio)の大きさに応じて、ワークフローイベント間のタイムスタンプの間隔を動的に補正する。

import logging
from models.data_types import Workflow, IntegratedEvent

logger = logging.getLogger(__name__)

BASE_DELAY_MULTIPLIER = 5.0
MIN_DIFF_THRESHOLD = 0.05

def adjust_workflow_timestamps(workflow: Workflow, integrated_data: list[IntegratedEvent]) -> Workflow:
    logger.info(f"[{workflow.workflow_ID}] Adjusting workflow timestamps based on diffRatio...")
    
    event_dict = {evt.id: evt for evt in integrated_data if evt.id}
    accumulated_delay_ms = 0
    
    for i, event in enumerate(workflow.events):
        event.timestamp += accumulated_delay_ms
        
        integrated_evt = event_dict.get(event.event_id)
        if not integrated_evt:
            continue
            
        uis = integrated_evt.window.UIs
        if not uis:
            continue
            
        diff_ratio = uis[0].action.diffRatio
        
        if diff_ratio >= MIN_DIFF_THRESHOLD:
            # タイムスタンプがミリ秒単位であることを考慮し、追加秒数をミリ秒に変換して加算
            additional_delay_sec = diff_ratio * BASE_DELAY_MULTIPLIER
            additional_delay_ms = int(additional_delay_sec * 1000)
            
            if additional_delay_ms > 0:
                accumulated_delay_ms += additional_delay_ms
                logger.debug(f"Event {event.event_id}: diffRatio {diff_ratio:.2f} detected. Adding {additional_delay_sec}s to subsequent events.")
                
    return workflow