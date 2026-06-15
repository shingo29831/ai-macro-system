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
    """サブプロセスを用いたローカル推論サーバーのライフサイクル管理者"""

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