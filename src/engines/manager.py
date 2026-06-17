# @role: ローカルAI APIサーバー（LLM, YOLO/OCR, vLLM）の独立したバックグラウンドプロセスとしてのライフサイクルを管理する。

import subprocess
import os
import atexit
import logging
import sys
from typing import List

from utils.config_manager import ConfigManager

logger = logging.getLogger(__name__)

class LocalServerManager:
    def __init__(self) -> None:
        self._processes: List[subprocess.Popen] = []

    def start_servers(self) -> None:
        # ConfigManagerを通じて現在の設定を安全に取得
        config = ConfigManager.load_config()

        ai_mode = config.ai_mode
        llm_host = config.llm_host
        llm_port = config.llm_port
        cv_host = config.cv_host
        cv_port = config.cv_port
        vllm_host = config.vllm_host
        vllm_port = config.vllm_port

        env = os.environ.copy()
        python_executable = sys.executable

        # ローカルLLMサーバーの起動
        if ai_mode == 'local' and llm_host in ['127.0.0.1', 'localhost']:
            try:
                llm_cmd = [python_executable, '-m', 'uvicorn', 'engines.llm.server:app', '--port', llm_port]
                llm_proc = subprocess.Popen(llm_cmd, stdout=subprocess.DEVNULL, stderr=subprocess.DEVNULL, env=env)
                self._processes.append(llm_proc)
                logger.info(f"Local LLM server started on port {llm_port}")
            except Exception as e:
                logger.error(f"Failed to start local LLM server: {e}")

        # YOLO/OCRサーバーの起動
        if cv_host in ['127.0.0.1', 'localhost']:
            try:
                cv_cmd = [python_executable, '-m', 'uvicorn', 'engines.yolo.server:app', '--port', cv_port]
                cv_proc = subprocess.Popen(cv_cmd, stdout=subprocess.DEVNULL, stderr=subprocess.DEVNULL, env=env)
                self._processes.append(cv_proc)
                logger.info(f"Local CV server started on port {cv_port}")
            except Exception as e:
                logger.error(f"Failed to start local CV server: {e}")

        # vLLMサーバーの起動 (Phase 3用)
        if ai_mode == 'local' and vllm_host in ['127.0.0.1', 'localhost']:
            try:
                vllm_cmd = [python_executable, '-m', 'vllm.entrypoints.openai.api_server', '--port', vllm_port]
                vllm_proc = subprocess.Popen(vllm_cmd, stdout=subprocess.DEVNULL, stderr=subprocess.DEVNULL, env=env)
                self._processes.append(vllm_proc)
                logger.info(f"Local vLLM server started on port {vllm_port}")
            except Exception as e:
                logger.error(f"Failed to start local vLLM server: {e}")

        # アプリケーション終了時にプロセス群を確実にクリーンアップ
        atexit.register(self.stop_servers)

    def stop_servers(self) -> None:
        for p in self._processes:
            if p.poll() is None:
                p.terminate()
                try:
                    # 強制終了前の猶予時間
                    p.wait(timeout=5.0)
                except subprocess.TimeoutExpired:
                    p.kill()
        self._processes.clear()
        logger.info("All local servers stopped.")