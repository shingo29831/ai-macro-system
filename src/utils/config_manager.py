# @role: config.jsonの読み書きと、AppConfigモデルを用いたデータ検証をカプセル化する共通ユーティリティ。
# 
# 【参照元】
#   - ui/viewmodels/settings_viewmodel.py (設定保存時)
#   - engines/manager.py (ローカルサーバー起動時の設定参照)

import json
import os
import logging
from pydantic import ValidationError
from models.data_types import AppConfig

logger = logging.getLogger(__name__)

class ConfigManager:
    CONFIG_PATH = "config.json"

    @classmethod
    def load_config(cls) -> AppConfig:
        """設定ファイルを読み込み、検証済みのAppConfigモデルを返す。
        ファイルが存在しない、または内容が破損・不正な場合はデフォルト設定を返す。"""
        if not os.path.exists(cls.CONFIG_PATH):
            logger.info("config.jsonが見つからないため、デフォルト設定を使用します。")
            return AppConfig()

        try:
            with open(cls.CONFIG_PATH, 'r', encoding='utf-8') as f:
                data = json.load(f)
            return AppConfig(**data)
        except (json.JSONDecodeError, ValidationError) as e:
            logger.error(f"設定ファイルの読み込みまたは検証に失敗しました。デフォルト値で上書き/復旧します: {e}")
            return AppConfig()
        except Exception as e:
            logger.critical(f"予期せぬエラーが発生しました: {e}")
            raise

    @classmethod
    def save_config(cls, config: AppConfig) -> None:
        """検証済みのAppConfigモデルをJSONとしてファイルに永続化する。"""
        try:
            with open(cls.CONFIG_PATH, 'w', encoding='utf-8') as f:
                # model_dump()で辞書化し、人間が読みやすい形式(indent=4)で保存
                json.dump(config.model_dump(), f, indent=4, ensure_ascii=False)
            logger.info("システム設定を config.json に保存しました。")
        except Exception as e:
            logger.error(f"設定の保存処理に失敗しました: {e}")
            raise