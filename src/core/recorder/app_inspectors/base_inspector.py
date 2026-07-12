# Role: アプリ固有インスペクターの抽象基底クラス定義

from abc import ABC, abstractmethod
from typing import Any, Dict, Optional

class BaseInspector(ABC):
    @abstractmethod
    def inspect(self, window_info: Dict[str, Any], x: Optional[int] = None, y: Optional[int] = None) -> Dict[str, Any]:
        """
        対象アプリケーション固有の詳細情報（タブ補完後の文字やURLなど）を取得して辞書で返す
        """
        pass