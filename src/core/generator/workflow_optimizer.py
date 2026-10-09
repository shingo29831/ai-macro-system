# src/core/generator/workflow_optimizer.py
# Role: 統合されたイベントリストに対し、Officeイベントのクリーンアップ、OSシェル操作のカット、ループ解析、変数化などの最適化を行うモジュール

import logging
import re
import difflib
import time
from typing import List, Dict, Any, Callable, Optional

from core.generator.navigation_optimizer import (
    is_residual_hover as _is_residual_hover,
    promote_navigation_hover_to_click as _promote_navigation_hover_to_click,
    reorder_displaced_clicks_before_scroll as _reorder_displaced_clicks_before_scroll,
    optimize_typing_and_search_flow as _optimize_typing_and_search_flow,
    cleanup_redundant_moves_and_scrolls as _cleanup_redundant_moves_and_scrolls,
)
from core.generator.form_optimizer import (
    is_system_ui_event as _is_system_ui_event,
    consolidate_web_form_interactions as _consolidate_web_form_interactions,
)

logger = logging.getLogger(__name__)

def optimize_workflow_events(
    temp_workflow_info: List[Dict[str, Any]], 
    workflow_id: str, 
    check_cancel_callback: Optional[Callable[[], bool]] = None,
    config: Optional[Any] = None,
    strategy: str = "hybrid"
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

    def _is_business_action(evt: Dict[str, Any]) -> bool:
        act = evt.get("raw_action", "")
        role = str(evt.get("semantic_role", "")).lower()
        if act in ["click", "type_text", "excel_action", "browser_action"]:
            return True
        if "key" in act and role not in ["win", "cmd", "windows"]:
            return True
        return False

    shell_cut_info = []
    skip_until_new_window = False
    win_key_window_name = ""
    
    for info in temp_workflow_info:
        if should_cancel():
            raise InterruptedError("Generation cancelled by user")
            
        if _is_system_ui_event(info):
            continue

        win_name = info.get("window_name", "")
        
        if skip_until_new_window:
            # Why: Google検索などのWebタイトルを誤判定せずOSシェルウィンドウのみスキップ
            wn_lower = win_name.lower().strip()
            is_browser_w = any(b in wn_lower for b in ["firefox", "chrome", "edge", "brave", "opera"])
            sys_exact = ["検索", "スタート", "start", "search", "taskbar", "タスクバー", "cortana", "ジャンプ リスト"]
            sys_contains = ["python", "unknown window", "マクロ生成中", "aiマクロ生成中", "ai macro system", "記録中", "停止中", "実行中", "設定", "ウィンドウの紐付け"]
            is_system = not wn_lower or (not is_browser_w and (wn_lower in sys_exact or any(sw in wn_lower for sw in sys_contains)))
            
            # Why: 業務アクションが開始された場合、または新ウィンドウが出現した場合はシェルスキップを即時解除
            if _is_business_action(info) or (not is_system and win_name != win_key_window_name):
                skip_until_new_window = False
            else:
                continue
        
        # Why: アプリ起動用のWinキー検知時、起動前ウィンドウの有為でない残骸を完全除去
        if info.get("raw_action") in ["key_down", "key_press", "press_key"] and str(info.get("semantic_role", "")).lower() in ["win", "cmd", "windows"]:
            skip_until_new_window = True
            win_key_window_name = win_name
            target_win_ops = [e for e in shell_cut_info if e.get("window_name") == win_name]
            if target_win_ops and not any(_is_business_action(e) for e in target_win_ops):
                shell_cut_info = [e for e in shell_cut_info if e.get("window_name") != win_name]
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
        orig_wx = info.get("win_x", 0)
        orig_wy = info.get("win_y", 0)
        raw_type = str(info.get("raw_type", "")).lower()
        raw_act = str(info.get("raw_action", "")).lower()
        cy = info.get("cursor_y", info.get("y", 0))
        cx = info.get("cursor_x", info.get("x", 0))

        # Why: ウィンドウ移動・リサイズ・タイトルバー操作は全期間で検知しマクロ操作から完全除外
        if "drag" in raw_type or raw_act == "drag":
            logger.info(f"[{workflow_id}] Omitted layout adjustment drag (Event: {info.get('event_id')})")
            continue

        # Why: マウス移動・クリックのみタイトルバー誤爆を判定し、キーボード入力やテキスト入力の脱落を完全防止
        if raw_act in ["click", "move"] and ("drag" in raw_type or raw_act == "drag" or (cy - orig_wy <= 45) or cy < -50):
            ctx = info.get("app_context") or {}
            has_valid_web_target = bool(ctx.get("url") and str(ctx.get("url")).startswith("http"))
            if not has_valid_web_target:
                logger.info(f"[{workflow_id}] Omitted title bar/off-screen layout action (Event: {info.get('event_id')}) at ({cx}, {cy})")
                continue

        if app in app_final_rect:
            final_x, final_y, final_w, final_h = app_final_rect[app]
            # Why: 移動前モニタ(負の座標等)の操作座標を最終ウィンドウの座標系へ安全に投影変換
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

    # Why: 推測によるスクロール捏造を撤廃しユーザーの実スクロール記録のみを厳密採用
    temp_workflow_info = layout_cleaned
    temp_workflow_info = _optimize_typing_and_search_flow(temp_workflow_info)
    temp_workflow_info = _promote_navigation_hover_to_click(temp_workflow_info)
    temp_workflow_info = _reorder_displaced_clicks_before_scroll(temp_workflow_info)
    temp_workflow_info = _cleanup_redundant_moves_and_scrolls(temp_workflow_info)

    # Why: 入力コンテキストは真のクリック操作のみから継承しホバーによるすり替わりを完全防止
    last_clk_ctx = {}
    last_clk_win = ""
    for info in temp_workflow_info:
        act = info.get("raw_action", "")
        w_name = info.get("window_name", "")
        c = info.get("app_context") or {}
        elem = c.get("element_name") or ""
        sel = c.get("css_selector") or ""

        if act == "click":
            if sel or elem or c.get("xpath"):
                last_clk_ctx = c.copy()
                last_clk_win = w_name
        elif act in ["type_text", "key_combo", "key_down", "key_press"] and last_clk_ctx:
            # Why: ブラウザ遷移直後の空ウィンドウ名に対しても安全にコンテキストを伝播
            is_win_match = (w_name == last_clk_win) or not last_clk_win or not w_name or any(b in (w_name + last_clk_win).lower() for b in ["firefox", "chrome", "edge"])
            if is_win_match:
                i_ctx = info.setdefault("app_context", {})
                # Why: 自身がセレクタを持たない場合のみ直前クリックの全コンテキストを安全に継承
                if not i_ctx.get("css_selector") and not i_ctx.get("xpath"):
                    for k in ["css_selector", "xpath", "element_name", "control_type"]:
                        if last_clk_ctx.get(k):
                            i_ctx[k] = last_clk_ctx[k]
                elif i_ctx.get("css_selector") == last_clk_ctx.get("css_selector"):
                    if not i_ctx.get("element_name") and last_clk_ctx.get("element_name"):
                        i_ctx["element_name"] = last_clk_ctx["element_name"]
                    if not i_ctx.get("control_type") and last_clk_ctx.get("control_type"):
                        i_ctx["control_type"] = last_clk_ctx["control_type"]

    # Why: ビジュアル優先戦略時は過剰なWebフォーム集約を抑止し物理入力構造を保護
    if strategy != "visual":
        temp_workflow_info = _consolidate_web_form_interactions(temp_workflow_info, config=config)

    # Why: 最初の有為操作より前、および最後の有為操作より後の停止ボタン関連ノイズを除去
    while temp_workflow_info:
        first_evt = temp_workflow_info[0]
        if first_evt.get("raw_action") == "move":
            diff = first_evt.get("diff_val", 0.0)
            elem = first_evt.get("app_context", {}).get("element_name")
            if diff < 0.005 and not elem:
                temp_workflow_info.pop(0)
            else:
                break
        else:
            break

    while temp_workflow_info:
        last_evt = temp_workflow_info[-1]
        # Why: 意図的なスクロールや有為なホバーを保護し停止操作由来の移動・システムUIのみ末尾除去
        if _is_system_ui_event(last_evt) or last_evt.get("raw_action") in ["unknown", "uia_scan"]:
            temp_workflow_info.pop()
        elif last_evt.get("raw_action") == "move":
            diff = last_evt.get("diff_val", 0.0)
            cy = last_evt.get("cursor_y", last_evt.get("y", 0))
            wy = last_evt.get("win_y", 0)
            # Why: 画面遷移後の操作や有意差分を持つ操作は末尾にあっても保護
            prev_events_win = [e.get("window_name", "") for e in temp_workflow_info[:-1] if e.get("window_name")]
            is_new_win = bool(prev_events_win and last_evt.get("window_name") and last_evt.get("window_name") != prev_events_win[0])
            if (diff < 0.005 or (cy - wy) <= 45) and not is_new_win:
                temp_workflow_info.pop()
            else:
                break
        else:
            break

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

    from core.executor.os_env_controller import is_link_or_url, normalize_text_width

    # Why: Web自動化マクロにおいて記録時に混入したExcelセル書き込み(write_cell)を完全排除
    has_browser_ops = any(
        info.get("raw_action") == "browser_action" or 
        any(b in str(info.get("window_name", "")).lower() for b in ["firefox", "chrome", "edge", "brave", "opera"])
        for info in temp_workflow_info
    )
    if has_browser_ops:
        temp_workflow_info = [
            info for info in temp_workflow_info 
            if not (info.get("raw_action") == "excel_action" and info.get("action") == "write_cell")
        ]

    # Why: 直前クリック先要素のセレクタ・コンテキストを入力イベント(type_text)へ安全にバインド
    last_browser_click_ctx = {}
    last_browser_win = ""
    for info in temp_workflow_info:
        act = info.get("raw_action", "")
        w_name = info.get("window_name", "")
        if act == "click":
            ctx = info.get("app_context") or {}
            # Why: ドロップダウンや別コンテナのクリックをテキスト欄へ誤付与しない
            c_type = str(ctx.get("control_type", "")).lower()
            if "combobox" not in c_type and (ctx.get("css_selector") or ctx.get("xpath") or ctx.get("element_name")):
                last_browser_click_ctx = ctx.copy()
                last_browser_win = w_name
        elif act in ["type_text", "key_combo"] and last_browser_click_ctx:
            if w_name == last_browser_win:
                if not info.get("app_context"):
                    info["app_context"] = last_browser_click_ctx.copy()
                else:
                    info_ctx = info["app_context"]
                    if not info_ctx.get("css_selector") and not info_ctx.get("xpath"):
                        for k, v in last_browser_click_ctx.items():
                            if not info_ctx.get(k):
                                info_ctx[k] = v

    # Why: ブラウザ内のリンククリック、要素操作、URL入力を専用ブラウザアクション(browser_action)へ網羅昇格
    promoted_browser_info = []
    for info in temp_workflow_info:
        act = info.get("raw_action", "")
        app_ctx = info.get("app_context") or {}
        win_name = info.get("window_name", "").lower()
        is_browser = app_ctx.get("app") == "Browser" or any(b in win_name for b in ["firefox", "chrome", "edge", "brave", "opera"])

        # Why: ビジュアル戦略時はブラウザアクションへの過剰昇格を抑え高精度な画面照合を保持
        if is_browser and strategy != "visual":
            if act == "type_text":
                role_text = str(info.get("semantic_role", "")).strip()
                is_addr = app_ctx.get("is_address_bar") or "検索" in str(app_ctx.get("element_name", "")) or "url" in str(app_ctx.get("element_name", "")).lower()
                if is_link_or_url(role_text) or (is_addr and any(ext in role_text.lower() for ext in [".jp", ".com", ".net", ".org", "http"])):
                    norm_url = normalize_text_width(role_text)
                    if not norm_url.startswith(("http://", "https://")):
                        norm_url = f"https://{norm_url}"
                    info["raw_action"] = "browser_action"
                    info["raw_type"] = "browser_action"
                    info["action"] = "open_url"
                    info["url"] = norm_url
                    info["semantic_role"] = norm_url
                elif app_ctx.get("css_selector") or app_ctx.get("xpath"):
                    info["raw_action"] = "browser_action"
                    info["raw_type"] = "browser_action"
                    info["action"] = "type_text"
                    info["selector"] = app_ctx.get("css_selector") or app_ctx.get("xpath")
                    info["text"] = role_text
            elif act in ["click", "move"]:
                # Why: URL(text/value/url)を持つリンククリックやページ遷移移動を専用サイト訪問アクションへ昇格
                target_url = app_ctx.get("url") or app_ctx.get("value") or app_ctx.get("text")
                if target_url and isinstance(target_url, str):
                    target_url = target_url.strip()
                else:
                    target_url = ""

                has_http = target_url.startswith(("http://", "https://"))
                cur_sel = info.get("selector") or app_ctx.get("css_selector") or app_ctx.get("xpath")
                has_selector = bool(cur_sel)
                elem_name = info.get("element_name") or app_ctx.get("element_name") or info.get("semantic_role") or ""

                if has_http or (has_selector and act == "click"):
                    info["raw_action"] = "browser_action"
                    info["raw_type"] = "browser_action"
                    if has_selector and act == "click":
                        info["action"] = "click_element"
                        info["selector"] = cur_sel
                        info["selector_type"] = info.get("selector_type") or "css"
                    else:
                        info["action"] = "open_url"
                    if has_http:
                        info["url"] = target_url
                    if elem_name and elem_name != "left_click":
                        info["element_name"] = elem_name
                        info["semantic_role"] = elem_name

        promoted_browser_info.append(info)
    # Why: 入力補完Tabを除去しつつフォーム送信や検索実行のEnterキーを確実に保護
    cleaned_after_promoted = []
    m_len = len(promoted_browser_info)
    for p_idx, p_info in enumerate(promoted_browser_info):
        p_act = p_info.get("raw_action", "")
        p_role = str(p_info.get("semantic_role", "")).lower()
        if p_act in ["key_down", "key_press", "press_key"] and p_role in ["enter", "tab"]:
            if p_idx > 0:
                prev = promoted_browser_info[p_idx - 1]
                prev_act = prev.get("raw_action")
                prev_role = str(prev.get("semantic_role", "")).lower()
                # Why: IME確定Enterと送信Enterが連続する場合は送信用の1回へ集約
                if p_role in ["enter", "return"] and prev_role in ["enter", "return"] and prev_act in ["key_down", "key_press", "press_key"]:
                    continue
                if prev_act in ["browser_action", "type_text"]:
                    # Why: 末尾のEnterや非IME確定Enterは送信キーのため除去せず保持
                    if p_role == "tab" or (p_info.get("ime_active") and p_idx < m_len - 1 and promoted_browser_info[p_idx + 1].get("raw_action") in ["type_text", "key_press"]):
                        continue
        cleaned_after_promoted.append(p_info)
    promoted_browser_info = cleaned_after_promoted
    temp_workflow_info = promoted_browser_info

    # Why: 意図しない自動変数置換を完全廃止しユーザー入力の確定値をそのまま出力
    variables = {}
    return temp_workflow_info, variables
