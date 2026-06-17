# @role: ローカルAI(LLM)およびComputer Vision(YOLO/OCR)のAPIサーバーを、独立したバックグラウンドプロセスとして起動・終了管理する。
#
# 【背景】
#   - AIモデルは初期ロードに時間がかかるため、アプリ起動時にウォームアップしておく。
#   - AppConfigの状態（ローカルかクラウドか、ホスト先はどこか）に基づき、必要なサーバーのみを自律的に起動する。

import subprocess
import os
import atexit
import logging
from typing import List
from models.data_types import AppConfig

logger = logging.getLogger(__name__)

class LocalServerManager:
    """サブプロセスを用いたローカル推論サーバーのライフサ# @role: ローカルAI APIサーバー（LLM, YOLO/OCR, vLLM）の独立したバックグラウンドプロセスとしてのライフサイクルを管理する。

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
        logger.info("All local servers stopped.")イクル管理者"""

    def __init__(self) -> None:
        self._processes: List[subprocess.Popen] = []

    def start_servers(self, config: AppConfig) -> None:
        """設定を評価し、要件を満たす場合のみローカルサーバープロセスを起動する"""
        
        # 既に起動中のプロセスがあればクリーンアップ
        self.stop_servers()

        # 実行環境の環境変数を継承
        env = os.environ.copy()

        # LLMサーバーの起動判定: モードが'local'であり、かつホスト先がローカルマシンの場合
        if config.ai_mode == 'local' and config.llm_host in ['127.0.0.1', 'localhost']:
            try:
                llm_cmd = ['python', '-m', 'uvicorn', 'engines.llm.server:app', '--port', config.llm_port]
                # UI側のコンソールを汚さないよう出力をDEVNULLにリダイレクト
                llm_proc = subprocess.Popen(llm_cmd, stdout=subprocess.DEVNULL, stderr=subprocess.DEVNULL, env=env)
                self._processes.append(llm_proc)
                logger.info(f"Local LLM server started on port {config.llm_port} as PID {llm_proc.pid}")
            except Exception as e:
                logger.error(f"Failed to start local LLM server: {e}")

        # CV(YOLO/OCR)サーバーの起動判定: ホスト先がローカルマシンの場合 (AIモード設定とは独立して評価)
        if config.cv_host in ['127.0.0.1', 'localhost']:
            try:
                # TODO: CVチームのサーバーモジュール(engines.yolo.server)が完成次第、適切なパスに変更する
                # cv_cmd = ['python', '-m', 'uvicorn', 'engines.yolo.server:app', '--port', config.cv_port]
                # cv_proc = subprocess.Popen(cv_cmd, stdout=subprocess.DEVNULL, stderr=subprocess.DEVNULL, env=env)
                # self._processes.append(cv_proc)
                # logger.info(f"Local CV server started on port {config.cv_port} as PID {cv_proc.pid}")
                pass
            except Exception as e:
                logger.error(f"Failed to start local CV server: {e}")

        # アプリケーション終了時（強制終了含む）に確実にサブプロセスを道連れにしてキルする
        atexit.register(self.stop_servers)

    def stop_servers(self) -> None:
        """管理下にあるすべてのローカルサーバープロセスを強制終了する"""
        for p in self._processes:
            # プロセスがまだ生きているか確認 (poll() が None なら実行中)
            if p.poll() is None:
                p.terminate()
                try:
                    # 終了を待機（最大3秒）し、応じなければキル
                    p.wait(timeout=3)
                except subprocess.TimeoutExpired:
                    p.kill()
        
        self._processes.clear()
        
        # 登録したatexitハンドラが重複しないように解除を試みる
        try:
            atexit.unregister(self.stop_servers)
        except AttributeError:
            pass