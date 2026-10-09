"""Module: @role: マクロ実行中のフリーズ検知、スレッドスタックトレース取得、および強制例外注入を担う。"""

import ctypes
import logging
import platform
import sys
import threading
import time
import traceback
from typing import Callable, Optional

logger = logging.getLogger(__name__)


class WorkflowStoppedException(Exception):
    """ユーザーによってマクロの実行が強制停止された場合に送出される例外"""
    pass


class WorkflowFreezeException(Exception):
    """ステップ実行中に無応答（フリーズ）が検知された場合に送出される例外"""
    pass


def get_thread_stack_trace(thread_id: int) -> str:
    # Why: フリーズ発生時にスレッドがどの関数の何行目で止まっていたかを外部から強制ダンプ
    frames = sys._current_frames()
    frame = frames.get(thread_id)
    if frame is not None:
        return "".join(traceback.format_stack(frame))
    return "スタックフレームの取得に失敗しました（スレッドが存在しないか既に終了しています）。"


def inject_async_exception(thread_id: int, exc_type: type) -> None:
    # Why: ブロッキング中の実行スレッドに非同期例外を注入しフリーズから強制脱出
    if not thread_id or platform.system() != "Windows":
        return
    try:
        res = ctypes.pythonapi.PyThreadState_SetAsyncExc(
            ctypes.c_ulong(thread_id), ctypes.py_object(exc_type)
        )
        if res > 1:
            ctypes.pythonapi.PyThreadState_SetAsyncExc(ctypes.c_ulong(thread_id), None)
    except Exception as e:
        logger.error(f"Failed to inject async exception: {e}")


class ExecutionWatchdog:
    """実行ステップの無応答（フリーズ）を常時監視し、規定秒数超過時に強制終了とスタックダンプを実行する"""
    def __init__(self, target_thread_id: int, timeout_sec: float = 30.0, on_freeze_callback: Optional[Callable[[str, float, str], None]] = None) -> None:
        self.target_thread_id = target_thread_id
        self.timeout_sec = timeout_sec
        self.on_freeze_callback = on_freeze_callback
        self.last_beat_time = time.time()
        self.current_step_info = "初期化中"
        self._is_active = True
        self._lock = threading.Lock()
        self._monitor_thread = threading.Thread(target=self._monitor_loop, daemon=True, name="ExecWatchdog")

    def start(self) -> None:
        self.last_beat_time = time.time()
        self._monitor_thread.start()

    def heartbeat(self, step_info: str) -> None:
        with self._lock:
            self.last_beat_time = time.time()
            self.current_step_info = step_info

    def stop(self) -> None:
        with self._lock:
            self._is_active = False

    def _monitor_loop(self) -> None:
        while True:
            time.sleep(1.0)
            with self._lock:
                if not self._is_active:
                    break
                elapsed = time.time() - self.last_beat_time
                if elapsed > self.timeout_sec:
                    frozen_step = self.current_step_info
                    self._is_active = False
                    
                    frozen_stack = get_thread_stack_trace(self.target_thread_id)
                    logger.error(
                        f"Watchdog: Step freeze detected! No heartbeat for {elapsed:.1f}s at: {frozen_step}\n"
                        f"Frozen Thread Stack Trace:\n{frozen_stack}"
                    )
                    
                    if self.on_freeze_callback:
                        try:
                            self.on_freeze_callback(frozen_step, elapsed, frozen_stack)
                        except Exception as cb_err:
                            logger.error(f"Error in on_freeze_callback: {cb_err}")

                    inject_async_exception(self.target_thread_id, WorkflowFreezeException)
                    break
