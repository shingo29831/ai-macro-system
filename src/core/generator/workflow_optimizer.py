# src/core/generator/workflow_optimizer.py
# Role: 統合されたイベントリストに対し、Officeイベントのクリーンアップ、OSシェル操作のカット、ループ解析、変数化などの最適化を行うモジュール

import logging
import re
import difflib
import time
from typing import List, Dict, Any, Callable, Optional

logger = logging.getLogger(__name__)

def optimize_workflow_events(
    temp_workflow_info: List[Dict[str, Any]], 
    workflow_id: str, 
    check_cancel_callback: Optional[Callable[[], bool]] = None
) -> tuple[List[Dict[str, Any]], Dict[str, str]]:
    last_cancel_check_time = time.time()
    def should_cancel():
        nonlocal last_cancel_check_time
        current_time = time.time()
        if current_time - last_cancel_check_time > 0.1:
            last_cancel_check_time = current_time
            if check_cancel_callback and check_cancel_callback():
                return True
        return False

    cleaned_workflow_info = []
    for info in temp_workflow_info:
        if should_cancel():
            raise InterruptedError("Generation cancelled by user")
            
        if info["raw_action"] == "office_event":
            msg = info.get("semantic_role", "")
            if "入力確定" in msg:
                match = re.search(r"セル:\s*([^\s|]+)\s*\|\s*値:\s*(.+)", msg)
                if match:
                    cell = match.group(1).replace("$", "")
                    val = match.group(2).strip()
                    if val.endswith(".0"):
                        val = val[:-2]
                    
                    is_duplicate = False
                    for i in range(len(cleaned_workflow_info) - 1, -1, -1):
                        prev_info = cleaned_workflow_info[i]
                        if prev_info["raw_action"] == "type_text":
                            if prev_info.get("excel_cell") == cell:
                                is_duplicate = True
                            break
                        elif prev_info.get("excel_dest_cell"):
                            break
                            
                    if not is_duplicate:
                        idx_to_remove = []
                        insert_idx = len(cleaned_workflow_info)
                        for i in range(len(cleaned_workflow_info) - 1, -1, -1):
                            prev_info = cleaned_workflow_info[i]
                            if prev_info.get("excel_dest_cell") or prev_info.get("excel_cell"):
                                break
                            if prev_info["raw_action"] in ["key_down", "type_text", "click", "move"]:
                                role = str(prev_info.get("semantic_role", "")).lower()
                                if role in ["enter", "tab", "esc", "up", "down", "left", "right"] or role.startswith("key.") or "+" in role or prev_info["raw_action"] == "key_down":
                                    insert_idx = i
                                else:
                                    idx_to_remove.append(i)
                            else:
                                break
                                
                        info["raw_action"] = "type_text"
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
                    for i in range(len(cleaned_workflow_info) - 1, -1, -1):
                        prev_info = cleaned_workflow_info[i]
                        if prev_info["raw_action"] == "click" and prev_info.get("excel_dest_cell"):
                            if prev_info.get("excel_dest_cell") == cell:
                                is_duplicate = True
                            break
                        elif prev_info["raw_action"] == "type_text" and prev_info.get("excel_cell"):
                            if prev_info.get("excel_cell") == cell:
                                is_duplicate = True
                            break
                            
                    if not is_duplicate:
                        idx_to_remove = []
                        last_x, last_y = 0, 0
                        has_nav_key = False
                        for i in range(len(cleaned_workflow_info) - 1, -1, -1):
                            prev_info = cleaned_workflow_info[i]
                            if prev_info.get("excel_dest_cell") or prev_info.get("excel_cell"):
                                break
                            if prev_info["raw_action"] in ["click", "move"]:
                                idx_to_remove.append(i)
                                if prev_info["raw_action"] == "click" and last_x == 0:
                                    last_x = prev_info.get("cursor_x", prev_info.get("x", 0))
                                    last_y = prev_info.get("cursor_y", prev_info.get("y", 0))
                            elif prev_info["raw_action"] in ["key_down", "type_text"]:
                                role = str(prev_info.get("semantic_role", "")).lower()
                                if role in ["enter", "tab", "esc", "up", "down", "left", "right"] or role.startswith("key.") or "+" in role or prev_info["raw_action"] == "key_down":
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
                                if cleaned_workflow_info[i]["raw_action"] in ["key_down", "type_text"]:
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
        
    temp_workflow_info = cleaned_workflow_info

    shell_cut_info = []
    skip_until_new_window = False
    win_key_window_name = ""
    
    for info in temp_workflow_info:
        if should_cancel():
            raise InterruptedError("Generation cancelled by user")
            
        win_name = info.get("window_name", "")
        
        if skip_until_new_window:
            system_windows = ["python", "unknown window", "検索", "スタート", "start", "search", "taskbar", "タスクバー", "cortana", "ジャンプ リスト"]
            is_system = not win_name.strip() or any(sw in win_name.lower() for sw in system_windows)
            
            if not is_system and win_name != win_key_window_name:
                skip_until_new_window = False
            else:
                continue
        
        if info.get("raw_action") == "key_down" and info.get("semantic_role", "").lower() in ["win", "cmd", "windows"]:
            skip_until_new_window = True
            win_key_window_name = win_name
            continue
            
        shell_cut_info.append(info)
        
    temp_workflow_info = shell_cut_info

    optimized_workflow_info = []
    idx = 0
    while idx < len(temp_workflow_info):
        if should_cancel():
            raise InterruptedError("Generation cancelled by user")
            
        info = temp_workflow_info[idx]
        if info.get("raw_type", "").lower() == "meta_loop_start":
            loop_start_info = info
            
            loop_events = []
            j = idx + 1
            while j < len(temp_workflow_info) and temp_workflow_info[j].get("raw_type", "").lower() != "meta_loop_end":
                loop_events.append(temp_workflow_info[j])
                j += 1
            
            actions = [e["raw_action"] for e in loop_events]
            n = len(actions)
            best_period = 0
            best_score = 0.0
            best_start_idx = 0
            
            max_start_idx = min(4, max(1, n - 1))
            for start_idx in range(max_start_idx):
                for p in range(1, (n - start_idx) // 2 + 1):
                    if should_cancel():
                        raise InterruptedError("Generation cancelled by user")
                        
                    template = actions[start_idx : start_idx + p]
                    target = actions[start_idx + p : start_idx + p * 2]
                    sm = difflib.SequenceMatcher(None, template, target)
                    score = sm.ratio()
                    
                    if score > best_score and score >= 0.6:
                        best_score = score
                        best_period = p
                        best_start_idx = start_idx

            y_offset = 0
            x_offset = 0
            
            if best_period > 0 and (best_start_idx + best_period * 2) <= n:
                pre_loop_events = loop_events[:best_start_idx]
                optimized_workflow_info.extend(pre_loop_events)
                
                num_iterations = (n - best_start_idx) // best_period
                iterations = []
                for it in range(num_iterations):
                    it_start = best_start_idx + it * best_period
                    iterations.append(loop_events[it_start : it_start + best_period])
                
                first_iter = iterations[0]
                second_iter = iterations[1]
                
                for k in range(len(first_iter)):
                    if first_iter[k].get("raw_action") in ["click", "move"] and second_iter[k].get("raw_action") == first_iter[k].get("raw_action"):
                        diff_y = second_iter[k].get("cursor_y", second_iter[k].get("y", 0)) - first_iter[k].get("cursor_y", first_iter[k].get("y", 0))
                        diff_x = second_iter[k].get("cursor_x", second_iter[k].get("x", 0)) - first_iter[k].get("cursor_x", first_iter[k].get("x", 0))
                        if 10 < abs(diff_y) < 200:
                            y_offset = diff_y
                        if 10 < abs(diff_x) < 200:
                            x_offset = diff_x
                            
                    if first_iter[k].get("raw_action") in ["type_text", "key_down"]:
                        vals = []
                        is_valid_num_seq = True
                        for iter_idx in range(len(iterations)):
                            if k < len(iterations[iter_idx]):
                                v = iterations[iter_idx][k].get("semantic_role")
                                try:
                                    vals.append(int(v))
                                except (ValueError, TypeError):
                                    is_valid_num_seq = False
                                    break
                            else:
                                is_valid_num_seq = False
                                break
                                
                        if is_valid_num_seq and len(vals) >= 2:
                            step = vals[1] - vals[0]
                            is_uniform_step = all(vals[i] - vals[i - 1] == step for i in range(1, len(vals)))
                            if is_uniform_step and step != 0:
                                first_iter[k]["sequence_value"] = {"start": vals[0], "step": step}
                                first_iter[k]["is_sequence"] = True
                                first_iter[k]["raw_action"] = "type_text"
                                first_iter[k]["semantic_role"] = str(vals[0])
                                
                    for cell_key in ["excel_cell", "excel_dest_cell"]:
                        if cell_key in first_iter[k] and cell_key in second_iter[k]:
                            if first_iter[k][cell_key] != second_iter[k][cell_key]:
                                del first_iter[k][cell_key]
                                first_iter[k]["dynamic_excel_cell"] = True
                            
                loop_start_info["loop_variables"] = {"y_offset": y_offset, "x_offset": x_offset}
                optimized_workflow_info.append(loop_start_info)
                optimized_workflow_info.extend(first_iter)
                logger.info(f"[{workflow_id}] Loop pattern detected (score={best_score:.2f}, period={best_period}, count={num_iterations}). Offsets: y={y_offset}, x={x_offset}")
            else:
                loop_start_info["loop_variables"] = {"y_offset": 0, "x_offset": 0}
                optimized_workflow_info.append(loop_start_info)
                optimized_workflow_info.extend(loop_events)
                logger.info(f"[{workflow_id}] Could not determine loop period. Keeping all events.")
            
            if j < len(temp_workflow_info):
                optimized_workflow_info.append(temp_workflow_info[j])
            else:
                optimized_workflow_info.append({
                    "raw_type": "meta_loop_end",
                    "raw_action": "meta_loop_end",
                    "event_id": "auto_loop_end",
                    "semantic_role": "",
                    "window_name": info.get("window_name", "")
                })
            idx = j + 1
        else:
            optimized_workflow_info.append(info)
            idx += 1
            
    temp_workflow_info = optimized_workflow_info
    
    last_excel_dest_cell = None
    for info in temp_workflow_info:
        if info.get("excel_dest_cell"):
            last_excel_dest_cell = info["excel_dest_cell"]
        elif info.get("excel_cell"):
            last_excel_dest_cell = info["excel_cell"]
        elif info["raw_action"] == "type_text" and last_excel_dest_cell:
            if "excel_cell" not in info and not info.get("dynamic_excel_cell"):
                info["excel_cell"] = last_excel_dest_cell

    variables = {}
    for info in temp_workflow_info:
        if should_cancel():
            raise InterruptedError("Generation cancelled by user")
            
        if info.get("raw_action") == "type_text" and info.get("semantic_role"):
            if info.get("is_sequence"):
                continue
                
            role_str = str(info["semantic_role"])
            role_lower = role_str.lower()
            
            if role_lower not in ["enter", "tab", "esc", "backspace", "delete"] and not role_lower.startswith("key."):
                var_name = f"search_query_{len(variables) + 1}"
                variables[var_name] = role_str
                info["semantic_role"] = f"{{{{{var_name}}}}}"

    return temp_workflow_info, variables