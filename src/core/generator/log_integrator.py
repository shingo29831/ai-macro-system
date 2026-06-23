# @role: temp/ に保存された一時生データ（入力ログ・画像）とローカルAI（YOLO/OCR）の解析結果を統合し、意味を理解した実行可能なワークフローを生成する。
# 
# 【参照元】
#   - UI層からの「記録終了」シグナル受信直後（バックグラウンド処理）
# 
# 【参照先】
#   - engines/yolo/detector.py (UI要素の物体認識)
#   - engines/ocr/reader.py (画像からのテキスト抽出)
#   - models/data_types.py (IntegratedEvent, Workflow 等の全データモデル)
# 
# 【処理内容】
#   - Phase 2 (解析・生成): input_logs.json を読み込み、対応するCrop画像をYOLOとOCRにかけて意味情報を抽出する。
#   - AIの解析結果から ui_type (button, input 等) や semantic_role (テキストやキー名) を推論し、コンテキストに付与する。
#   - ウィンドウを基準としたDOMライクな統合データ (integrated.json) を構築する。
#   - 実行エンジン用の最終ワークフローシナリオ (workflow.json) を構築する。
#   - 処理完了後、 temp/ ディレクトリを破棄する。

import json
import logging
import shutil
import os
from pathlib import Path
from datetime import datetime

from models.data_types import (
    AppConfig, Workflow, WorkflowEvent, WorkflowAction, 
    EventContext, InteractedElementContext,
    IntegratedEvent, WindowContext, Size, Coordinates,
    InteractedUiElement, BoundingBox, ActionDetail,
    ContextComponent
)
from engines.yolo.detector import detect_ui_elements
from engines.ocr.reader import read_text_from_image

logger = logging.getLogger(__name__)

def generate_macro_workflow(workflow_id: str, config: AppConfig) -> None:
    """記録された生データを統合し、AI解析結果を含めたマクロワークフローを生成する"""
    logger.info(f"[{workflow_id}] Starting log integration and AI workflow generation...")
    
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

        # 2. データのパース、AI解析、および仕様書モデルへの変換
        for i, log_entry in enumerate(log_entries):
            if not isinstance(log_entry, dict):
                continue
                
            event_no = log_entry.get("EventNo", f"{i+1:03d}")
            event_id = f"evt_{event_no}" if not str(event_no).startswith("evt_") else str(event_no)
            
            ts_val = log_entry.get("TimeStamp", 0)
            try:
                if isinstance(ts_val, str):
                    dt = datetime.fromisoformat(ts_val.replace('Z', '+00:00'))
                    safe_timestamp = int(dt.timestamp() * 1000)
                else:
                    safe_timestamp = int(ts_val)
            except Exception:
                safe_timestamp = 0

            window_name = log_entry.get("WindowName") or "Unknown Window"
            win_size_data = log_entry.get("WindowSize") or {"width": 0, "height": 0}
            win_coord_data = log_entry.get("WindowCoordinates") or {"x": 0, "y": 0}
            cursor_coord_data = log_entry.get("CursorCoordinates") or {"x": 0, "y": 0}

            win_x = win_coord_data.get("x", 0)
            win_y = win_coord_data.get("y", 0)
            cursor_x = cursor_coord_data.get("x", 0)
            cursor_y = cursor_coord_data.get("y", 0)

            rel_x = cursor_x - win_x
            rel_y = cursor_y - win_y

            raw_type = str(log_entry.get("Type", ""))
            content_data = log_entry.get("Content") or {}
            
            button_val = "left"
            input_val = "unknown"
            
            if isinstance(content_data, dict):
                button_val = content_data.get("button", "left")
                input_val = content_data.get("key", "") or content_data.get("text", "") or f"{button_val}_click"
            else:
                input_val = str(content_data)

            action_type = "click" if "click" in raw_type.lower() else "key_down" if "key" in raw_type.lower() else "unknown"

            # --- AIによる画像解析 (YOLO & OCR) ---
            ui_type = "unknown"
            semantic_role = input_val
            context_components = []
            
            images_data = log_entry.get("Images", {})
            crop_path = images_data.get("Crop")
            
            # クロップ画像が存在する場合はローカルAIエンジンに推論をリクエスト
            if crop_path and os.path.exists(crop_path):
                logger.info(f"[{workflow_id}] Running AI inference for {event_id}...")
                
                # YOLO: UI要素のタイプを特定
                yolo_results = detect_ui_elements(crop_path)
                if yolo_results:
                    best_yolo = max(yolo_results, key=lambda x: x.confidence)
                    ui_type = best_yolo.type
                
                # OCR: 要素に書かれているテキストを抽出
                ocr_results = read_text_from_image(crop_path)
                if ocr_results:
                    best_ocr = max(ocr_results, key=lambda x: x.confidence)
                    if best_ocr.content and action_type == "click":
                        # クリック操作の場合のみ、見えているテキストを意味的役割として上書き
                        semantic_role = best_ocr.content
                        
                    for ocr_res in ocr_results:
                        context_components.append(ContextComponent(
                            type="text",
                            content=ocr_res.content,
                            relativeBoundingBox=ocr_res.boundingBox,
                            confidence=ocr_res.confidence,
                            parentRelevance=1.0  # クロップ画像からの抽出のため関連度は最大とする
                        ))

            # --- [A] integrated.json 向けモデルの構築 ---
            action_detail = ActionDetail(
                inputType=raw_type,
                inputValue=input_val,
                cursorRelativeCoordinates=Coordinates(x=rel_x, y=rel_y),
                diffRatio=0.0
            )

            ui_element = InteractedUiElement(
                type=ui_type,
                relativeBoundingBox=BoundingBox(x=rel_x, y=rel_y, width=0, height=0),
                confidence=1.0,
                action=action_detail,
                context=context_components
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

            # --- [B] workflow.json 向けモデルの構築 ---
            action = WorkflowAction(
                type=action_type,
                button=button_val,
                modifiers=[]
            )
            
            context = EventContext(
                interacted_element=InteractedElementContext(
                    element_id=f"el_{event_id}",
                    ui_type=ui_type,
                    semantic_role=semantic_role,
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

        with open(integrated_path, 'w', encoding='utf-8') as f:
            json.dump([evt.model_dump() for evt in integrated_events], f, indent=4, ensure_ascii=False)

        with open(workflow_path, 'w', encoding='utf-8') as f:
            f.write(workflow.model_dump_json(indent=4))
            
        logger.info(f"[{workflow_id}] Successfully generated integrated.json and workflow.json with AI inference ({len(workflow_events)} events).")

        # 5. ストレージ節約のため temp/ を削除
        if temp_dir.exists() and temp_dir.is_dir():
            shutil.rmtree(temp_dir)
            logger.info(f"[{workflow_id}] Cleaned up temp directory.")

    except Exception as e:
        logger.error(f"[{workflow_id}] Failed to generate macro workflow: {e}")
        raise