"""Module: @role: Office/Excel操作イベント（入力確定、セル選択移動）のクリーンアップ、物理入力巻き戻し、およびセル情報伝播を担当する。"""

from typing import Any, Callable, Dict, List, Optional
import logging
import re

logger = logging.getLogger(__name__)


def clean_office_events(
    temp_workflow_info: List[Dict[str, Any]],
    should_cancel: Optional[Callable[[], bool]] = None,
) -> List[Dict[str, Any]]:
    """Excelの入力確定・選択移動イベントを解析し、重複する物理操作を巻き戻してexcel_actionへ変換する"""
    cleaned_workflow_info: List[Dict[str, Any]] = []

    for info in temp_workflow_info:
        if should_cancel and should_cancel():
            raise InterruptedError("Generation cancelled by user")

        if info.get("raw_action") == "office_event":
            msg = info.get("semantic_role", "")
            if "入力確定" in msg:
                match = re.search(r"セル:\s*([^\s|]+)\s*\|\s*値:\s*(.+)", msg)
                if match:
                    cell = match.group(1).replace("$", "")
                    val = match.group(2).strip()
                    if val.endswith(".0"):
                        val = val[:-2]

                    is_duplicate = False
                    current_window = info.get("window_name", "")
                    for i in range(len(cleaned_workflow_info) - 1, -1, -1):
                        prev_info = cleaned_workflow_info[i]
                        if prev_info.get("window_name", "") != current_window:
                            break
                        if prev_info.get("raw_action") == "type_text":
                            if prev_info.get("excel_cell") == cell and prev_info.get("semantic_role") == val:
                                is_duplicate = True
                            break
                        elif prev_info.get("excel_dest_cell"):
                            break

                    if not is_duplicate:
                        idx_to_remove: List[int] = []
                        insert_idx = len(cleaned_workflow_info)
                        for i in range(len(cleaned_workflow_info) - 1, -1, -1):
                            prev_info = cleaned_workflow_info[i]
                            if prev_info.get("window_name", "") != current_window:
                                break
                            if prev_info.get("excel_dest_cell") or prev_info.get("excel_cell"):
                                break
                            if prev_info.get("raw_action") in ["key_down", "type_text", "click", "move"]:
                                role = str(prev_info.get("semantic_role", "")).lower()
                                if role in ["enter", "tab", "esc", "up", "down", "left", "right"] or role.startswith("key.") or "+" in role:
                                    if not idx_to_remove:
                                        insert_idx = i
                                    else:
                                        break
                                else:
                                    idx_to_remove.append(i)
                            else:
                                break

                        if idx_to_remove and insert_idx == len(cleaned_workflow_info):
                            insert_idx = min(idx_to_remove)

                        # Why: 入力前の孤立したEnterキー（起動直後等）を除去しA1セルの入力抜け・二重Enterを防止
                        if insert_idx > 0:
                            prior = cleaned_workflow_info[insert_idx - 1]
                            if prior.get("raw_action") in ["key_down", "press_key"] and str(prior.get("semantic_role", "")).lower() in ["enter", "return"]:
                                if not prior.get("excel_dest_cell") and not prior.get("excel_cell"):
                                    cleaned_workflow_info.pop(insert_idx - 1)
                                    insert_idx -= 1
                                    idx_to_remove = [idx - 1 for idx in idx_to_remove]

                        if idx_to_remove:
                            first_typed = cleaned_workflow_info[min(idx_to_remove)]
                            if first_typed.get("pre_img_path"):
                                info["pre_img_path"] = first_typed["pre_img_path"]
                            if first_typed.get("event_id"):
                                info["event_id"] = first_typed["event_id"]

                        # Why: Excelセル書き込みを物理入力ではなく専用アクション(excel_action)として記録
                        info["raw_action"] = "excel_action"
                        info["raw_type"] = "excel_action"
                        info["action"] = "write_cell"
                        info["cell"] = cell
                        info["value"] = val
                        info["semantic_role"] = val
                        info["excel_cell"] = cell

                        cleaned_workflow_info.insert(insert_idx, info)

                        for i in sorted(idx_to_remove, reverse=True):
                            if i >= insert_idx:
                                cleaned_workflow_info.pop(i + 1)
                            else:
                                cleaned_workflow_info.pop(i)

            elif "選択移動" in msg:
                match = re.search(r"セル:\s*([^\s|]+)", msg)
                if match:
                    cell = match.group(1).replace("$", "")

                    is_duplicate = False
                    current_window = info.get("window_name", "")
                    for i in range(len(cleaned_workflow_info) - 1, -1, -1):
                        prev_info = cleaned_workflow_info[i]
                        if prev_info.get("window_name", "") != current_window:
                            break
                        if prev_info.get("raw_action") == "click" and prev_info.get("excel_dest_cell"):
                            if prev_info.get("excel_dest_cell") == cell:
                                is_duplicate = True
                            break
                        elif prev_info.get("raw_action") == "type_text" and prev_info.get("excel_cell"):
                            if prev_info.get("excel_cell") == cell:
                                is_duplicate = True
                            break

                    if not is_duplicate:
                        idx_to_remove = []
                        last_x, last_y = 0, 0
                        has_nav_key = False
                        for i in range(len(cleaned_workflow_info) - 1, -1, -1):
                            prev_info = cleaned_workflow_info[i]
                            if prev_info.get("window_name", "") != current_window:
                                break
                            if prev_info.get("excel_dest_cell") or prev_info.get("excel_cell"):
                                break
                            if prev_info.get("raw_action") in ["click", "move"]:
                                idx_to_remove.append(i)
                                if prev_info.get("raw_action") == "click" and last_x == 0:
                                    last_x = prev_info.get("cursor_x", prev_info.get("x", 0))
                                    last_y = prev_info.get("cursor_y", prev_info.get("y", 0))
                            elif prev_info.get("raw_action") in ["key_down", "type_text"]:
                                role = str(prev_info.get("semantic_role", "")).lower()
                                if role in ["enter", "tab", "esc", "up", "down", "left", "right"] or role.startswith("key.") or "+" in role:
                                    has_nav_key = True
                                    break
                                else:
                                    break
                            else:
                                break

                        if has_nav_key:
                            for i in sorted(idx_to_remove, reverse=True):
                                cleaned_workflow_info.pop(i)
                            for i in range(len(cleaned_workflow_info) - 1, -1, -1):
                                if cleaned_workflow_info[i].get("raw_action") in ["key_down", "type_text"]:
                                    cleaned_workflow_info[i]["excel_dest_cell"] = cell
                                    break
                        else:
                            for i in sorted(idx_to_remove, reverse=True):
                                cleaned_workflow_info.pop(i)

                            if last_x != 0 or last_y != 0:
                                info["raw_action"] = "click"
                                info["excel_dest_cell"] = cell
                                info["button"] = "left"
                                info["cursor_x"] = last_x
                                info["cursor_y"] = last_y
                                cleaned_workflow_info.append(info)

            continue

        cleaned_workflow_info.append(info)

    return cleaned_workflow_info


def propagate_excel_cells(temp_workflow_info: List[Dict[str, Any]]) -> List[Dict[str, Any]]:
    """セル移動や書き込み先セル情報を後続のtype_textへ安全に伝播する"""
    last_excel_dest_cell = None
    for info in temp_workflow_info:
        if info.get("excel_dest_cell"):
            last_excel_dest_cell = info["excel_dest_cell"]
        elif info.get("excel_cell"):
            last_excel_dest_cell = info["excel_cell"]
        elif info.get("raw_action") == "type_text" and last_excel_dest_cell:
            if "excel_cell" not in info and not info.get("dynamic_excel_cell"):
                info["excel_cell"] = last_excel_dest_cell
    return temp_workflow_info


def filter_web_excel_conflicts(temp_workflow_info: List[Dict[str, Any]]) -> List[Dict[str, Any]]:
    """Why: Web自動化マクロにおいて記録時に混入したExcelセル書き込み(write_cell)を完全排除"""
    has_browser_ops = any(
        info.get("raw_action") == "browser_action" or
        any(b in str(info.get("window_name", "")).lower() for b in ["firefox", "chrome", "edge", "brave", "opera"])
        for info in temp_workflow_info
    )
    if has_browser_ops:
        return [
            info for info in temp_workflow_info
            if not (info.get("raw_action") == "excel_action" and info.get("action") == "write_cell")
        ]
    return temp_workflow_info
