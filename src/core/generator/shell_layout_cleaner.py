"""Module: @role: OSシェル操作ノイズ除去、ウィンドウ移動に伴う座標系正規化、および先頭・末尾の不要移動イベント除去を担当する。"""

import logging
from typing import Any, Callable, Dict, List, Tuple

from core.generator.system_ui_filter import is_system_ui_event
from core.shared.window_utils import extract_app_name as _extract_app_name, is_system_shell_window

logger = logging.getLogger(__name__)


def is_business_action(evt: Dict[str, Any]) -> bool:
    act = evt.get("raw_action", "")
    role = str(evt.get("semantic_role", "")).lower()
    if act in ["click", "type_text", "excel_action", "browser_action"]:
        return True
    if "key" in act and role not in ["win", "cmd", "windows"]:
        return True
    return False


def extract_app_name(title: str) -> str:
    return _extract_app_name(title).lower()


def remove_os_shell_noise(
    temp_workflow_info: List[Dict[str, Any]],
    should_cancel: Callable[[], bool],
) -> List[Dict[str, Any]]:
    shell_cut_info: List[Dict[str, Any]] = []
    skip_until_new_window = False
    win_key_window_name = ""

    for info in temp_workflow_info:
        if should_cancel():
            raise InterruptedError("Generation cancelled by user")

        if is_system_ui_event(info):
            continue

        win_name = info.get("window_name", "")

        if skip_until_new_window:
            # Why: Google検索などのWebタイトルを誤判定せずOSシェルウィンドウのみスキップ
            is_system = is_system_shell_window(win_name, check_keywords_for_browser=False)

            # Why: 業務アクション開始または新ウィンドウ出現時にシェルスキップを即時解除
            if is_business_action(info) or (not is_system and win_name != win_key_window_name):
                skip_until_new_window = False
                # Why: シェル検索からアプリ起動した瞬間の確定Enterが新ウィンドウ先頭に漏れた場合は除外
                if (
                    not is_system
                    and win_name != win_key_window_name
                    and info.get("raw_action") in ["key_down", "key_press", "press_key"]
                    and str(info.get("semantic_role", "")).lower() in ["enter", "return"]
                ):
                    continue
            else:
                continue

        # Why: アプリ起動用Winキー検知時、起動前ウィンドウの無意味な残骸を除去
        if info.get("raw_action") in ["key_down", "key_press", "press_key"] and str(
            info.get("semantic_role", "")
        ).lower() in ["win", "cmd", "windows"]:
            skip_until_new_window = True
            win_key_window_name = win_name
            target_win_ops = [e for e in shell_cut_info if e.get("window_name") == win_name]
            if target_win_ops and not any(is_business_action(e) for e in target_win_ops):
                shell_cut_info = [e for e in shell_cut_info if e.get("window_name") != win_name]
            continue

        shell_cut_info.append(info)

    return shell_cut_info


def normalize_window_layout(
    temp_workflow_info: List[Dict[str, Any]],
    workflow_id: str,
) -> List[Dict[str, Any]]:
    app_first_business_op: Dict[str, int] = {}
    app_final_rect: Dict[str, Tuple[int, int, int, int]] = {}

    for i, info in enumerate(temp_workflow_info):
        app = extract_app_name(info.get("window_name", ""))
        if not app:
            continue
        raw_act = info.get("raw_action", "")
        raw_tp = str(info.get("raw_type", "")).lower()
        role = str(info.get("semantic_role", "")).lower()

        is_business = False
        if raw_act == "type_text" or "key" in raw_act:
            if role not in ["win", "cmd"]:
                is_business = True
        elif raw_act == "click" and "drag" not in raw_tp:
            raw_cy = info.get("cursor_y") if info.get("cursor_y") is not None else info.get("y")
            raw_wy = info.get("win_y")
            cy = 0 if raw_cy is None else raw_cy
            wy = 0 if raw_wy is None else raw_wy
            if cy - wy > 45 or info.get("excel_cell") or info.get("excel_dest_cell"):
                is_business = True

        if is_business and app not in app_first_business_op:
            app_first_business_op[app] = i

        wx = 0 if info.get("win_x") is None else info.get("win_x")
        wy = 0 if info.get("win_y") is None else info.get("win_y")
        ww = 0 if info.get("win_w") is None else info.get("win_w")
        wh = 0 if info.get("win_h") is None else info.get("win_h")
        if ww > 0 and wh > 0:
            app_final_rect[app] = (wx, wy, ww, wh)

    layout_cleaned: List[Dict[str, Any]] = []
    for info in temp_workflow_info:
        app = extract_app_name(info.get("window_name", ""))
        orig_wx = 0 if info.get("win_x") is None else info.get("win_x")
        orig_wy = 0 if info.get("win_y") is None else info.get("win_y")
        raw_cy = info.get("cursor_y") if info.get("cursor_y") is not None else info.get("y")
        raw_cx = info.get("cursor_x") if info.get("cursor_x") is not None else info.get("x")
        cy = 0 if raw_cy is None else raw_cy
        cx = 0 if raw_cx is None else raw_cx

        # Why: 業務ドラッグやメニュー・タブ操作を保護し、完全な画面外座標のみ除外
        if cy < -50 or cx < -50:
            logger.info(f"[{workflow_id}] Omitted off-screen action (Event: {info.get('event_id')}) at ({cx}, {cy})")
            continue

        if app in app_final_rect:
            final_x, final_y, final_w, final_h = app_final_rect[app]
            # Why: 移動前モニタの操作座標を最終ウィンドウの座標系へ安全に投影変換
            if cy < final_y or cy < -50 or abs(orig_wy - final_y) > 100:
                rel_cy = cy - orig_wy
                rel_cx = cx - orig_wx
                if 0 <= rel_cy <= final_h:
                    info["cursor_y"] = final_y + rel_cy
                    info["cursor_x"] = final_x + rel_cx
                    info["y"] = info["cursor_y"]
                    info["x"] = info["cursor_x"]
            info["win_x"] = final_x
            info["win_y"] = final_y
            info["win_w"] = final_w
            info["win_h"] = final_h

        layout_cleaned.append(info)

    return layout_cleaned


def trim_boundary_noise(temp_workflow_info: List[Dict[str, Any]]) -> List[Dict[str, Any]]:
    while temp_workflow_info:
        first_evt = temp_workflow_info[0]
        if first_evt.get("raw_action") == "move":
            diff = first_evt.get("diff_val", 0.0)
            elem = (first_evt.get("app_context") or {}).get("element_name")
            if diff < 0.005 and not elem:
                temp_workflow_info.pop(0)
            else:
                break
        else:
            break

    while temp_workflow_info:
        last_evt = temp_workflow_info[-1]
        # Why: 意図的なスクロールや有為なホバーを保護し停止操作由来の移動・システムUIのみ末尾除去
        if is_system_ui_event(last_evt) or last_evt.get("raw_action") in ["unknown", "uia_scan"]:
            temp_workflow_info.pop()
        elif last_evt.get("raw_action") == "move":
            diff = last_evt.get("diff_val", 0.0)
            prev_events_win = [e.get("window_name", "") for e in temp_workflow_info[:-1] if e.get("window_name")]
            is_new_win = bool(
                prev_events_win and last_evt.get("window_name") and last_evt.get("window_name") != prev_events_win[0]
            )
            if diff < 0.005 and not is_new_win and not (last_evt.get("app_context") or {}).get("element_name"):
                temp_workflow_info.pop()
            else:
                break
        else:
            break

    return temp_workflow_info
