"""Module: @role: Office/Excel操作イベント（入力確定、セル選択移動）のクリーンアップ、物理入力巻き戻し、およびセル情報伝播を担当する。"""

from typing import Any, Callable, Dict, List, Optional, Tuple
import logging
import re

from core.shared.app_log_profile import (
    AppProfile,
    detect_app_profile,
    get_app_log_policy,
    has_explicit_app_identity,
)

logger = logging.getLogger(__name__)


def _extract_office_event_payload(info: Dict[str, Any]) -> Tuple[str, str, Any, bool]:
    """構造化office_infoまたは従来のメッセージ文字列から(event_kind, cell, value, is_range)を抽出する。"""
    office_info = info.get("office_info") or (info.get("content") or {}).get("office_info") or {}
    if isinstance(office_info, dict) and office_info.get("event_type") in ("cell_change", "selection_change"):
        e_type = str(office_info["event_type"])
        cell = str(office_info.get("cell", "")).replace("$", "").strip()
        is_range = bool(office_info.get("is_range")) or (":" in cell)
        raw_val = office_info.get("value")
        if is_range:
            return e_type, cell, raw_val, True
        val = "" if raw_val is None else str(raw_val).strip()
        if val.endswith(".0") and val[:-2].lstrip("-").isdigit():
            val = val[:-2]
        return e_type, cell, val, False

    msg = str(info.get("semantic_role", ""))
    if "入力確定" in msg:
        match = re.search(r"セル:\s*([^\s|]+)\s*\|\s*値:\s*(.*)", msg)
        if match:
            cell = match.group(1).replace("$", "").strip()
            is_range = ":" in cell
            val = match.group(2).strip()
            if val.endswith(".0") and val[:-2].lstrip("-").isdigit():
                val = val[:-2]
            return "cell_change", cell, (val if val != "" else None) if is_range else val, is_range
    elif "選択移動" in msg:
        match = re.search(r"セル:\s*([^\s|]+)", msg)
        if match:
            cell = match.group(1).replace("$", "").strip()
            return "selection_change", cell, "", ":" in cell

    return "", "", "", False


def _is_empty_range_value(val: Any) -> bool:
    if val is None or val == "":
        return True
    if isinstance(val, (list, tuple)):
        for row in val:
            if isinstance(row, (list, tuple)):
                if any(c not in (None, "") for c in row):
                    return False
            elif row not in (None, ""):
                return False
        return True
    return False


def clean_office_events(
    temp_workflow_info: List[Dict[str, Any]],
    should_cancel: Optional[Callable[[], bool]] = None,
) -> List[Dict[str, Any]]:
    """Excelの入力確定・選択移動イベントを解析し、重複する物理操作を巻き戻してexcel_actionへ変換する"""
    cleaned_workflow_info: List[Dict[str, Any]] = []
    last_profile: AppProfile = AppProfile.DESKTOP

    for info in temp_workflow_info:
        if should_cancel and should_cancel():
            raise InterruptedError("Generation cancelled by user")

        has_id = has_explicit_app_identity(info)
        prof = detect_app_profile(info) if has_id else last_profile
        if has_id and prof != AppProfile.SYSTEM:
            last_profile = prof

        if info.get("raw_action") == "office_event":
            event_kind, cell, val, is_range = _extract_office_event_payload(info)
            # Why: 識別情報欠落時はselection_changeを遮断し、明示的非Office時はCOMイベントを遮断
            if has_id and not get_app_log_policy(prof, source=info).use_office_com:
                continue
            if not has_id and prof != AppProfile.OFFICE:
                if event_kind != "cell_change" or any(detect_app_profile(e) == AppProfile.BROWSER for e in cleaned_workflow_info):
                    continue

            if event_kind == "cell_change" and cell:
                is_duplicate = False
                current_window = info.get("window_name", "")
                for i in range(len(cleaned_workflow_info) - 1, -1, -1):
                    prev_info = cleaned_workflow_info[i]
                    if prev_info.get("window_name", "") != current_window:
                        # Why: フォーカス喪失契機の別ウィンドウクリック1件は飛び越えて直前のExcel入力を探索
                        if i == len(cleaned_workflow_info) - 1 and prev_info.get("raw_action") in ("click", "move"):
                            continue
                        break
                    if prev_info.get("raw_action") == "type_text":
                        if prev_info.get("excel_cell") == cell and prev_info.get("semantic_role") == str(val or ""):
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
                            if i == len(cleaned_workflow_info) - 1 and prev_info.get("raw_action") in ("click", "move"):
                                insert_idx = i
                                continue
                            break
                        if prev_info.get("excel_dest_cell") or prev_info.get("excel_cell"):
                            break
                        # Why: セル入力確定の巻き戻しは文字入力キーのみに限定し無関係なクリック/移動の消失を防止
                        if prev_info.get("raw_action") in ["key_down", "type_text"]:
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

                    if idx_to_remove and insert_idx >= min(idx_to_remove):
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

                    # Why: 単一セル書き込み(write_cell)と複数セル範囲操作(write_range/clear_range)を正確に区別
                    info["raw_action"] = "excel_action"
                    info["raw_type"] = "excel_action"
                    info["cell"] = cell
                    if is_range:
                        info["range_address"] = cell
                        if _is_empty_range_value(val):
                            info["action"] = "clear_range"
                            info["value"] = None
                            info["semantic_role"] = f"Clear {cell}"
                        else:
                            info["action"] = "write_range"
                            info["value"] = val
                            info["semantic_role"] = str(val)
                    else:
                        info["action"] = "write_cell"
                        info["value"] = val
                        info["semantic_role"] = str(val or "")
                        info["excel_cell"] = cell

                    cleaned_workflow_info.insert(insert_idx, info)

                    for i in sorted(idx_to_remove, reverse=True):
                        if i >= insert_idx:
                            cleaned_workflow_info.pop(i + 1)
                        else:
                            cleaned_workflow_info.pop(i)

            elif event_kind == "selection_change" and cell:
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
    """同一Officeブック内のセル移動や書き込み先セル情報を後続のtype_textへ安全に伝播する"""
    last_excel_dest_cell: Optional[str] = None
    last_office_win: Optional[str] = None
    last_profile: AppProfile = AppProfile.DESKTOP

    for info in temp_workflow_info:
        has_id = has_explicit_app_identity(info)
        if has_id:
            prof = detect_app_profile(info)
        elif info.get("excel_cell") or info.get("excel_dest_cell") or info.get("raw_action") == "excel_action":
            prof = AppProfile.OFFICE
        else:
            prof = last_profile

        win_name = str(info.get("window_name") or "").strip()

        # Why: 非Officeアプリ(Browser/Desktop/System)へ切り替わった場合はセル伝播をリセットし誤付与属性を除去
        if prof != AppProfile.OFFICE:
            last_excel_dest_cell = None
            last_office_win = None
            if prof != AppProfile.SYSTEM:
                last_profile = prof
            info.pop("excel_cell", None)
            info.pop("excel_dest_cell", None)
            continue

        last_profile = AppProfile.OFFICE
        # Why: 初回ウィンドウ名が空の場合も含め、異なるウィンドウ名へ変化した時点で前ブックのセル番地をリセット
        if last_office_win is not None and win_name != last_office_win:
            last_excel_dest_cell = None
            last_office_win = win_name

        if info.get("excel_dest_cell"):
            last_excel_dest_cell = info["excel_dest_cell"]
            last_office_win = win_name
        elif info.get("excel_cell"):
            last_excel_dest_cell = info["excel_cell"]
            last_office_win = win_name
        elif info.get("raw_action") == "type_text" and last_excel_dest_cell and ":" not in str(last_excel_dest_cell):
            if "excel_cell" not in info and not info.get("dynamic_excel_cell"):
                info["excel_cell"] = last_excel_dest_cell

    return temp_workflow_info


def filter_web_excel_conflicts(temp_workflow_info: List[Dict[str, Any]]) -> List[Dict[str, Any]]:
    """Why: 非OfficeウィンドウまたはWebマクロ中の識別情報欠落write_cellを排除し、正当なExcelウィンドウ操作は保護"""
    has_browser_ops = any(
        info.get("raw_action") == "browser_action" or detect_app_profile(info) == AppProfile.BROWSER
        for info in temp_workflow_info
    )
    filtered: List[Dict[str, Any]] = []
    for info in temp_workflow_info:
        if info.get("raw_action") == "excel_action" and info.get("action") in ("write_cell", "write_range", "clear_range"):
            if has_explicit_app_identity(info):
                if detect_app_profile(info) != AppProfile.OFFICE:
                    continue
            elif has_browser_ops:
                continue
        filtered.append(info)
    return filtered
