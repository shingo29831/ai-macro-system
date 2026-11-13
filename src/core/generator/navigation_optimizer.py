"""Module: @role: ワークフローイベント内のナビゲーションホバー、マウス移動・スクロール整流化、および検索フローの最適化を統括するファサード。"""

from core.generator.hover_navigator import is_residual_hover, promote_navigation_hover_to_click
from core.generator.move_scroll_cleaner import (
    cleanup_redundant_moves_and_scrolls,
    reorder_displaced_clicks_before_scroll,
)
from core.generator.search_flow_optimizer import optimize_typing_and_search_flow

__all__ = [
    "is_residual_hover",
    "promote_navigation_hover_to_click",
    "reorder_displaced_clicks_before_scroll",
    "optimize_typing_and_search_flow",
    "cleanup_redundant_moves_and_scrolls",
]

