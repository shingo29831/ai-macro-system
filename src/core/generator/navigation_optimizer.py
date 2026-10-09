"""Module: @role: ワークフローイベント内のナビゲーションホバー、マウス移動・スクロール整流化、および検索フローの最適化を担当する。"""

import logging
import re
from typing import Any, Dict, List

logger = logging.getLogger(__name__)


def is_residual_hover(move_info: Dict[str, Any], temp_workflow_info: List[Dict[str, Any]], current_idx: int) -> bool:
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


def promote_navigation_hover_to_click(temp_workflow_info: List[Dict[str, Any]]) -> List[Dict[str, Any]]:
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

        # Why: 直近クリックの存在判定および画面遷移先タイトルに基づくアクション整合性修復
        has_recent_nav_action = False
        recent_click_info = None
        for idx in range(i - 1, max(-1, i - 4), -1):
            act_info = temp_workflow_info[idx]
            if act_info.get("window_name") != prev_win:
                break
            act_role = str(act_info.get("semantic_role", "")).lower()
            act_act = str(act_info.get("raw_action", "")).lower()
            act_tp = str(act_info.get("raw_type", "")).lower()
            if "click" in act_act or "click" in act_tp:
                recent_click_info = act_info
                has_recent_nav_action = True
                break
            if act_act in ["type_text"] or (
                act_act in ["key_down", "key_press", "press_key"] and act_role in ["enter", "return"]
            ):
                # Why: URL直接遷移以外のキー入力・Enterによる画面遷移アクションを普遍的に検知
                has_recent_nav_action = True
                break

        # Why: 遷移直前(直近移動)に遷移先タイトルやURLに合致するリンクがあれば先行クリックの有無に関わらず最優先昇格
        nav_link_cand = None
        clean_target_title = re.split(r"[\-—–―]", target_title)[0].strip() if target_title else ""
        target_kws = [w for w in re.findall(r"[\w\u3000-\u30ff\u4e00-\u9fff]+", clean_target_title) if len(w) >= 2]

        for k in range(i - 1, max(-1, i - 6), -1):
            cand = temp_workflow_info[k]
            if cand.get("window_name") != prev_win:
                break
            if cand.get("raw_action") == "move":
                c_ctx = cand.get("app_context") or {}
                c_url = str(c_ctx.get("url") or c_ctx.get("text") or c_ctx.get("value") or "").strip()
                c_name = str(c_ctx.get("element_name") or cand.get("element_name") or "").strip()
                c_ctl = str(c_ctx.get("control_type", "")).lower()

                matched = False
                if c_url.startswith("http"):
                    if target_url and (c_url.split("#")[0] in target_url or target_url.split("#")[0] in c_url):
                        matched = True
                    elif target_kws and any(kw.lower() in c_url.lower() for kw in target_kws):
                        matched = True
                if not matched and c_name and target_kws:
                    if any(kw.lower() in c_name.lower() for kw in target_kws):
                        matched = True
                if matched or "hyperlink" in c_ctl:
                    nav_link_cand = cand
                    break

        if nav_link_cand is not None:
            nav_link_cand["raw_action"] = "click"
            nav_link_cand["raw_type"] = "mouse_click"
            nav_link_cand["button"] = "left"
            if clean_target_title:
                nav_link_cand["element_name"] = clean_target_title
                nav_link_cand["semantic_role"] = clean_target_title
                nav_link_cand.setdefault("app_context", {})["element_name"] = clean_target_title
            logger.info(f"Promoted navigation link move to click for Event: {nav_link_cand.get('event_id')} targeting '{clean_target_title}'")
            i += 1
            continue

        # Why: 遷移契機となったクリックまたは遷移先自身のクリック要素名を実遷移先タイトルで完全修復
        target_click = recent_click_info if recent_click_info else (curr_info if curr_info.get("raw_action") == "click" else None)
        if target_click:
            if clean_target_title:
                click_elem_name = str(target_click.get("element_name") or target_click.get("semantic_role") or "").strip()
                click_ctx = target_click.setdefault("app_context", {})
                click_url = str(click_ctx.get("url") or target_click.get("url") or "").strip()

                title_mismatch = False
                if target_kws and click_elem_name:
                    if not any(kw.lower() in click_elem_name.lower() for kw in target_kws):
                        title_mismatch = True

                # Why: 記録時UIA誤判定や無名divクリックを実遷移先タイトルで完全修復
                if title_mismatch or not click_elem_name or click_elem_name in ["left_click", "move", "click"]:
                    logger.info(f"Reconciling clicked element '{click_elem_name}' with actual destination window title '{clean_target_title}'")
                    target_click["element_name"] = clean_target_title
                    target_click["semantic_role"] = clean_target_title
                    click_ctx["element_name"] = clean_target_title

                    clean_title_esc = clean_target_title.replace("'", "\\'")
                    target_click["selector"] = f"a:has-text('{clean_title_esc}')"
                    target_click["selector_type"] = "css"
                    click_ctx["css_selector"] = f"a:has-text('{clean_title_esc}')"
                    click_ctx["xpath"] = f"//a[contains(., '{clean_title_esc}')]"

                    if click_url and any(kw.lower() not in click_url.lower() for kw in target_kws):
                        target_click.pop("url", None)
                        click_ctx.pop("url", None)

        if has_recent_nav_action or curr_info.get("raw_action") == "click":
            i += 1
            continue

        curr_act = curr_info.get("raw_action", "")
        prev_act = prev_info.get("raw_action", "")

        # Why: 直前クリックの残留ホバーは親メニューと誤認させず後続の個別昇格・除去へ誘導
        is_prev_residual = is_residual_hover(prev_info, temp_workflow_info, i - 1)

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
            if is_residual_hover(cand, temp_workflow_info, k):
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
                if p_cand.get("raw_action") == "move" and not is_residual_hover(p_cand, temp_workflow_info, candidate_idx - 1):
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


def reorder_displaced_clicks_before_scroll(temp_workflow_info: List[Dict[str, Any]]) -> List[Dict[str, Any]]:
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


def optimize_typing_and_search_flow(temp_workflow_info: List[Dict[str, Any]]) -> List[Dict[str, Any]]:
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

            # Why: ウィンドウ枠外やタイトルバーの移動は入力フォーカスクリックへ誤昇格させない
            cy = curr.get("cursor_y", curr.get("y", 0))
            wy = curr.get("win_y", 0)
            if cy - wy <= 45 or cy < -50:
                is_input_elem = False

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


def cleanup_redundant_moves_and_scrolls(temp_workflow_info: List[Dict[str, Any]]) -> List[Dict[str, Any]]:
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
            if is_residual_hover(curr, temp_workflow_info, i):
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

            # Why: スクロール合間の無駄な移動を除去しつつ、サイト訪問・URL遷移やUI要素ホバーは確実に保持
            if next_act == "scroll":
                prev_act = filtered[-1].get("raw_action", "") if filtered else ""
                diff_val = curr.get("diff_val", 0.0)
                ctx = curr.get("app_context") or {}
                has_url = bool(str(ctx.get("url") or ctx.get("text") or ctx.get("value") or "").startswith("http"))
                has_ui_context = bool(ctx.get("element_name") or ctx.get("css_selector") or has_url)
                is_meaningful = curr.get("is_nav_hover") or has_url or diff_val >= 0.005 or (has_ui_context and prev_act != "scroll")
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
                c_elem = str((next_click.get("app_context") or {}).get("element_name", "")).strip()

                # Why: クリック前の一連の移動からメニュー親要素(起点ホバー)のみを最大1つ厳選し過渡移動を全消去
                best_hover = None
                for mv in move_group:
                    mx, my = mv.get("cursor_x", mv.get("x", 0)), mv.get("cursor_y", mv.get("y", 0))
                    dist_to_click = ((mx - cx) ** 2 + (my - cy) ** 2) ** 0.5
                    if dist_to_click <= 35:
                        continue

                    m_ctx = mv.get("app_context") or {}
                    m_elem = str(m_ctx.get("element_name") or mv.get("element_name") or "").strip()
                    m_sel = str(m_ctx.get("css_selector") or "").lower()
                    diff_val = mv.get("diff_val", 0.0)

                    # Why: 要素名なしでも画面変化(Diff>=3%)を伴うメニュー展開ホバーを確実に保持
                    is_menu_parent = (
                        mv.get("is_nav_hover") or
                        any(k in m_sel for k in ["nav", "menu", "drop", "header"]) or
                        (bool(m_elem) and m_elem != c_elem and m_elem not in ["move", "left_click"]) or
                        diff_val >= 0.03
                    )
                    if is_menu_parent:
                        best_hover = mv
                        break

                if best_hover:
                    deduped.append(best_hover)
            else:
                meaningful_navs = [
                    m for m in move_group 
                    if str((m.get("app_context") or {}).get("url") or "").startswith("http") or m.get("is_nav_hover")
                ]
                if meaningful_navs:
                    deduped.append(meaningful_navs[0])
                else:
                    # Why: 後続クリックがない場合も新ウィンドウ遷移後や有意差分を持つ操作は破棄せず保持
                    first_m = move_group[0]
                    prev_win = deduped[-1].get("window_name", "") if deduped else ""
                    curr_win = first_m.get("window_name", "")
                    has_win_transition = bool(prev_win and curr_win and prev_win != curr_win)
                    for m_cand in move_group:
                        m_diff = m_cand.get("diff_val", 0.0)
                        m_ctx = m_cand.get("app_context") or {}
                        m_has_ui = bool(m_ctx.get("element_name") or m_ctx.get("css_selector") or m_cand.get("element_name"))
                        if has_win_transition or m_diff >= 0.02 or m_has_ui:
                            deduped.append(m_cand)
                            break

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
            curr_win = curr.get("window_name", "")
            last_eid = curr.get("event_id")
            last_ts = curr.get("timestamp", 0)
            evts = list(curr.get("fallback_events", [curr.get("event_id")]))

            j = i + 1
            while j < k:
                nxt = deduped[j]
                n_act = nxt.get("raw_action", "")
                if n_act == "scroll":
                    n_win = nxt.get("window_name", "")
                    if curr_win and n_win and curr_win != n_win:
                        break
                    nxt_ts = nxt.get("timestamp", 0)
                    ts_gap = abs(nxt_ts - last_ts) if (nxt_ts and last_ts) else 0
                    # Why: スクロール操作中のカーソル移動に惑わされず起点位置でセッションを完全統合
                    if ts_gap > 800:
                        break

                    nxt_dx = nxt.get("dx", 0.0)
                    nxt_dy = nxt.get("dy", 0.0)

                    # Why: 行き過ぎ戻し等の逆方向スクロールも合算し正味移動量を完全集約
                    tot_dx += nxt_dx
                    tot_dy += nxt_dy
                    last_eid = nxt.get("event_id")
                    last_ts = nxt_ts or last_ts
                    evts.extend(nxt.get("fallback_events", [last_eid]))
                    j += 1
                    continue
                break

            fin_dx = round(tot_dx, 2)
            fin_dy = round(tot_dy, 2)
            # Why: 相殺や微小入力でゼロとなった無効スクロールは出力から完全除外
            if fin_dx != 0.0 or fin_dy != 0.0:
                merged = curr.copy()
                merged["dx"] = fin_dx
                merged["dy"] = fin_dy
                merged["event_id"] = last_eid
                merged["fallback_events"] = evts
                result.append(merged)
            i = j
            continue

        result.append(curr)
        i += 1

    return result
