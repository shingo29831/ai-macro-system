# @role: 設定画面(Settings Dialog)のUI状態を管理し、入力値の検証とConfigManagerへの保存要求を中継するViewModel。

import logging
from PySide6.QtCore import QObject, Signal, Slot
from pydantic import ValidationError
from models.data_types import AppConfig
from utils.config_manager import ConfigManager

logger = logging.getLogger(__name__)

class SettingsViewModel(QObject):
    # UIへの通知用シグナル
    config_loaded = Signal(dict)          # ロード完了時にUIへ値を渡す
    save_successful = Signal()            # 保存成功通知
    save_failed = Signal(str)             # 保存失敗・バリデーションエラー通知

    def __init__(self):
        super().__init__()
        self._current_config = AppConfig()

    @Slot()
    def load_current_settings(self):
        """現在の設定を読み込み、UIに反映させるためのシグナルを発行する"""
        try:
            self._current_config = ConfigManager.load_config()
            self.config_loaded.emit(self._current_config.model_dump())
        except Exception as e:
            logger.error(f"設定のUI反映に失敗しました: {e}")
            self.save_failed.emit("設定の読み込みに失敗しました。")

    @Slot(str, str, str, str, str)
    def save_settings(self, ai_mode: str, llm_host: str, llm_port: str, cv_host: str, cv_port: str):
        """UIからの入力値を受け取り、検証後に保存する"""
        try:
            # AppConfigの初期化時に自動でバリデーション(pydantic)が走る
            new_config = AppConfig(
                ai_mode=ai_mode,
                llm_host=llm_host,
                llm_port=llm_port,
                cv_host=cv_host,
                cv_port=cv_port
            )
            
            # 検証に成功した場合のみ保存処理を実行
            ConfigManager.save_config(new_config)
            self._current_config = new_config
            self.save_successful.emit()
            
        except ValidationError as ve:
            # バリデーションエラー（不正なIPやポート等）のハンドリング
            error_msg = "入力値に誤りがあります。\n" + "\n".join([err['msg'] for err in ve.errors()])
            logger.warning(f"設定のバリデーションに失敗: {ve}")
            self.save_failed.emit(error_msg)
            
        except Exception as e:
            logger.error(f"設定の保存中に予期せぬエラーが発生: {e}")
            self.save_failed.emit("システムエラーが発生し、保存できませんでした。")