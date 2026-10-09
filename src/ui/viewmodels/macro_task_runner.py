"""Module: @role: AIマクロ生成タスクおよびマクロ実行タスクの非同期スレッド制御、進捗中継、および例外監視を担当する。"""

from typing import Any, Callable, Dict, List, Optional
import logging
import threading
import traceback

from core.executor import runner
from core.generator import log_integrator
from core.recorder import os_hook
from utils.config_manager import ConfigManager

logger = logging.getLogger(__name__)


class MacroTaskRunner:
    """非同期タスク（録画停止後のAI生成、およびマクロ自律実行）のワーカースレッドを統括するクラス"""

    @staticmethod
    def start_generation_task(
        is_cancel_requested: Callable[[], bool],
        on_progress: Callable[[int, str], None],
        on_finished: Callable[[bool, str, str], None],
        on_error: Callable[[str], None],
    ) -> threading.Thread:
        def background_stop_task() -> None:
            try:
                os_hook.stop_recording()
                workflow_id = os_hook.get_current_workflow_id()

                if not workflow_id:
                    try:
                        from core.recorder.screen_capturer import get_macros_root
                        macros_root = get_macros_root()
                        wf_dirs = sorted([d for d in macros_root.glob("wf_*") if d.is_dir()], key=lambda x: x.stat().st_mtime)
                        if wf_dirs:
                            workflow_id = wf_dirs[-1].name
                    except Exception as e:
                        logger.error(f"Failed to fallback workflow_id: {e}")

                if workflow_id:
                    app_config = ConfigManager.load_config()
                    logger.info(f"Kicking background macro generation workflow for ID: {workflow_id}")

                    log_integrator.generate_macro_workflow(
                        workflow_id,
                        app_config,
                        progress_callback=on_progress,
                        check_cancel_callback=is_cancel_requested
                    )

                    logger.info(f"Background macro generation successfully completed for ID: {workflow_id}")
                    on_finished(True, "", workflow_id)
                else:
                    msg = "Recording stopped, but target workflow_id could not be resolved from os_hook."
                    logger.warning(msg)
                    on_finished(False, msg, "")

            except InterruptedError:
                logger.warning("Background macro generation cancelled.")
                on_finished(False, "キャンセルされました", "")
            except Exception as gen_err:
                tb_str = traceback.format_exc()
                err_msg = str(gen_err)
                detail_err = f"{type(gen_err).__name__}: {gen_err}\n\n【詳細スタックトレース】\n{tb_str}"
                logger.error(f"Unhandled exception during background macro generation: {detail_err}")
                on_error(detail_err)
                on_finished(False, err_msg, "")

        thread = threading.Thread(target=background_stop_task, daemon=True)
        thread.start()
        return thread

    @staticmethod
    def start_execution_task(
        workflow_id: str,
        commands: Optional[List[Dict[str, Any]]],
        status_callback: Callable[[str, bool], None],
        on_finished: Callable[[], None],
        on_error: Callable[[str], None],
    ) -> threading.Thread:
        app_config = ConfigManager.load_config()

        def background_execution() -> None:
            freeze_handled = threading.Event()

            def on_freeze_detected(step_info: str, elapsed: float, stack_trace: str) -> None:
                if freeze_handled.is_set():
                    return
                freeze_handled.set()

                err_msg = (
                    f"マクロ実行の無応答（フリーズ）を検知したため強制終了しました ({elapsed:.1f}秒タイムアウト)。\n\n"
                    f"【発生箇所】\n{step_info}\n\n"
                    f"【フリーズ発生時のスタックトレース（原因箇所）】\n{stack_trace}"
                )
                status_callback("フリーズ検知により強制終了", True)
                on_error(err_msg)
                on_finished()

            try:
                runner.run_workflow(
                    workflow_id,
                    app_config,
                    status_callback=status_callback,
                    temp_commands=commands,
                    on_freeze_callback=on_freeze_detected
                )
                logger.info(f"Macro execution finished successfully for ID: {workflow_id}")
            except runner.WorkflowStoppedException as stop_err:
                logger.warning(f"Macro execution stopped by user: {stop_err}")
            except runner.WorkflowFreezeException:
                pass
            except Exception as exec_err:
                if not freeze_handled.is_set():
                    tb_str = traceback.format_exc()
                    err_msg = f"{type(exec_err).__name__}: {exec_err}\n\n【詳細スタックトレース】\n{tb_str}"
                    logger.error(f"Exception occurred during pipeline execution for {workflow_id}:\n{tb_str}")
                    status_callback(f"エラー: {exec_err}", True)
                    on_error(err_msg)
            finally:
                if not freeze_handled.is_set():
                    on_finished()

        thread = threading.Thread(target=background_execution, daemon=True)
        thread.start()
        return thread
