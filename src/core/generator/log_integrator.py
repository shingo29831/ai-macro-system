# @role: temp/ に保存された一時生データ（入力ログ・YOLO/OCR解析等）を統合し、仕様書定義の integrated.json と workflow.json を生成する。

import json
import logging
import shutil
from pathlib import Path
from datetime import datetime
from models.data_types import (
    AppConfig, Workflow, WorkflowEvent, WorkflowAction, 
    EventContext, InteractedElementContext,
    IntegratedEvent, WindowContext, Size, Coordinates,
    InteractedUiElement, BoundingBox, ActionDetail
)

logger = logging.getLogger(__name__)

def generate_macro_workflow(workflow_id: str, config: AppConfig) -> None:
    """記録された生データを統合し、仕様定義に基づくマクロワークフローを生成する"""
    logger.info(f"[{workflow_id}] Starting log integration and workflow generation...")
    
    try:
        from core.recorder.screen_capturer import get_macros_root
        macros_root = get_macros_root()
        target_dir = macros_root / workflow_id
        temp_dir = target_dir / "temp"
        input_logs_path = temp_dir / "input_logs.json"

        if not input_logs_path.exists():
            raise FileNotFoundError(f"Missing input_logs.json at {input_logs_path}")

        # 1. 生データ (input_logs.json) の読み込み
        with open(input_logs_path, 'r', encoding='utf-8') as f:
            raw_logs = json.load(f)

        # OS Hook側で保存された Logs 配列を特定
        log_entries = []
        if isinstance(raw_logs, dict) and "Logs" in raw_logs:
            log_entries = raw_logs["Logs"]
        elif isinstance(raw_logs, dict) and "source_logs" in raw_logs and "Logs" in raw_logs["source_logs"]:
            log_entries = raw_logs["source_logs"]["Logs"]
        elif isinstance(raw_logs, list):
            log_entries = raw_logs
        else:
            raise ValueError(f"Unsupported JSON structure: {type(raw_logs)}")

        integrated_events = []
        workflow_events = []

        # 2. データのパースと仕様書モデル (Phase 2) への変換
        for i, log_entry in enumerate(log_entries):
            if not isinstance(log_entry, dict):
                continue
                
            # --- 共通属性の抽出 ---
            event_no = log_entry.get("EventNo", f"{i+1:03d}")
            event_id = f"evt_{event_no}" if not str(event_no).startswith("evt_") else str(event_no)
            
            # タイムスタンプのパース (ISO 8601 -> Unix Epoch)
            ts_val = log_entry.get("TimeStamp", 0)
            try:
                if isinstance(ts_val, str):
                    dt = datetime.fromisoformat(ts_val.replace('Z', '+00:00'))
                    safe_timestamp = int(dt.timestamp() * 1000)
                else:
                    safe_timestamp = int(ts_val)
            except Exception:
                safe_timestamp = 0

            # --- ウィンドウ・座標情報の抽出 (null対策として `or {}` を使用) ---
            window_name = log_entry.get("WindowName") or "Unknown Window"
            win_size_data = log_entry.get("WindowSize") or {"width": 0, "height": 0}
            win_coord_data = log_entry.get("WindowCoordinates") or {"x": 0, "y": 0}
            cursor_coord_data = log_entry.get("CursorCoordinates") or {"x": 0, "y": 0}

            win_x = win_coord_data.get("x", 0)
            win_y = win_coord_data.get("y", 0)
            cursor_x = cursor_coord_data.get("x", 0)
            cursor_y = cursor_coord_data.get("y", 0)

            # ウィンドウ内相対座標の計算
            rel_x = cursor_x - win_x
            rel_y = cursor_y - win_y

            # --- 操作内容の抽出 ---
            raw_type = str(log_entry.get("Type", ""))
            content_data = log_entry.get("Content") or {}
            
            button_val = "left"
            input_val = "unknown"
            
            if isinstance(content_data, dict):
                button_val = content_data.get("button", "left")
                # キー入力の場合はキーの文字を取得し、クリックの場合はbutton名をセット
                input_val = content_data.get("key", "") or content_data.get("text", "") or f"{button_val}_click"
            else:
                input_val = str(content_data)

            # Workflow用アクション種別
            action_type = "click" if "click" in raw_type.lower() else "key_down" if "key" in raw_type.lower() else "unknown"

            # -------------------------------------------------------------
            # [A] integrated.json (仕様書 6.4 / 6.5) 向けモデルの構築
            # -------------------------------------------------------------
            action_detail = ActionDetail(
                inputType=raw_type,
                inputValue=input_val,
                cursorRelativeCoordinates=Coordinates(x=rel_x, y=rel_y),
                diffRatio=0.0
            )

            # YOLO/OCR統合前のため、UI要素として仮構築
            ui_element = InteractedUiElement(
                type="unknown",
                relativeBoundingBox=BoundingBox(x=rel_x, y=rel_y, width=0, height=0),
                confidence=0.0,
                action=action_detail,
                context=[]
            )

            window_context = WindowContext(
                name=window_name,
                size=Size(width=win_size_data.get("width", 0), height=win_size_data.get("height", 0)),
                coordinates=Coordinates(x=win_x, y=win_y),
                UIs=[ui_element]
            )

            integrated_events.append(IntegratedEvent(
                id=event_id,
                timestamp=safe_timestamp,
                window=window_context
            ))

            # -------------------------------------------------------------
            # [B] workflow.json (仕様書 6.6 / 6.7) 向けモデルの構築
            # -------------------------------------------------------------
            action = WorkflowAction(
                type=action_type,
                button=button_val,
                modifiers=[]
            )
            
            context = EventContext(
                interacted_element=InteractedElementContext(
                    element_id=f"el_{event_id}",
                    ui_type="unknown",
                    semantic_role=input_val, # キー入力等の意味情報を保持
                    location_context="screen"
                )
            )
            
            workflow_events.append(WorkflowEvent(
                event_id=event_id,
                timestamp=safe_timestamp,
                action=action,
                context=context
            ))

        # 3. 最上位 Workflow モデルの構築
        workflow = Workflow(
            workflow_ID=workflow_id,
            target_ID="primary_application",
            events=workflow_events
        )

        # 4. JSONファイルの書き出し
        integrated_path = target_dir / "integrated.json"
        workflow_path = target_dir / "workflow.json"

        # integrated.json は IntegratedEvent の配列として保存
        with open(integrated_path, 'w', encoding='utf-8') as f:
            json.dump([evt.model_dump() for evt in integrated_events], f, indent=4, ensure_ascii=False)

        # workflow.json の保存
        with open(workflow_path, 'w', encoding='utf-8') as f:
            f.write(workflow.model_dump_json(indent=4))
            
        logger.info(f"[{workflow_id}] Successfully generated integrated.json and workflow.json ({len(workflow_events)} events).")

        # 5. ストレージ節約のため temp/ を削除
        if temp_dir.exists() and temp_dir.is_dir():
            shutil.rmtree(temp_dir)
            logger.info(f"[{workflow_id}] Cleaned up temp directory.")

    except Exception as e:
        logger.error(f"[{workflow_id}] Failed to generate macro workflow: {e}")
        raise