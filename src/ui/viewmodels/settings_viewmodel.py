"""Module: @role: 設定画面のUI状態を管理し、非ITユーザー向け3モード選択と自前サーバー接続検証を中継するViewModel。"""

import logging
import socket
import threading
from PySide6.QtCore import QObject, Signal, Slot
from pydantic import SecretStr, ValidationError

from src.models.data_types import (
    AppConfig,
    DEFAULT_CLOUD_HOST,
    DEFAULT_LOCAL_HOST,
    DEFAULT_LOCAL_LLM_PORT,
    DEFAULT_LOCAL_CV_PORT,
)
from src.utils.config_manager import ConfigManager

logger = logging.getLogger(__name__)


class SettingsViewModel(QObject):
    config_loaded = Signal(dict)
    save_successful = Signal()
    save_failed = Signal(str)
    connection_test_finished = Signal(bool, str)

    def __init__(self):
        super().__init__()
        self._current_config = AppConfig()

    @Slot()
    def load_current_settings(self):
        """現在の設定を読み込み、UIに反映させるためのシグナルを発行する"""
        try:
            self._current_config = ConfigManager.load_config()
            config_dict = self._current_config.model_dump()

            # 自前サーバー設定が空の場合はcv_hostから初期値を補完
            if not config_dict.get("custom_server_host") and self._current_config.ai_mode == "custom":
                config_dict["custom_server_host"] = self._current_config.cv_host
                config_dict["custom_server_port"] = self._current_config.cv_port

            if self._current_config.generator_api_key:
                config_dict['generator_api_key'] = self._current_config.generator_api_key.get_secret_value()
            if self._current_config.judge_api_key:
                config_dict['judge_api_key'] = self._current_config.judge_api_key.get_secret_value()

            self.config_loaded.emit(config_dict)
        except Exception as e:
            logger.error(f"設定のUI反映に失敗しました: {e}")
            self.save_failed.emit("設定の読み込みに失敗しました。")

    @Slot(str, str, str)
    def save_settings(self, ai_mode: str, custom_host: str = "", custom_port: str = "8843"):
        """3つのAIモードに応じたホスト・ポート解決を行い設定を保存する"""
        try:
            target_mode = ai_mode.strip()
            host = custom_host.strip()
            port = custom_port.strip() or "8843"

            if target_mode == "custom":
                if not host:
                    self.save_failed.emit("社内・自前サーバーのアドレス（IPアドレスまたはドメイン名）を入力してください。")
                    return
                resolved_llm_host = host
                resolved_llm_port = port
                resolved_cv_host = host
                resolved_cv_port = port
            elif target_mode == "cloud":
                resolved_llm_host = DEFAULT_CLOUD_HOST
                resolved_llm_port = DEFAULT_LOCAL_LLM_PORT
                resolved_cv_host = DEFAULT_CLOUD_HOST
                resolved_cv_port = DEFAULT_LOCAL_CV_PORT
            else:
                # このパソコンで動かす (local)
                target_mode = "local"
                resolved_llm_host = DEFAULT_LOCAL_HOST
                resolved_llm_port = DEFAULT_LOCAL_LLM_PORT
                resolved_cv_host = DEFAULT_LOCAL_HOST
                resolved_cv_port = DEFAULT_LOCAL_CV_PORT

            new_config = AppConfig(
                ai_mode=target_mode,
                custom_server_host=host,
                custom_server_port=port,
                llm_host=resolved_llm_host,
                llm_port=resolved_llm_port,
                cv_host=resolved_cv_host,
                cv_port=resolved_cv_port,
                vllm_host=self._current_config.vllm_host,
                vllm_port=self._current_config.vllm_port,
                generator_api_key=self._current_config.generator_api_key,
                judge_api_key=self._current_config.judge_api_key
            )

            ConfigManager.save_config(new_config)
            self._current_config = new_config
            self.save_successful.emit()

        except ValidationError as ve:
            error_msg = "入力値に誤りがあります:\n" + "\n".join([err['msg'] for err in ve.errors()])
            logger.warning(f"設定バリデーション失敗: {ve}")
            self.save_failed.emit(error_msg)
        except Exception as e:
            logger.error(f"設定保存エラー: {e}")
            self.save_failed.emit("設定の保存中にエラーが発生しました。")

    @Slot(str, str, str)
    def test_connection(self, ai_mode: str, custom_host: str = "", custom_port: str = "8843"):
        """指定された接続先サーバーへのTCPソケット疎通テストをバックグラウンド実行する"""
        def _worker():
            mode = ai_mode.strip()
            if mode == "custom":
                host = custom_host.strip()
                port_str = custom_port.strip() or "8843"
                if not host:
                    self.connection_test_finished.emit(False, "サーバーのアドレスが入力されていません。")
                    return
            elif mode == "cloud":
                host = DEFAULT_CLOUD_HOST
                port_str = DEFAULT_LOCAL_CV_PORT
            else:
                host = DEFAULT_LOCAL_HOST
                port_str = DEFAULT_LOCAL_CV_PORT

            try:
                port = int(port_str)
                with socket.create_connection((host, port), timeout=3.0):
                    self.connection_test_finished.emit(True, f"接続に成功しました（{host}:{port}）")
            except socket.timeout:
                self.connection_test_finished.emit(False, f"接続がタイムアウトしました。サーバーが起動しているか確認してください（{host}:{port_str}）。")
            except ConnectionRefusedError:
                self.connection_test_finished.emit(False, f"接続が拒否されました。サーバープログラムがポート {port_str} で待機しているか確認してください。")
            except Exception as e:
                self.connection_test_finished.emit(False, f"接続に失敗しました: {e}")

        threading.Thread(target=_worker, daemon=True).start()
