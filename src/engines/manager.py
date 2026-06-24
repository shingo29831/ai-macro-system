# @role: ローカルAI APIサーバー（LLM, YOLO/OCR, vLLM）の独立したバックグラウンドプロセスとしてのライフサイクルを管理する。

import subprocess
import os
import atexit
import logging
import sys
import importlib.util
from typing import List

from utils.config_manager import ConfigManager

logger = logging.getLogger(__name__)

class LocalServerManager:
    def __init__(self) -> None:
        self._processes: List[subprocess.Popen] = []

    def start_servers(self) -> None:
        config = ConfigManager.load_config()

        ai_mode = config.ai_mode
        llm_host = config.llm_host
        llm_port = str(config.llm_port)
        cv_host = config.cv_host
        cv_port = str(config.cv_port)
        vllm_host = config.vllm_host
        vllm_port = str(config.vllm_port)

        env = os.environ.copy()
        
        src_path = os.path.abspath(os.path.join(os.path.dirname(__file__), ".."))
        env["PYTHONPATH"] = src_path + os.pathsep + env.get("PYTHONPATH", "")

        python_executable = sys.executable

        if ai_mode == 'local' and llm_host in ['127.0.0.1', 'localhost']:
            try:
                llm_cmd = [python_executable, '-m', 'uvicorn', 'engines.llm.server:app', '--port', llm_port]
                # stdout と stderr を sys.stdout/stderr に変更し、AIサーバーのログをコンソールに流す
                llm_proc = subprocess.Popen(llm_cmd, stdout=sys.stdout, stderr=sys.stderr, env=env, cwd=src_path)
                self._processes.append(llm_proc)
                logger.info(f"Local LLM server started on port {llm_port}")
            except Exception as e:
                logger.error(f"Failed to start local LLM server: {e}")

        if ai_mode == 'local' and cv_host in ['127.0.0.1', 'localhost']:
            try:
                cv_cmd = [python_executable, '-m', 'uvicorn', 'engines.yolo.server:app', '--port', cv_port]
                cv_proc = subprocess.Popen(cv_cmd, stdout=sys.stdout, stderr=sys.stderr, env=env, cwd=src_path)
                self._processes.append(cv_proc)
                logger.info(f"Local CV server started on port {cv_port}")
            except Exception as e:
                logger.error(f"Failed to start local CV server: {e}")

        if ai_mode == 'local' and vllm_host in ['127.0.0.1', 'localhost']:
            # vLLMはWindowsネイティブサポートが限定的なため、インストールされているか事前に確認する
            if importlib.util.find_spec('vllm') is None:
                logger.warning("vLLM module is not installed. Skipping local vLLM server startup.")
            else:
                try:
                    vllm_cmd = [python_executable, '-m', 'vllm.entrypoints.openai.api_server', '--port', vllm_port]
                    vllm_proc = subprocess.Popen(vllm_cmd, stdout=sys.stdout, stderr=sys.stderr, env=env, cwd=src_path)
                    self._processes.append(vllm_proc)
                    logger.info(f"Local vLLM server started on port {vllm_port}")
                except Exception as e:
                    logger.error(f"Failed to start local vLLM server: {e}")

        atexit.register(self.stop_servers)

    def stop_servers(self) -> None:
        for p in self._processes:
            if p.poll() is None:
                p.terminate()
                try:
                    p.wait(timeout=3.0)
                except subprocess.TimeoutExpired:
                    p.kill()
        self._processes.clear()
        logger.info("All local background servers stopped.")