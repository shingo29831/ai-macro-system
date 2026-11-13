"""Module: @role: マクロステップ実行前の画面照合、スクロール位置合わせ、要素可視化、およびUI自己修復検証を担当する。"""

import ctypes
import logging
import platform
from pathlib import Path
from typing import Any, Callable, Dict, List, Optional, Tuple

from core.executor.excel_handler import skip_failed_loop_step as _skip_failed_loop_step
from core.executor.input_helper import get_window_offset, wait_for_screen_settle
from core.executor.screen_matcher import align_scroll_by_screen_match, wait_for_screen_match
from core.executor.target_healer import verify_and_heal_target

logger = logging.getLogger(__name__)

BROWSER_KEYWORDS = ("firefox", "chrome", "edge", "brave", "opera")


def detect_browser_target(commands: List[Dict[str, Any]]) -> bool:
    """全体コマンドから主要ブラウザ操作の有無を包括判定する。"""
    has_browser_window = any(
        any(b in str(c.get("args", {}).get("window_title", "")).lower() for b in BROWSER_KEYWORDS)
        for c in commands
        if c.get("method") == "activate_window"
    )
    return has_browser_window or any(c.get("method") == "browser_action" for c in commands)


def propagate_mapped_hwnd(
    commands: List[Dict[str, Any]],
    activated_hwnd: int,
    first_args: Dict[str, Any],
) -> None:
    """初回取得したHWNDを同一アプリ・同一エイリアスの後続activate_windowへ伝播する。"""
    if not activated_hwnd:
        return
    window_title = first_args.get("window_title", "")
    app_name = window_title.split("—")[-1].split("-")[-1].strip().lower()
    first_alias = first_args.get("window_alias")

    # Why: ループ毎新規起動指定時は初回HWNDでコマンド引数を固定せず都度起動を保証
    if not first_args.get("loop_launch_each_time"):
        first_args["mapped_hwnd"] = activated_hwnd

    for cmd in commands:
        if cmd.get("method") != "activate_window":
            continue
        cmd_args = cmd.setdefault("args", {})
        cmd_app = cmd_args.get("window_title", "").split("—")[-1].split("-")[-1].strip().lower()
        if cmd_app == app_name or (first_alias and cmd_args.get("window_alias") == first_alias):
            if not cmd_args.get("loop_launch_each_time"):
                cmd_args["mapped_hwnd"] = activated_hwnd


def align_scroll_if_needed(
    commands: List[Dict[str, Any]],
    step_index: int,
    method: str,
    raw_event_id: Optional[str],
    is_browser_target: bool,
    last_win_args: Optional[Dict[str, Any]],
    current_win_rect: Tuple[int, int, int, int],
    target_dir: Path,
    workflow_id: str,
    status_callback: Optional[Callable[[str, bool], None]],
    is_stop_requested: Callable[[], bool],
) -> None:
    """先行スクロールが存在する場合、記録時画像と一致する位置までスクロールを合わせる。"""
    has_scroll_in_macro = any(c.get("method") == "scroll" for c in commands)
    has_prior_scroll = any(commands[k].get("method") == "scroll" for k in range(max(0, step_index - 3), step_index))
    if not (
        raw_event_id
        and method in ["click", "move", "type_text", "browser_action"]
        and is_browser_target
        and has_scroll_in_macro
        and has_prior_scroll
    ):
        return

    target_hwnd = (last_win_args.get("mapped_hwnd") if last_win_args else None) or 0
    cur_x, cur_y, cur_w, cur_h = current_win_rect
    scrolled_ok, match_sc = align_scroll_by_screen_match(
        target_dir=target_dir,
        raw_event_id=raw_event_id,
        win_x=cur_x,
        win_y=cur_y,
        win_w=cur_w,
        win_h=cur_h,
        target_hwnd=target_hwnd,
        status_callback=status_callback,
        check_cancel_callback=is_stop_requested,
    )
    if scrolled_ok:
        logger.info(f"[{workflow_id}] Visual scroll aligned with step {step_index + 1} pre-image: {match_sc}")


def scroll_browser_element_into_view(
    method: str,
    args: Dict[str, Any],
    is_browser_target: bool,
    last_win_args: Optional[Dict[str, Any]],
    current_win_rect: Tuple[int, int, int, int],
    workflow_id: str,
) -> None:
    """ブラウザ操作対象がビューポート外にある場合、要素をスクロールして座標を再取得する。"""
    cur_sel = str(args.get("selector") or "").strip().lower()
    is_generic = cur_sel in ["div", "span", "p", "a", "li", "ul", "body"]
    is_coords_visible = (
        args.get("x") is not None and args.get("y") is not None and args.get("x") > 20 and args.get("y") > 45
    )
    if not (
        is_browser_target
        and method in ["click", "type_text", "move"]
        and (args.get("selector") or args.get("element_name") or args.get("url"))
        and not is_generic
        and not is_coords_visible
    ):
        return

    try:
        from core.executor.browser_controller import BrowserController

        bc = BrowserController.get_instance()
        target_hwnd_for_offset = (last_win_args.get("mapped_hwnd") if last_win_args else None) or (
            ctypes.windll.user32.GetForegroundWindow() if platform.system() == "Windows" else None
        )
        cur_x, cur_y, _, _ = current_win_rect
        pre_off_x, pre_off_y = get_window_offset(target_hwnd_for_offset, cur_x, cur_y)
        new_coords = bc.scroll_into_view(
            selector=args.get("selector"),
            last_win_args=last_win_args,
            timeout_sec=0.8,
            element_name=args.get("element_name"),
            url=args.get("url"),
            text=args.get("text"),
            x=args.get("x"),
            y=args.get("y"),
        )
        if new_coords:
            args["x"] = new_coords[0] - pre_off_x
            args["y"] = new_coords[1] - pre_off_y
            logger.info(
                f"[{workflow_id}] Element scrolled into view. Updated coords to ({new_coords[0]}, {new_coords[1]})"
            )
    except Exception as sc_err:
        logger.debug(f"[{workflow_id}] Auto scroll_into_view bypassed: {sc_err}")


def perform_screen_matching(
    step_index: int,
    args: Dict[str, Any],
    raw_event_id: str,
    loop_stack: List[Dict[str, Any]],
    step_log: Dict[str, Any],
    step_msg: str,
    target_dir: Path,
    current_win_rect: Tuple[int, int, int, int],
    workflow_id: str,
    status_callback: Optional[Callable[[str, bool], None]],
    check_stop_callback: Callable[[], None],
    is_stop_requested: Callable[[], bool],
    update_ui_callback: Callable[[str, bool], None],
) -> None:
    """ステップ実行前の画面一致待機（ループ内は描画安定待機）を実施する。"""
    if loop_stack:
        wait_for_screen_settle(timeout=1.5, settle_threshold=0.004)
        return

    match_eid = args.get("match_event_id") or raw_event_id
    cur_x, cur_y, cur_w, cur_h = current_win_rect
    match_info = wait_for_screen_match(
        target_dir,
        match_eid,
        cur_x,
        cur_y,
        cur_w,
        cur_h,
        workflow_id,
        status_callback,
        step_index,
        timeout=1.5,
        check_cancel_callback=is_stop_requested,
    )
    check_stop_callback()
    step_log["match_info"] = match_info
    update_ui_callback(step_msg, False)


def heal_target_or_skip(
    step_index: int,
    commands: List[Dict[str, Any]],
    args: Dict[str, Any],
    target_id: str,
    raw_event_id: str,
    step_log: Dict[str, Any],
    loop_stack: List[Dict[str, Any]],
    current_win_rect: Tuple[int, int, int, int],
    last_win_args: Optional[Dict[str, Any]],
    target_dir: Path,
    workflow_id: str,
    status_callback: Optional[Callable[[str, bool], None]],
    check_stop_callback: Callable[[], None],
    update_ui_callback: Callable[[str, bool], None],
) -> Tuple[int, bool, bool]:
    """UIターゲットの検証・自己修復を行い、(next_i, aborted_for_skip, healed) を返す。"""
    current_win_x, current_win_y, _, _ = current_win_rect
    target_hwnd_for_offset = (last_win_args.get("mapped_hwnd") if last_win_args else None) or 0
    off_x, off_y = get_window_offset(target_hwnd_for_offset, current_win_x, current_win_y)

    recovered_ok, recovery_info = verify_and_heal_target(
        workflow_id=workflow_id,
        target_id=target_id,
        raw_event_id=raw_event_id,
        args=args,
        target_dir=target_dir,
        last_win_args=last_win_args,
        status_callback=status_callback,
        check_stop_callback=check_stop_callback,
        window_offset=(off_x, off_y),
    )
    healed = False
    if recovery_info:
        step_log["recovery_info"] = recovery_info
        if recovered_ok:
            healed = True
        else:
            if loop_stack and loop_stack[-1].get("continue_on_error"):
                skipped_i = _skip_failed_loop_step(
                    step_index, commands, loop_stack, workflow_id, update_ui_callback, "UI検出失敗"
                )
                return skipped_i, True, False
            raise RuntimeError("対象のUIが見つからず、自己修復にも失敗したためマクロを安全停止しました。")
    return step_index + 1, False, healed
