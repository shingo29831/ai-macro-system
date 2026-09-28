# src/core/generator/workflow_optimizer.py
# Role: 統合されたイベントリストに対し、Officeイベントのクリーンアップ、OSシェル操作のカット、ループ解析、変数化などの最適化を行うモジュール

import logging
import re
import difflib
import time
from typing import List, Dict, Any, Callable, Optional

logger = logging.getLogger(__name__)

def _promote_navigation_hover_to_click(temp_workflow_info: List[Dict[str, Any]]) -> List[Dict[str, Any]]:
    # Why: ドロップダウン等のホバー操作を保持しつつ遷移の契機となった最後の要素のみクリックへ昇格
    n = len(temp_workflow_info)
    if n == 0:
        return temp_workflow_info

    for i in range(1, n):
        curr_info = temp_workflow_info[i]
        prev_info = temp_workflow_info[i - 1]

        curr_win = curr_info.get("window_name", "")
        prev_win = prev_info.get("window_name", "")

        curr_ctx = curr_info.get("app_context") or {}
        curr_url = str(curr_ctx.get("url") or curr_ctx.get("text") or curr_ctx.get("value") or "").strip()

        transition_detected = False
        target_url = curr_url
        target_title = curr_win

        if prev_win and curr_win and prev_win != curr_win:
            w1_parts = [p.strip().lower() for p in re.split(r"[\-—–―]", prev_win)]
            w2_parts = [p.strip().lower() for p in re.split(r"[\-—–―]", curr_win)]
            if len(w1_parts) > 0 and len(w2_parts) > 0 and w1_parts[-1] == w2_parts[-1]:
                transition_detected = True

        if not transition_detected and curr_url.startswith("http"):
            prev_ctx = prev_info.get("app_context") or {}
            prev_url = str(prev_ctx.get("url") or prev_ctx.get("text") or prev_ctx.get("value") or "").strip()
            if prev_url and prev_url != curr_url:
                base_prev = prev_url.split("#")[0].rstrip("/")
                base_curr = curr_url.split("#")[0].rstrip("/")
                if base_prev != base_curr:
                    transition_detected = True

        if not transition_detected:
            continue

        candidate_idx = None
        best_match_score = -1

        for k in range(i - 1, max(-1, i - 6), -1):
            cand = temp_workflow_info[k]
            cand_act = cand.get("raw_action", "")

            if cand_act == "click":
                candidate_idx = None
                break

            if cand_act != "move":
                continue

            cand_ctx = cand.get("app_context") or {}
            c_type = str(cand_ctx.get("control_type", "")).lower()
            c_url = str(cand_ctx.get("url") or cand_ctx.get("text") or cand_ctx.get("value") or "").strip()
            c_name = str(cand_ctx.get("element_name", "")).strip()

            is_interactive = (
                "hyperlink" in c_type or
                "button" in c_type or
                "menuitem" in c_type or
                c_url.startswith("http") or
                bool(cand_ctx.get("css_selector")) or
                bool(cand_ctx.get("xpath"))
            )

            if not is_interactive:
                continue

            score = 1
            if target_url and c_url:
                b_target = target_url.split("#")[0].rstrip("/").lower()
                b_curl = c_url.split("#")[0].rstrip("/").lower()
                if b_target == b_curl:
                    score = 10
                elif b_curl in b_target or b_target in b_curl:
                    score = 8

            if target_title and (c_name or c_url):
                clean_title = re.split(r"[\-—–―]", target_title)[0].strip().lower()
                if clean_title and (clean_title in c_name.lower() or clean_title in c_url.lower()):
                    score = max(score, 9)

            if score > best_match_score:
                best_match_score = score
                candidate_idx = k

        if candidate_idx is not None:
            target_cand = temp_workflow_info[candidate_idx]
            logger.info(
                f"Navigation transition detected (Step {candidate_idx} -> {i}). "
                f"Promoting move -> click on Event: {target_cand.get('event_id')} (score: {best_match_score})."
            )
            target_cand["raw_action"] = "click"
            target_cand["raw_type"] = "mouse_click"
            target_cand["button"] = "left"

    return temp_workflow_info

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
                    current_window = info.get("window_name", "")
                    for i in range(len(cleaned_workflow_info) - 1, -1, -1):
                        prev_info = cleaned_workflow_info[i]
                        if prev_info.get("window_name", "") != current_window:
                            break
                        if prev_info["raw_action"] == "type_text":
                            if prev_info.get("excel_cell") == cell and prev_info.get("semantic_role") == val:
                                is_duplicate = True
                            break
                        elif prev_info.get("excel_dest_cell"):
                            break
                            
                    if not is_duplicate:
                        idx_to_remove = []
                        insert_idx = len(cleaned_workflow_info)
                        for i in range(len(cleaned_workflow_info) - 1, -1, -1):
                            prev_info = cleaned_workflow_info[i]
                            if prev_info.get("window_name", "") != current_window:
                                break
                            if prev_info.get("excel_dest_cell") or prev_info.get("excel_cell"):
                                break
                            if prev_info["raw_action"] in ["key_down", "type_text", "click", "move"]:
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
                    current_window = info.get("window_name", "")
                    for i in range(len(cleaned_workflow_info) - 1, -1, -1):
                        prev_info = cleaned_workflow_info[i]
                        if prev_info.get("window_name", "") != current_window:
                            break
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
                            if prev_info.get("window_name", "") != current_window:
                                break
                            if prev_info.get("excel_dest_cell") or prev_info.get("excel_cell"):
                                break
                            if prev_info["raw_action"] in ["click", "move"]:
                                idx_to_remove.append(i)
                                if prev_info["raw_action"] == "click" and last_x == 0:
                                    last_x = prev_info.get("cursor_x", prev_info.get("x", 0))
                                    last_y = prev_info.get("cursor_y", prev_info.get("y", 0))
                            elif prev_info["raw_action"] in ["key_down", "type_text"]:
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
            # Why: Google検索などのWebタイトルを誤判定せずOSシェルウィンドウのみスキップ
            wn_lower = win_name.lower().strip()
            is_browser_w = any(b in wn_lower for b in ["firefox", "chrome", "edge", "brave", "opera"])
            sys_exact = ["検索", "スタート", "start", "search", "taskbar", "タスクバー", "cortana", "ジャンプ リスト"]
            sys_contains = ["python", "unknown window", "マクロ生成中", "aiマクロ生成中", "ai macro system", "記録中", "停止中", "実行中", "設定", "ウィンドウの紐付け"]
            is_system = not wn_lower or (not is_browser_w and (wn_lower in sys_exact or any(sw in wn_lower for sw in sys_contains)))
            
            if not is_system and win_name != win_key_window_name:
                skip_until_new_window = False
            else:
                continue
        
        # Why: アプリ起動用のWinキー操作（key_down/key_press問わず）を確実に検知してシェル操作を完全カット
        if info.get("raw_action") in ["key_down", "key_press", "press_key"] and str(info.get("semantic_role", "")).lower() in ["win", "cmd", "windows"]:
            skip_until_new_window = True
            win_key_window_name = win_name
            continue
            
        shell_cut_info.append(info)
        
    temp_workflow_info = shell_cut_info

    # Why: 起動直後の過渡的ウィンドウ移動・リサイズ操作を除去し最終調整後座標へ昇格
    def _extract_app(title: str) -> str:
        if not title: return ""
        parts = re.split(r"[\-—–―]", title)
        return parts[-1].strip().lower()

    app_first_business_op = {}
    app_final_rect = {}

    for i, info in enumerate(temp_workflow_info):
        app = _extract_app(info.get("window_name", ""))
        if not app: continue
        raw_act = info.get("raw_action", "")
        raw_tp = str(info.get("raw_type", "")).lower()
        role = str(info.get("semantic_role", "")).lower()
        
        is_business = False
        if raw_act == "type_text" or "key" in raw_act:
            if role not in ["win", "cmd"]:
                is_business = True
        elif raw_act == "click" and "drag" not in raw_tp:
            cy = info.get("cursor_y", 0)
            wy = info.get("win_y", 0)
            if cy - wy > 45 or info.get("excel_cell") or info.get("excel_dest_cell"):
                is_business = True

        if is_business and app not in app_first_business_op:
            app_first_business_op[app] = i

        wx = info.get("win_x", 0)
        wy = info.get("win_y", 0)
        ww = info.get("win_w", 0)
        wh = info.get("win_h", 0)
        if ww > 0 and wh > 0:
            app_final_rect[app] = (wx, wy, ww, wh)

    layout_cleaned = []
    for i, info in enumerate(temp_workflow_info):
        app = _extract_app(info.get("window_name", ""))
        first_op_idx = app_first_business_op.get(app, len(temp_workflow_info))
        
        if app in app_final_rect and i <= first_op_idx:
            final_x, final_y, final_w, final_h = app_final_rect[app]
            info["win_x"] = final_x
            info["win_y"] = final_y
            info["win_w"] = final_w
            info["win_h"] = final_h

        if i < first_op_idx:
            raw_type = str(info.get("raw_type", "")).lower()
            raw_act = str(info.get("raw_action", "")).lower()
            cy = info.get("cursor_y", 0)
            wy = info.get("win_y", 0)
            if "drag" in raw_type or (raw_act == "click" and cy - wy <= 45):
                logger.info(f"[{workflow_id}] Omitted window layout adjustment action (Event: {info.get('event_id')})")
                continue

        layout_cleaned.append(info)

    temp_workflow_info = layout_cleaned
    temp_workflow_info = _promote_navigation_hover_to_click(temp_workflow_info)

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
                
                num_iterations = (n - best_start_idx) // best_period
                iterations = []
                for it in range(num_iterations):
                    it_start = best_start_idx + it * best_period
                    iterations.append(loop_events[it_start : it_start + best_period])
                
                first_iter = iterations[0]
                second_iter = iterations[1]
                
                for k in range(len(first_iter)):
                    evt = first_iter[k]
                    
                    if evt.get("raw_action") in ["click", "move"] and second_iter[k].get("raw_action") == evt.get("raw_action"):
                        diff_y = second_iter[k].get("cursor_y", second_iter[k].get("y", 0)) - evt.get("cursor_y", evt.get("y", 0))
                        diff_x = second_iter[k].get("cursor_x", second_iter[k].get("x", 0)) - evt.get("cursor_x", evt.get("x", 0))
                        if 10 < abs(diff_y) < 200:
                            y_offset = diff_y
                        if 10 < abs(diff_x) < 200:
                            x_offset = diff_x
                            
                    if evt.get("raw_action") in ["type_text", "key_down"]:
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
                                evt["sequence_value"] = {"start": vals[0], "step": step}
                                evt["is_sequence"] = True
                                evt["raw_action"] = "type_text"
                                evt["semantic_role"] = str(vals[0])
                                
                    for cell_key in ["excel_cell", "excel_dest_cell"]:
                        if cell_key in evt and cell_key in second_iter[k]:
                            if evt[cell_key] != second_iter[k][cell_key]:
                                del evt[cell_key]
                                evt["dynamic_excel_cell"] = True
                            
                loop_start_info["loop_variables"] = {"y_offset": y_offset, "x_offset": x_offset}
                optimized_workflow_info.extend(pre_loop_events)
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