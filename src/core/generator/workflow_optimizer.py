# src/core/generator/workflow_optimizer.py
# Role: 統合されたイベントリストに対し、Officeイベントのクリーンアップ、OSシェル操作のカット、ループ解析、変数化などの最適化を行うモジュール

import logging
import re
import difflib
import time
from typing import List, Dict, Any, Callable, Optional

logger = logging.getLogger(__name__)

def _is_residual_hover(move_info: Dict[str, Any], temp_workflow_info: List[Dict[str, Any]], current_idx: int) -> bool:
    # Why: 直前クリックと同一座標(15px以内)の残留ホバーを判定
    if move_info.get("is_nav_hover"):
        return False
    mx, my = move_info.get("cursor_x", move_info.get("x", 0)), move_info.get("cursor_y", move_info.get("y", 0))
    for k in range(current_idx - 1, max(-1, current_idx - 4), -1):
        prev = temp_workflow_info[k]
        p_act = prev.get("raw_action", "")
        if p_act == "click":
            cx, cy = prev.get("cursor_x", prev.get("x", 0)), prev.get("cursor_y", prev.get("y", 0))
            if ((mx - cx) ** 2 + (my - cy) ** 2) ** 0.5 <= 15:
                return True
            break
        elif p_act != "move":
            break
    return False

def _promote_navigation_hover_to_click(temp_workflow_info: List[Dict[str, Any]]) -> List[Dict[str, Any]]:
    # Why: ドロップダウン等のホバー操作を保持しつつ遷移の契機となった要素のクリックを正しく生成
    if not temp_workflow_info:
        return temp_workflow_info

    i = 1
    while i < len(temp_workflow_info):
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
            i += 1
            continue

        # Why: 遷移先自身がクリック、または直近にEnter・テキスト入力があればキー操作遷移のためホバー昇格不要
        if curr_info.get("raw_action") == "click":
            i += 1
            continue

        # Why: 遷移元ウィンドウ内でのクリックやEnter等の操作遷移があった場合のみホバー昇格を抑制
        has_recent_nav_action = False
        for idx in range(i - 1, max(-1, i - 4), -1):
            act_info = temp_workflow_info[idx]
            if act_info.get("window_name") != prev_win:
                break
            act_role = str(act_info.get("semantic_role", "")).lower()
            act_act = str(act_info.get("raw_action", "")).lower()
            act_tp = str(act_info.get("raw_type", "")).lower()
            if "click" in act_act or "click" in act_tp:
                has_recent_nav_action = True
                break
            if act_act in ["type_text"] or (
                act_act in ["key_down", "key_press", "press_key"] and act_role in ["enter", "return"]
            ):
                has_recent_nav_action = True
                break

        if has_recent_nav_action:
            i += 1
            continue

        curr_act = curr_info.get("raw_action", "")
        prev_act = prev_info.get("raw_action", "")

        # Why: 直前クリックの残留ホバーは親メニューと誤認させず後続の個別昇格・除去へ誘導
        is_prev_residual = _is_residual_hover(prev_info, temp_workflow_info, i - 1)

        # Why: 同一ウィンドウ内での親ホバー直後の子項目移動のみクリックに昇格し別画面移動の誤昇格を防止
        if curr_act == "move" and prev_act == "move" and not is_prev_residual and (prev_win == curr_win):
            cx, cy = curr_info.get("cursor_x", curr_info.get("x", 0)), curr_info.get("cursor_y", curr_info.get("y", 0))
            px, py = prev_info.get("cursor_x", prev_info.get("x", 0)), prev_info.get("cursor_y", prev_info.get("y", 0))
            dist = ((cx - px) ** 2 + (cy - py) ** 2) ** 0.5
            # Why: 下方向かつメガメニュー等の距離(250px以内)の移動をドロップダウンサブメニューと判定
            if 15 <= dist <= 250 and cy >= py - 15:
                curr_info["raw_action"] = "click"
                curr_info["raw_type"] = "mouse_click"
                curr_info["button"] = "left"
                curr_info["window_name"] = prev_win
                if prev_info.get("pre_img_path"):
                    curr_info["pre_img_path"] = prev_info["pre_img_path"]
                # Why: 照合用IDのみ親画像を割り当て、自身のイベントID・座標の完全保持を徹底
                curr_info["match_event_id"] = prev_info.get("event_id")

                clean_title = re.split(r"[\-—–―]", target_title)[0].strip() if target_title else ""
                if clean_title:
                    curr_info["element_name"] = clean_title
                    curr_info["semantic_role"] = clean_title
                    curr_info.setdefault("app_context", {})["element_name"] = clean_title

                logger.info(f"Sub-menu item click promoted at ({cx}, {cy}) for Event: {curr_info.get('event_id')}")
                i += 1
                continue

        candidate_idx = None
        best_match_score = -1

        for k in range(i - 1, max(-1, i - 6), -1):
            cand = temp_workflow_info[k]
            cand_act = cand.get("raw_action", "")

            # Why: 直近のキー入力やクリックを跨いで過去の無関係な移動へ遡るのを完全阻止
            if cand_act in ["click", "type_text", "key_down", "key_press", "press_key"]:
                break
            # Why: 遷移元ウィンドウ外の無関係な移動へ遡るのを防止
            if cand.get("window_name") != prev_win:
                break
            # Why: 直前クリックの残留ホバーを誤昇格して2重クリックになるのを完全阻止
            if _is_residual_hover(cand, temp_workflow_info, k):
                continue

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
                bool(cand_ctx.get("xpath")) or
                cand.get("diff_val", 0.0) > 0.02
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
            # Why: 直前の移動が残留ホバーではない真の親ホバーであるか厳密判定
            has_real_parent = False
            if candidate_idx > 0:
                p_cand = temp_workflow_info[candidate_idx - 1]
                if p_cand.get("raw_action") == "move" and not _is_residual_hover(p_cand, temp_workflow_info, candidate_idx - 1):
                    has_real_parent = True

            if has_real_parent:
                target_cand["raw_action"] = "click"
                target_cand["raw_type"] = "mouse_click"
                target_cand["button"] = "left"
                i += 1
            else:
                # Why: ヘッダーホバーでメニューを展開してからクリックするシークエンスを完全構築
                target_cand["raw_action"] = "move"
                target_cand["raw_type"] = "mouse_move"
                target_cand["is_nav_hover"] = True

                nav_click = target_cand.copy()
                nav_click["raw_action"] = "click"
                nav_click["raw_type"] = "mouse_click"
                nav_click["button"] = "left"
                nav_click["event_id"] = f"{target_cand.get('event_id')}_nav_click"
                nav_click["fallback_events"] = [target_cand.get("event_id")]

                clean_title = re.split(r"[\-—–―]", target_title)[0].strip() if target_title else ""
                if clean_title:
                    nav_click["element_name"] = clean_title
                    nav_click["semantic_role"] = clean_title
                    nav_click.setdefault("app_context", {})["element_name"] = clean_title

                temp_workflow_info.insert(candidate_idx + 1, nav_click)
                i += 2
        elif curr_act == "move" and (prev_win == curr_win):
            # Why: 同一画面内のみ親ホバーを先行生成し、別ウィンドウ遷移直後の移動誤クリック化を完全防止
            lead_hover = curr_info.copy()
            lead_hover["raw_action"] = "move"
            lead_hover["raw_type"] = "mouse_move"
            lead_hover["window_name"] = prev_win
            lead_hover["is_nav_hover"] = True
            lead_hover["event_id"] = f"{curr_info.get('event_id')}_header_hover"

            curr_info["raw_action"] = "click"
            curr_info["raw_type"] = "mouse_click"
            curr_info["button"] = "left"
            curr_info["window_name"] = prev_win
            if prev_info.get("pre_img_path"):
                curr_info["pre_img_path"] = prev_info["pre_img_path"]
            curr_info["match_event_id"] = prev_info.get("event_id")

            clean_title = re.split(r"[\-—–―]", target_title)[0].strip() if target_title else ""
            if clean_title:
                curr_info["element_name"] = clean_title
                curr_info["semantic_role"] = clean_title
                curr_info.setdefault("app_context", {})["element_name"] = clean_title

            temp_workflow_info.insert(i, lead_hover)
            logger.info(f"Direct nav hover and click promoted at ({curr_info.get('cursor_x')}, {curr_info.get('cursor_y')}) for Event: {curr_info.get('event_id')}")
            i += 2
        else:
            i += 1

    return temp_workflow_info

def _reorder_displaced_clicks_before_scroll(temp_workflow_info: List[Dict[str, Any]]) -> List[Dict[str, Any]]:
    # Why: 非破壊ワンパス走査で無限ループを完全排除しクリック直後のスクロール逆転を自動正規化
    if len(temp_workflow_info) < 3:
        return temp_workflow_info

    result = []
    i = 0
    n = len(temp_workflow_info)
    while i < n:
        curr = temp_workflow_info[i]
        if curr.get("raw_action") == "move":
            mx, my = curr.get("cursor_x", curr.get("x", 0)), curr.get("cursor_y", curr.get("y", 0))
            m_win = curr.get("window_name", "")
            j = i + 1
            scrolls = []
            while j < n and temp_workflow_info[j].get("raw_action") == "scroll" and temp_workflow_info[j].get("window_name", "") == m_win:
                scrolls.append(temp_workflow_info[j])
                j += 1
            if scrolls and j < n:
                after = temp_workflow_info[j]
                if after.get("raw_action") == "click" and after.get("window_name", "") == m_win:
                    cx, cy = after.get("cursor_x", after.get("x", 0)), after.get("cursor_y", after.get("y", 0))
                    if ((mx - cx) ** 2 + (my - cy) ** 2) ** 0.5 <= 35:
                        result.append(curr)
                        result.append(after)
                        result.extend(scrolls)
                        logger.info(f"Reordered click (Event: {after.get('event_id')}) before scroll at ({cx}, {cy})")
                        i = j + 1
                        continue
        result.append(curr)
        i += 1
    return result

def _optimize_typing_and_search_flow(temp_workflow_info: List[Dict[str, Any]]) -> List[Dict[str, Any]]:
    # Why: タイピング前の確実なフォーカスと入力後のサジェスト手ブレノイズを除去しEnter検索遷移を完全保証
    if not temp_workflow_info:
        return temp_workflow_info

    n = len(temp_workflow_info)
    result = []
    i = 0
    while i < n:
        curr = temp_workflow_info[i]
        act = curr.get("raw_action", "")

        if act == "move":
            ctx = curr.get("app_context") or {}
            c_type = str(ctx.get("control_type", "")).lower()
            is_input_elem = "edit" in c_type or bool(ctx.get("css_selector")) or bool(curr.get("element_name"))
            
            j = i + 1
            has_subsequent_type = False
            while j < n:
                next_act = temp_workflow_info[j].get("raw_action", "")
                if next_act == "type_text":
                    has_subsequent_type = True
                    break
                elif next_act in ["click", "excel_action", "browser_action"]:
                    break
                j += 1

            if is_input_elem and has_subsequent_type:
                curr["raw_action"] = "click"
                curr["raw_type"] = "mouse_click"
                curr["button"] = "left"
                logger.info(f"Promoted pre-typing move to click for focus at ({curr.get('cursor_x')}, {curr.get('cursor_y')})")

        # 2. タイピング直前の非入力UI(ブラウザロゴ等)クリックを除去しフォーカス奪取を防止
        if act == "click":
            ctx = curr.get("app_context") or {}
            sel = str(ctx.get("css_selector", "")).lower()
            is_browser = any(b in curr.get("window_name", "").lower() for b in ["firefox", "chrome", "edge", "brave", "opera"])
            is_logo_or_bg = any(k in sel for k in ["logo", "wordmark", "brand", "banner"])
            if is_browser and is_logo_or_bg:
                j = i + 1
                while j < min(n, i + 3):
                    if temp_workflow_info[j].get("raw_action") == "type_text":
                        logger.info(f"Removed non-input click on '{sel}' immediately before typing")
                        curr = None
                        break
                    elif temp_workflow_info[j].get("raw_action") in ["click", "excel_action"]:
                        break
                    j += 1
                if curr is None:
                    i += 1
                    continue

        # 3. type_text 直後の文字補完用Tab・サジェスト選択クリック・手ブレ移動を完全除去
        if act == "type_text":
            result.append(curr)
            t_win = curr.get("window_name", "")

            k = i + 1
            noise_indices = set()
            found_target_event = False

            while k < min(n, i + 15):
                nxt = temp_workflow_info[k]
                n_act = nxt.get("raw_action", "")
                n_role = str(nxt.get("semantic_role", "")).lower()
                n_win = nxt.get("window_name", "")

                if n_win != t_win:
                    found_target_event = True
                    break

                if n_act in ["key_down", "key_press", "press_key"] and n_role in ["enter", "return"]:
                    found_target_event = True
                    break

                # Why: 補完済みテキスト入力後のTab(補完用)・矢印キーや過渡的クリック・移動を完全排除
                is_completion_key = n_act in ["key_down", "key_press", "press_key"] and n_role in ["tab", "down", "up", "right", "left"]
                is_intervening_click = (n_act == "click")
                is_intervening_move = (n_act == "move")

                if is_completion_key or is_intervening_click or is_intervening_move:
                    noise_indices.add(k)
                    k += 1
                else:
                    break

            if found_target_event and noise_indices:
                logger.info(f"Cleaned {len(noise_indices)} completion/suggest noise events between typing and search execution")
                i += 1
                while i < n:
                    if i in noise_indices:
                        i += 1
                        continue
                    break
                continue
            else:
                i += 1
                continue

        result.append(curr)
        i += 1

    return result

def _cleanup_redundant_moves_and_scrolls(temp_workflow_info: List[Dict[str, Any]]) -> List[Dict[str, Any]]:
    # Why: スクロール合間の無駄な移動を除去してスクロールを集約しつつ、メニュー出現用ホバーを確実に保持
    if not temp_workflow_info:
        return temp_workflow_info

    n = len(temp_workflow_info)
    filtered = []
    i = 0
    while i < n:
        curr = temp_workflow_info[i]
        act = curr.get("raw_action", "")

        if act == "move":
            # Why: 直前クリックと同一座標の残留ホバーは新画面遷移時に無用なため完全排除
            if _is_residual_hover(curr, temp_workflow_info, i):
                i += 1
                continue

            next_act = None
            next_info = None
            for k in range(i + 1, n):
                a = temp_workflow_info[k].get("raw_action", "")
                if a != "move":
                    next_act = a
                    next_info = temp_workflow_info[k]
                    break

            # Why: スクロール合間の無駄な移動を除去しつつ、画面変化を伴うメニュー展開ホバーやUI要素ホバーは確実に保持
            if next_act == "scroll":
                prev_act = filtered[-1].get("raw_action", "") if filtered else ""
                diff_val = curr.get("diff_val", 0.0)
                has_ui_context = bool(curr.get("app_context", {}).get("element_name") or curr.get("app_context", {}).get("css_selector"))
                is_meaningful = curr.get("is_nav_hover") or diff_val >= 0.005 or (has_ui_context and prev_act != "scroll")
                if not is_meaningful:
                    i += 1
                    continue

            # Why: 直後にクリックがある場合はホバーメニュー展開の可能性を厳密評価
            if next_act == "click" and next_info:
                # Why: ナビゲーションホバーフラグがある場合は同一座標でも展開待機のため確実に保持
                if curr.get("is_nav_hover"):
                    filtered.append(curr)
                    i += 1
                    continue

                if next_info.get("match_event_id") and next_info.get("match_event_id") == curr.get("event_id"):
                    filtered.append(curr)
                    i += 1
                    continue

                cx, cy = next_info.get("cursor_x", next_info.get("x", 0)), next_info.get("cursor_y", next_info.get("y", 0))
                mx, my = curr.get("cursor_x", curr.get("x", 0)), curr.get("cursor_y", curr.get("y", 0))
                dist = ((cx - mx) ** 2 + (cy - my) ** 2) ** 0.5

                # Why: UI情報を持つホバーは直前移動判定でも安全に保護
                ctx = curr.get("app_context") or {}
                has_ui_info = bool(ctx.get("element_name") or ctx.get("css_selector") or ctx.get("xpath") or ctx.get("url"))
                is_meaningful_hover = curr.get("diff_val", 0.0) >= 0.005 or has_ui_info
                if not is_meaningful_hover and dist <= 35:
                    i += 1
                    continue

            filtered.append(curr)
            i += 1
            continue

        filtered.append(curr)
        i += 1

    deduped = []
    m = len(filtered)
    i = 0
    while i < m:
        curr = filtered[i]
        act = curr.get("raw_action", "")

        if act == "move":
            move_group = [curr]
            j = i + 1
            while j < m and filtered[j].get("raw_action") == "move":
                move_group.append(filtered[j])
                j += 1

            if len(move_group) == 1:
                deduped.append(curr)
                i = j
                continue

            next_click = filtered[j] if j < m and filtered[j].get("raw_action") == "click" else None

            if next_click:
                cx, cy = next_click.get("cursor_x", next_click.get("x", 0)), next_click.get("cursor_y", next_click.get("y", 0))
                c_elem = (next_click.get("app_context") or {}).get("element_name", "")

                # Why: 同一要素の手ブレは集約しつつ異なるUIへのホバーは完全保持
                distinct_hovers = []
                for mv in move_group:
                    mx, my = mv.get("cursor_x", mv.get("x", 0)), mv.get("cursor_y", mv.get("y", 0))
                    m_elem = (mv.get("app_context") or {}).get("element_name", "")
                    m_sel = (mv.get("app_context") or {}).get("css_selector", "")

                    if not distinct_hovers:
                        distinct_hovers.append(mv)
                        continue

                    last_h = distinct_hovers[-1]
                    lx, ly = last_h.get("cursor_x", last_h.get("x", 0)), last_h.get("cursor_y", last_h.get("y", 0))
                    l_elem = (last_h.get("app_context") or {}).get("element_name", "")
                    l_sel = (last_h.get("app_context") or {}).get("css_selector", "")
                    dist_to_last = ((mx - lx) ** 2 + (my - ly) ** 2) ** 0.5

                    if (m_elem and m_elem == l_elem) or (m_sel and m_sel == l_sel) or dist_to_last <= 15:
                        distinct_hovers[-1] = mv
                    else:
                        distinct_hovers.append(mv)

                final_hovers = []
                for h in distinct_hovers:
                    hx, hy = h.get("cursor_x", h.get("x", 0)), h.get("cursor_y", h.get("y", 0))
                    h_elem = (h.get("app_context") or {}).get("element_name", "")
                    dist_to_click = ((hx - cx) ** 2 + (hy - cy) ** 2) ** 0.5

                    # Why: クリック対象そのものへの同一位置ホバーのみクリック側へ委譲
                    if dist_to_click <= 15 and (not h_elem or h_elem == c_elem):
                        continue
                    final_hovers.append(h)

                if final_hovers:
                    deduped.extend(final_hovers)
                elif distinct_hovers:
                    meaningful = [h for h in distinct_hovers if h.get("is_nav_hover") or bool((h.get("app_context") or {}).get("element_name"))]
                    if meaningful:
                        deduped.extend(meaningful)
            else:
                deduped.append(move_group[-1])

            i = j
            continue

        deduped.append(curr)
        i += 1

    result = []
    k = len(deduped)
    i = 0
    while i < k:
        curr = deduped[i]
        act = curr.get("raw_action", "")

        if act == "scroll":
            tot_dx = curr.get("dx", 0.0)
            tot_dy = curr.get("dy", 0.0)
            base_x = curr.get("cursor_x", curr.get("x", 0))
            base_y = curr.get("cursor_y", curr.get("y", 0))
            last_eid = curr.get("event_id")
            evts = list(curr.get("fallback_events", [curr.get("event_id")]))

            j = i + 1
            while j < k:
                nxt = deduped[j]
                n_act = nxt.get("raw_action", "")
                if n_act == "scroll":
                    # Why: 同一ウィンドウ内でのスクロールであれば座標の揺れを許容して単一アクションに集約
                    sx, sy = nxt.get("cursor_x", nxt.get("x", 0)), nxt.get("cursor_y", nxt.get("y", 0))
                    if abs(sx - base_x) <= 80 and abs(sy - base_y) <= 80:
                        tot_dx += nxt.get("dx", 0.0)
                        tot_dy += nxt.get("dy", 0.0)
                        last_eid = nxt.get("event_id")
                        evts.extend(nxt.get("fallback_events", [last_eid]))
                        j += 1
                        continue
                    break
                break

            merged = curr.copy()
            merged["dx"] = round(tot_dx, 2)
            merged["dy"] = round(tot_dy, 2)
            merged["event_id"] = last_eid
            merged["fallback_events"] = evts
            result.append(merged)
            i = j
            continue

        result.append(curr)
        i += 1

    return result

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

    def _is_business_action(evt: Dict[str, Any]) -> bool:
        act = evt.get("raw_action", "")
        role = str(evt.get("semantic_role", "")).lower()
        if act in ["click", "type_text", "excel_action", "browser_action"]:
            return True
        if "key" in act and role not in ["win", "cmd", "windows"]:
            return True
        return False

    def _is_system_ui_event(evt: Dict[str, Any]) -> bool:
        ctx = evt.get("app_context") or {}
        elem_name = str(ctx.get("element_name", "")).lower()
        elem_text = str(ctx.get("text", "")).lower()
        role = str(evt.get("semantic_role", "")).lower()
        css = str(ctx.get("css_selector", "")).lower()
        system_terms = ["記録を終了", "記録中", "停止中", "実行中", "ai macro system", "マクロ生成中", "qapplication.qwidget"]
        return any(term in elem_name or term in elem_text or term in role or term in css for term in system_terms)

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
            
            if not is_system and win_name != win_key_window_name:
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
        first_op_idx = app_first_business_op.get(app, len(temp_workflow_info))

        orig_wy = info.get("win_y", 0)
        # Why: 最終座標上書き前に記録時ウィンドウ位置基準でタイトルバー操作を除外
        if i < first_op_idx:
            raw_type = str(info.get("raw_type", "")).lower()
            raw_act = str(info.get("raw_action", "")).lower()
            cy = info.get("cursor_y", 0)
            if "drag" in raw_type or ("click" in raw_act and cy - orig_wy <= 45):
                logger.info(f"[{workflow_id}] Omitted window layout adjustment action (Event: {info.get('event_id')})")
                continue

        if app in app_final_rect and i <= first_op_idx:
            final_x, final_y, final_w, final_h = app_final_rect[app]
            info["win_x"] = final_x
            info["win_y"] = final_y
            info["win_w"] = final_w
            info["win_h"] = final_h

        layout_cleaned.append(info)

    temp_workflow_info = layout_cleaned
    temp_workflow_info = _optimize_typing_and_search_flow(temp_workflow_info)
    temp_workflow_info = _promote_navigation_hover_to_click(temp_workflow_info)
    temp_workflow_info = _reorder_displaced_clicks_before_scroll(temp_workflow_info)
    temp_workflow_info = _cleanup_redundant_moves_and_scrolls(temp_workflow_info)

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
            # Why: 画面上部ウィンドウ枠外や差分のない停止ボタンへの移動のみ除外
            if diff < 0.005 or (cy - wy) <= 45:
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