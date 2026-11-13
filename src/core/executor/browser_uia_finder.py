"""Module: @role: ブラウザ操作におけるUIA要素探索・スクロール可視化・値読み取り・待機を統合提供するファサード。"""

from core.executor.browser_uia_reader import (
    get_combobox_current_value,
    read_attr_by_uia,
    read_text_by_uia_or_selector,
    wait_element_exist,
)
from core.executor.browser_uia_scroller import SIMPLE_GENERIC_TAGS, scroll_into_view
from core.executor.browser_uia_searcher import GENERIC_HTML_TAGS, find_uia_element

__all__ = [
    "SIMPLE_GENERIC_TAGS",
    "GENERIC_HTML_TAGS",
    "find_uia_element",
    "scroll_into_view",
    "get_combobox_current_value",
    "read_text_by_uia_or_selector",
    "read_attr_by_uia",
    "wait_element_exist",
]
