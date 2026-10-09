"""Module: @role: 生成されたExecutable Macro (executable_macro.json) を読み込み、ローカルで自律実行する実行エンジン。"""

from datetime import datetime
from pathlib import Path
from typing import Any, Callable, Dict, List, Optional
import json
import logging
import threading
import time

from pynput.keyboard import Controller as KeyboardController, Listener as KeyboardListener
from pynput.mouse import Controller as MouseController

from core.executor.input_helper import (
    detect_unexpected_dialog as _detect_unexpected_dialog,
    get_window_offset as _get_window_offset,
    resolve_variables,
    resolve_variables as _resolve_variables,
    send_unicode_string as _send_unicode_string,
    set_clipboard_text as _set_clipboard_text,
    smooth_move as _smooth_move,
    wait_for_screen_settle as _wait_for_screen_settle,
)
from core.executor.action_executor import (
    MOUSEEVENTF_HWHEEL,
    MOUSEEVENTF_WHEEL,
    WHEEL_DELTA,
    execute_physical_action,
    verify_and_heal_target,
)
from core.executor.loop_controller import LoopController
from core.executor.os_env_controller import restore_system_cursor, set_system_cursor
from core.executor.step_dispatcher import StepDispatcher
from core.executor.watchdog import ExecutionWatchdog, WorkflowFreezeException, WorkflowStoppedException
from core.executor.window_manager import reset_browser_activation_flag, set_dpi_awareness
from models.data_types import AppConfig

logger = logging.getLogger(__name__)

_is_running = False
_stop_requested = False

set_dpi_awareness()


def run_workflow(
    workflow_id: str,
    config: AppConfig,
    status_callback: Optional[Callable[[str, bool], None]] = None,
    temp_commands: Optional[List[Dict[str, Any]]] = None,
    on_freeze_callback: Optional[Callable[[str, float, str], None]] = None,
) -> None:
    global _is_running, _stop_requested
    _is_running = True
    _stop_requested = False
    reset_browser_activation_flag()
    set_system_cursor("run_idle")

    logger.info(f"[{workflow_id}] Starting executable macro execution...")
    exec_thread_id = threading.get_ident()

    def update_ui(text: str, is_warning: bool = False) -> None:
        if status_callback:
            status_callback(text, is_warning)
        else:
            try:
                from ui.views.running_dialog import RunningDialog
                RunningDialog.set_status(text, is_warning)
            except Exception:
                pass

    def on_freeze(step_info: str, elapsed: float, stack_trace: str) -> None:
        stop_workflow()
        restore_system_cursor(force=True)
        update_ui(f"フリーズ検知により強制終了 ({step_info})", is_warning=True)
        if on_freeze_callback:
            try:
                on_freeze_callback(step_info, elapsed, stack_trace)
            except Exception as e:
                logger.error(f"Failed to propagate on_freeze_callback: {e}")

    watchdog = ExecutionWatchdog(exec_thread_id, timeout_sec=30.0, on_freeze_callback=on_freeze)
    watchdog.start()

    mouse = MouseController()
    keyboard = KeyboardController()

    def _check_stop() -> None:
        if _stop_requested:
            raise WorkflowStoppedException("Execution aborted by user emergency stop.")

    _pressed_keys_for_stop = set()

    def on_press(key: Any) -> Optional[bool]:
        try:
            if hasattr(key, "char") and key.char is not None:
                key_name = str(key.char).lower()
            else:
                key_name = str(key).replace("Key.", "").lower()

            _pressed_keys_for_stop.add(key_name)
            has_ctrl = any(k in {"ctrl", "ctrl_l", "ctrl_r"} for k in _pressed_keys_for_stop)
            vk = getattr(key, "vk", None)
            is_backslash = key_name in {"\\", "¥", "yen", "\x1c"} or vk in {220, 226}

            if has_ctrl and is_backslash:
                logger.warning("Emergency stop shortcut (Ctrl+\\) triggered.")
                stop_workflow()
                return False
        except Exception:
            pass
        return None

    def on_release(key: Any) -> None:
        try:
            if hasattr(key, "char") and key.char is not None:
                key_name = str(key.char).lower()
            else:
                key_name = str(key).replace("Key.", "").lower()
            _pressed_keys_for_stop.discard(key_name)
        except Exception:
            pass

    listener = KeyboardListener(on_press=on_press, on_release=on_release)
    listener.start()

    try:
        from core.recorder.screen_capturer import get_macros_root
        macros_root = get_macros_root()
        target_dir = macros_root / workflow_id

        execution_log: Dict[str, Any] = {
            "workflow_id": workflow_id,
            "start_time": datetime.now().isoformat(),
            "start_step": 0,
            "steps": [],
            "status": "running"
        }

        executable_macro_path = target_dir / "executable_macro.json"
        variables_path = target_dir / "variables.json"

        macro_data: Dict[str, Any] = {}
        if executable_macro_path.exists():
            with open(executable_macro_path, "r", encoding="utf-8") as f:
                macro_data = json.load(f)
        elif temp_commands is None:
            raise FileNotFoundError(f"Missing executable_macro.json in {target_dir}")

        variables: Dict[str, Any] = {}
        if variables_path.exists():
            try:
                with open(variables_path, "r", encoding="utf-8") as f:
                    variables = json.load(f)
            except Exception as e:
                logger.warning(f"[{workflow_id}] Failed to load variables.json: {e}")

        commands = temp_commands if temp_commands is not None else macro_data.get("commands", [])

        step_dispatcher = StepDispatcher(
            workflow_id=workflow_id,
            target_dir=target_dir,
            commands=commands,
            mouse=mouse,
            keyboard=keyboard,
            status_callback=status_callback,
            check_stop_callback=_check_stop,
            is_stop_requested=lambda: _stop_requested,
            update_ui_callback=update_ui,
        )

        try:
            step_dispatcher.initialize_runtime_environment()
        except Exception as e:
            logger.warning(f"[{workflow_id}] Failed to initialize runtime environment: {e}")

        LoopController.preload_excel_variables(commands, target_dir, variables)

        i = 0
        loop_stack: List[Dict[str, Any]] = []

        while i < len(commands):
            _check_stop()
            cmd = commands[i]
            method = cmd.get("method")
            watchdog.heartbeat(f"Step {i+1}/{len(commands)}: {method}")

            step_msg = f"Step {i+1}/{len(commands)}: {method}"
            if loop_stack:
                step_msg += f" (Loop {loop_stack[-1]['current_iteration']+1}/{loop_stack[-1]['total_count']})"
            logger.info(f"[{workflow_id}] {step_msg}")
            update_ui(step_msg, False)

            if method == "loop_start":
                raw_args = cmd.get("args", {}).copy()
                resolved_args = resolve_variables(raw_args, variables)
                i = LoopController.handle_loop_start(i, commands, resolved_args, loop_stack, variables, target_dir, workflow_id)
                continue
            elif method == "loop_end":
                i = LoopController.handle_loop_end(i, loop_stack, variables)
                continue

            next_i, step_log, should_skip = step_dispatcher.dispatch_step(i, cmd, variables, loop_stack, step_msg)

            if should_skip:
                i = next_i
                continue

            if step_log:
                execution_log["steps"].append(step_log)
            i = next_i

        if step_dispatcher.macro_needs_save and temp_commands is None:
            try:
                with open(executable_macro_path, "w", encoding="utf-8") as f:
                    json.dump(macro_data, f, indent=4, ensure_ascii=False)
                logger.info(f"[{workflow_id}] Successfully saved healed coordinates to executable_macro.json for future runs.")
            except Exception as e:
                logger.error(f"[{workflow_id}] Failed to save healed macro to file: {e}")

        execution_log["status"] = "success"
        logger.info(f"[{workflow_id}] Macro execution finished successfully.")

    except WorkflowStoppedException as e:
        execution_log["status"] = "stopped"
        logger.warning(f"[{workflow_id}] {e}")
    except WorkflowFreezeException as e:
        execution_log["status"] = "frozen_aborted"
        err_msg = f"マクロの無応答（フリーズ）を検知したため強制終了しました (30秒タイムアウト)。\n発生箇所: {watchdog.current_step_info}"
        execution_log["error"] = err_msg
        logger.error(f"[{workflow_id}] {err_msg}")
        update_ui("フリーズ検知により強制終了しました", is_warning=True)
        raise RuntimeError(err_msg) from e
    except Exception as e:
        if "loop_stack" in locals() and loop_stack:
            LoopController.mark_current_iteration_error(loop_stack, str(e))
        execution_log["status"] = "failed"
        execution_log["error"] = str(e)
        logger.error(f"[{workflow_id}] Execution failed: {e}", exc_info=True)
        update_ui(f"エラー停止: {e}", is_warning=True)
        raise
    finally:
        watchdog.stop()
        restore_system_cursor(force=True)
        try:
            execution_log["end_time"] = datetime.now().isoformat()
            log_dir = target_dir / "execution_logs"
            log_dir.mkdir(parents=True, exist_ok=True)
            log_path = log_dir / f"run_log_{int(time.time())}.json"
            with open(log_path, "w", encoding="utf-8") as f:
                json.dump(execution_log, f, indent=4, ensure_ascii=False)
        except Exception as e:
            logger.error(f"Failed to save execution log: {e}")

        listener.stop()
        _is_running = False
        _stop_requested = False


def stop_workflow() -> None:
    global _stop_requested
    _stop_requested = True
    logger.warning("Emergency stop signal activated by user.")
