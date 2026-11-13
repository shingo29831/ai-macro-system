"""Module: @role: 最適化されたイベント情報と統合イベントからWorkflowモデルおよびUIターゲット定義を構築する。"""

import json
import logging
import re
from typing import Any, Callable, Dict, List, Optional, Tuple

from models.data_types import (
    ActionParameters,
    IntegratedEvent,
    Size,
    UniversalSelector,
    Workflow,
    WorkflowCommandAction,
    WorkflowMetadata,
    WorkflowStep,
    WorkflowStepContext,
)

logger = logging.getLogger(__name__)

# Why: Web検索ページ（Google 検索等）がシェル検索窓として誤除外されるのを防止
SYSTEM_EXACT_WINDOWS = [
    "検索", "スタート", "start", "search", "タスクバー", "taskbar", "cortana", "ジャンプ リスト",
]
SYSTEM_WINDOW_KEYWORDS = [
    "python", "unknown window", "マクロ生成中", "aiマクロ生成中",
    "ai macro system", "記録中", "停止中", "実行中", "設定", "ウィンドウの紐付け",
]
BROWSER_WINDOW_KEYWORDS = ["firefox", "chrome", "edge", "brave", "opera"]
SPECIAL_KEY_NAMES = {
    "enter", "space", "tab", "esc", "backspace", "delete", "shift",
    "ctrl", "alt", "cmd", "win", "windows", "up", "down", "left", "right",
}


def _extract_app_name(title: str) -> str:
    if not title:
        return "App"
    parts = re.split(r"[\-—–―]", title)
    app = parts[-1].strip()
    return app if app else title.strip()


def _should_skip_system_window(current_window: str) -> bool:
    cw_lower = current_window.lower().strip()
    is_browser_win = any(b in cw_lower for b in BROWSER_WINDOW_KEYWORDS)
    if not is_browser_win:
        return cw_lower in SYSTEM_EXACT_WINDOWS or any(sw in cw_lower for sw in SYSTEM_WINDOW_KEYWORDS)
    return any(sw in cw_lower for sw in SYSTEM_WINDOW_KEYWORDS)


def _build_action_command(
    info: Dict[str, Any],
    raw_action: str,
    raw_type: str,
    final_semantic_role: str,
    event_id: str,
) -> Optional[Tuple[str, str, str, ActionParameters]]:
    if raw_action == "browser_action" or raw_type == "browser_action":
        b_act = info.get("action", "open_url")
        app_ctx = info.get("app_context") or {}
        # Why: select_optionやset_checkboxの選択値をbrowser_valueフィールドへ確実にバインド
        b_val = (
            info.get("value")
            if info.get("value") is not None
            else (info.get("text") if b_act in ["select_option", "set_checkbox"] else None)
        )
        params = ActionParameters(
            browser_action=b_act,
            browser_url=info.get("url") or app_ctx.get("url"),
            browser_selector=info.get("selector") or app_ctx.get("css_selector") or app_ctx.get("xpath"),
            browser_selector_type=info.get("selector_type", "css"),
            text=info.get("text") or (final_semantic_role if final_semantic_role != "left_click" else ""),
            browser_value=b_val,
        )
        return "BROWSER_ACTION", f"BROWSER_{b_act.upper()}", f"Execute browser action: {b_act}", params

    if raw_action == "excel_action" or raw_type == "excel_action":
        params = ActionParameters(
            excel_action=info.get("action"),
            excel_file_path=info.get("file_path"),
            excel_sheet=info.get("sheet_name"),
            excel_range=info.get("range_address") or info.get("cell"),
            excel_cell=info.get("cell"),
            excel_value=info.get("value"),
            excel_variable_name=info.get("variable_name"),
            excel_macro_name=info.get("macro_name"),
        )
        return "EXCEL_ACTION", f"EXCEL_{info.get('action', 'ACTION').upper()}", f"Execute Excel action: {info.get('action')}", params

    if raw_action == "drag":
        start_x = info.get("drag_start_x", info.get("cursor_x"))
        start_y = info.get("drag_start_y", info.get("cursor_y"))
        end_x = info.get("drag_end_x")
        end_y = info.get("drag_end_y")
        if any(v is None for v in (start_x, start_y, end_x, end_y)) or (start_x == end_x and start_y == end_y):
            logger.warning("Skipping drag event %s because its start/end coordinates are missing or identical", event_id)
            return None
        params = ActionParameters(
            drag_start_x=int(start_x),
            drag_start_y=int(start_y),
            drag_end_x=int(end_x),
            drag_end_y=int(end_y),
            button=info.get("button") or "left",
        )
        return "MOUSE_DRAG", "DRAG_UI_ELEMENT", f"Drag from ({start_x}, {start_y}) to ({end_x}, {end_y}).", params

    if raw_action == "click":
        app_ctx = info.get("app_context") or {}
        params = ActionParameters(
            target=UniversalSelector(
                semantic_role=final_semantic_role,
                css_selector=app_ctx.get("css_selector"),
                xpath=app_ctx.get("xpath"),
                url_pattern=app_ctx.get("url"),
            ),
            button=info["button"],
        )
        return "MOUSE_CLICK", "CLICK_UI_ELEMENT", f"Click on the {final_semantic_role} element.", params

    if raw_action == "move":
        app_ctx = info.get("app_context") or {}
        params = ActionParameters(
            target=UniversalSelector(
                semantic_role=final_semantic_role,
                css_selector=app_ctx.get("css_selector"),
                xpath=app_ctx.get("xpath"),
                url_pattern=app_ctx.get("url"),
            )
        )
        return "MOUSE_MOVE", "MOVE_CURSOR", f"Move cursor to the {final_semantic_role} element.", params

    if raw_action == "type_text":
        params = ActionParameters(text=final_semantic_role)
        if "sequence_value" in info:
            params.sequence_value = info["sequence_value"]
        return "TYPE_TEXT", "INPUT_TEXT", f"Type the text: '{final_semantic_role}'", params

    if raw_action == "scroll":
        cursor_x = info.get("cursor_x", 0)
        cursor_y = info.get("cursor_y", 0)
        params = ActionParameters(text=f"{info['dx']},{info['dy']},{cursor_x},{cursor_y}")
        return "MOUSE_SCROLL", "SCROLL_WINDOW", f"Scroll window (dx: {info['dx']}, dy: {info['dy']})", params

    role_lower = final_semantic_role.lower() if final_semantic_role else ""
    is_special_key = False
    parsed_key = role_lower
    if role_lower.startswith("key."):
        is_special_key = True
        parsed_key = role_lower.replace("key.", "")
    elif role_lower in SPECIAL_KEY_NAMES or "+" in role_lower:
        is_special_key = True

    if is_special_key:
        return "KEYBOARD_SHORTCUT", "PRESS_SPECIAL_KEY", f"Press the {parsed_key} key.", ActionParameters(key=parsed_key)
    return "TYPE_TEXT", "INPUT_TEXT", f"Type the text: '{final_semantic_role}'", ActionParameters(text=final_semantic_role)


def build_workflow_model(
    temp_workflow_info: List[Dict[str, Any]],
    integrated_events: List[IntegratedEvent],
    workflow_id: str,
    check_cancel_callback: Optional[Callable[[], bool]] = None,
) -> Tuple[Workflow, Dict[str, Any]]:
    workflow_steps: List[WorkflowStep] = []
    ui_targets_dict: Dict[str, Any] = {}

    start_time = integrated_events[0].timestamp if integrated_events else 0
    end_time = integrated_events[-1].timestamp if integrated_events else 0
    max_w = max([e.window.size.width for e in integrated_events if e.window.size.width > 0] or [1920])
    max_h = max([e.window.size.height for e in integrated_events if e.window.size.height > 0] or [1080])
    screen_size = Size(width=max_w, height=max_h)

    step_idx = 1
    prev_window_name: Optional[str] = None
    window_alias_map: Dict[Tuple[str, Tuple[int, int, int, int]], str] = {}
    app_alias_counters: Dict[str, int] = {}

    def get_window_alias(title: str, rect: Tuple[int, int, int, int]) -> str:
        app_name = _extract_app_name(title)
        clean_title = (title or "").strip()
        # Why: 同一アプリでも別ドキュメント・別タブのウィンドウを正しく区別
        for (reg_title, _), alias in window_alias_map.items():
            if reg_title == clean_title:
                return alias
        app_alias_counters[app_name] = app_alias_counters.get(app_name, 0) + 1
        new_alias = f"{app_name}{app_alias_counters[app_name]}"
        window_alias_map[(clean_title, rect)] = new_alias
        return new_alias

    for info in temp_workflow_info:
        if check_cancel_callback and check_cancel_callback():
            raise InterruptedError("Generation cancelled by user")

        raw_action = info["raw_action"]
        raw_type = info["raw_type"].lower()
        if raw_action in ["unknown", "uia_scan"] or "recording" in raw_type:
            continue

        event_id = info["event_id"]
        fallback_evts = info.get("fallback_events", [event_id])
        current_window = next(
            (e.window.name for e in integrated_events if e.id == event_id or e.id in fallback_evts),
            info.get("window_name", "Unknown"),
        )

        if raw_type == "meta_loop_start":
            loop_vars = info.get("loop_variables", {"y_offset": 30})
            workflow_steps.append(
                WorkflowStep(
                    step_id=step_idx,
                    intent="START_LOOP",
                    description="Start repeating actions.",
                    context=WorkflowStepContext(active_window_name=current_window),
                    action=WorkflowCommandAction(
                        command="LOOP_START",
                        parameters=ActionParameters(loop_count=10, loop_variables=loop_vars),
                    ),
                    fallback_raw_events=[event_id],
                )
            )
            step_idx += 1
            continue

        if raw_type == "meta_loop_end":
            workflow_steps.append(
                WorkflowStep(
                    step_id=step_idx,
                    intent="END_LOOP",
                    description="End repeating actions.",
                    context=WorkflowStepContext(active_window_name=current_window),
                    action=WorkflowCommandAction(command="LOOP_END", parameters=ActionParameters()),
                    fallback_raw_events=[event_id],
                )
            )
            step_idx += 1
            continue

        if current_window != "Unknown":
            if _should_skip_system_window(current_window):
                continue

            win_ctx = next((e.window for e in integrated_events if e.id == event_id), None)
            # Why: 最適化処理で決定された最終ウィンドウ配置を優先しマルチモニタ移動を正しく反映
            win_x = info.get("win_x") if info.get("win_x") is not None else (win_ctx.coordinates.x if win_ctx else 0)
            win_y = info.get("win_y") if info.get("win_y") is not None else (win_ctx.coordinates.y if win_ctx else 0)
            win_w = info.get("win_w") if info.get("win_w") is not None else (win_ctx.size.width if win_ctx else 0)
            win_h = info.get("win_h") if info.get("win_h") is not None else (win_ctx.size.height if win_ctx else 0)
            is_max = info.get("is_maximized")
            if is_max is None:
                is_max = info.get("IsMaximized")
            if is_max is None and (win_x <= -8 and win_y <= -8 and win_w >= 1900):
                is_max = True

            current_win_rect = (win_x, win_y, win_w, win_h)
            curr_app = _extract_app_name(current_window).lower()
            prev_app = _extract_app_name(prev_window_name).lower() if prev_window_name else ""
            is_same_app = curr_app == prev_app and curr_app != ""

            # Why: 同一アプリ移動時は別アクティベートを挿入せずリサイズ/移動のみ委譲
            needs_activation = prev_window_name is None or (not is_same_app and current_window != prev_window_name)
            if needs_activation:
                win_info_json = json.dumps(
                    {
                        "title": current_window,
                        "x": win_x,
                        "y": win_y,
                        "width": win_w,
                        "height": win_h,
                        "launch_cmd": info.get("command_line", ""),
                        "window_alias": get_window_alias(current_window, current_win_rect),
                        "is_maximized": bool(is_max),
                    },
                    ensure_ascii=False,
                )
                workflow_steps.append(
                    WorkflowStep(
                        step_id=step_idx,
                        intent="ACTIVATE_WINDOW",
                        description=f"Activate and resize window: {current_window}",
                        context=WorkflowStepContext(active_window_name=current_window),
                        action=WorkflowCommandAction(
                            command="ACTIVATE_WINDOW",
                            parameters=ActionParameters(text=win_info_json),
                        ),
                        fallback_raw_events=[event_id],
                    )
                )
                step_idx += 1
                prev_window_name = current_window

        final_semantic_role = info.get("semantic_role") or info.get("element_name") or info.get("text") or ""
        if raw_action in ["click", "move", "type_text", "key_down", "drag"]:
            ui_targets_dict[f"tgt_{step_idx}"] = {
                "semantic_role": final_semantic_role,
                "ui_type": info.get("ui_type", "unknown"),
            }

        built = _build_action_command(info, raw_action, raw_type, final_semantic_role, event_id)
        if built is None:
            continue
        cmd, intent, desc, params = built

        if "excel_cell" in info:
            params.excel_cell = info["excel_cell"]
        if "excel_dest_cell" in info:
            params.excel_dest_cell = info["excel_dest_cell"]

        workflow_steps.append(
            WorkflowStep(
                step_id=step_idx,
                intent=intent,
                description=desc,
                context=WorkflowStepContext(active_window_name=current_window),
                action=WorkflowCommandAction(command=cmd, parameters=params),
                fallback_raw_events=fallback_evts,
            )
        )
        step_idx += 1

    workflow = Workflow(
        version="2.0",
        workflow_ID=workflow_id,
        metadata=WorkflowMetadata(
            os="Windows",
            resolution=screen_size,
            duration_ms=max(0, end_time - start_time),
        ),
        steps=workflow_steps,
    )
    return workflow, ui_targets_dict
