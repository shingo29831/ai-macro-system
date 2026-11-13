"""Module: @role: 統合イベント列に対し、各種専用オプティマイザ（Office・シェル・ナビゲーション・フォーム・ループ・ブラウザ昇格）を順次実行する純粋オーケストレーター。"""

import time
from typing import Any, Callable, Dict, List, Optional, Tuple

from core.generator.browser_action_promoter import (
    bind_click_context_to_inputs,
    clean_post_promotion_keys,
    inherit_click_context_before_form,
    promote_browser_actions,
)
from core.generator.form_optimizer import consolidate_web_form_interactions
from core.generator.loop_detector import detect_and_optimize_loops
from core.generator.navigation_optimizer import (
    cleanup_redundant_moves_and_scrolls,
    optimize_typing_and_search_flow,
    promote_navigation_hover_to_click,
    reorder_displaced_clicks_before_scroll,
)
from core.generator.office_optimizer import (
    clean_office_events,
    filter_web_excel_conflicts,
    propagate_excel_cells,
)
from core.generator.shell_layout_cleaner import (
    normalize_window_layout,
    remove_os_shell_noise,
    trim_boundary_noise,
)


def optimize_workflow_events(
    temp_workflow_info: List[Dict[str, Any]],
    workflow_id: str,
    check_cancel_callback: Optional[Callable[[], bool]] = None,
    config: Optional[Any] = None,
    strategy: str = "hybrid",
) -> Tuple[List[Dict[str, Any]], Dict[str, str]]:
    last_cancel_check_time = time.time()

    def should_cancel() -> bool:
        nonlocal last_cancel_check_time
        current_time = time.time()
        if current_time - last_cancel_check_time > 0.1:
            last_cancel_check_time = current_time
            if check_cancel_callback and check_cancel_callback():
                return True
        return False

    temp_workflow_info = clean_office_events(temp_workflow_info, should_cancel)
    temp_workflow_info = remove_os_shell_noise(temp_workflow_info, should_cancel)
    temp_workflow_info = normalize_window_layout(temp_workflow_info, workflow_id)

    # Why: 推測によるスクロール捏造を撤廃しユーザーの実スクロール記録のみを厳密採用
    temp_workflow_info = optimize_typing_and_search_flow(temp_workflow_info)
    temp_workflow_info = promote_navigation_hover_to_click(temp_workflow_info)
    temp_workflow_info = reorder_displaced_clicks_before_scroll(temp_workflow_info)
    temp_workflow_info = cleanup_redundant_moves_and_scrolls(temp_workflow_info)

    inherit_click_context_before_form(temp_workflow_info)

    # Why: ビジュアル優先戦略時は過剰なWebフォーム集約を抑止し物理入力構造を保護
    if strategy != "visual":
        temp_workflow_info = consolidate_web_form_interactions(temp_workflow_info, config=config)

    temp_workflow_info = trim_boundary_noise(temp_workflow_info)
    temp_workflow_info = detect_and_optimize_loops(temp_workflow_info, workflow_id, should_cancel)

    temp_workflow_info = propagate_excel_cells(temp_workflow_info)
    temp_workflow_info = filter_web_excel_conflicts(temp_workflow_info)

    bind_click_context_to_inputs(temp_workflow_info)

    promoted_browser_info = promote_browser_actions(temp_workflow_info, strategy)
    cleaned_after_promoted = clean_post_promotion_keys(promoted_browser_info)

    # Why: 意図しない自動変数置換を完全廃止しユーザー入力の確定値をそのまま出力
    return cleaned_after_promoted, {}
