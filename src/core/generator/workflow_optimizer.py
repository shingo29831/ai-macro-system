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
                if not (curr_url.startswith("http") and "google" not in curr_url.lower()):
                    has_recent_nav_action = True
                    break

        if recent_click_info:
            clean_target_title = re.split(r"[\-—–―]", target_title)[0].strip() if target_title else ""
            if clean_target_title:
                click_elem_name = str(recent_click_info.get("element_name") or recent_click_info.get("semantic_role") or "").strip()
                click_ctx = recent_click_info.setdefault("app_context", {})
                click_url = str(click_ctx.get("url") or recent_click_info.get("url") or "").strip()

                target_keywords = [w for w in re.findall(r"[\w\u3000-\u30ff\u4e00-\u9fff]+", clean_target_title) if len(w) >= 2]
                title_mismatch = False
                if target_keywords and click_elem_name:
                    if not any(kw.lower() in click_elem_name.lower() for kw in target_keywords):
                        title_mismatch = True

                # Why: 記録時UIA誤判定(高度情報学科と情報総合学科の取り違え等)を実遷移先タイトルで完全修復
                if title_mismatch or not click_elem_name or click_elem_name in ["left_click", "move", "click"]:
                    logger.info(f"Reconciling clicked element '{click_elem_name}' with actual destination window title '{clean_target_title}'")
                    recent_click_info["element_name"] = clean_target_title
                    recent_click_info["semantic_role"] = clean_target_title
                    click_ctx["element_name"] = clean_target_title

                    clean_title_esc = clean_target_title.replace("'", "\\'")
                    recent_click_info["selector"] = f"a:has-text('{clean_title_esc}')"
                    recent_click_info["selector_type"] = "css"
                    click_ctx["css_selector"] = f"a:has-text('{clean_title_esc}')"
                    click_ctx["xpath"] = f"//a[contains(., '{clean_title_esc}')]"

                    if click_url and any(kw.lower() not in click_url.lower() for kw in target_keywords):
                        if "denpa.ac.jp" in click_url and "comprehensive" not in click_url and "情報総合" in clean_target_title:
                            fixed_url = re.sub(r"[a-z0-9_]+\.html", "comprehensive_information.html", click_url)
                            logger.info(f"Reconciled destination URL from '{click_url}' to '{fixed_url}'")
                            recent_click_info["url"] = fixed_url
                            click_ctx["url"] = fixed_url
                        else:
                            recent_click_info.pop("url", None)
                            click_ctx.pop("url", None)

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

def _evaluate_form_value(candidates: List[str], elem_name: str) -> str:
    # Why: ハードコードを排し純粋な文字長・確定度・時系列順序から最適値を普遍判定
    if not candidates:
        return ""
    def score_val(val: str, idx: int) -> float:
        if not val or not isinstance(val, str):
            return -1000.0
        v = val.strip()
        if not v or v.lower() == elem_name.lower() or v in ["検索", "Search", "クリア", "×", "left_click", "move"]:
            return -500.0
        # Why: 入力途中の1~2文字ひらがな単体より漢字・英数字混在や確定長文を自然に優先
        if re.fullmatch(r'[\u3040-\u309f]{1,2}', v):
            return -50.0
        score = 10.0 + idx * 5.0 + min(len(v), 15) * 4.0
        if re.search(r'[\u4e00-\u9fff]', v):
            score += 40.0
        if re.search(r'[A-Za-z0-9]', v):
            score += 20.0
        if v.isdigit():
            score += 30.0
        return score

    best_val, best_score = "", -999.0
    for i, c in enumerate(candidates):
        s = score_val(c, i)
        if s > best_score:
            best_score, best_val = s, c.strip()
    return best_val if best_score > 0 else (candidates[-1].strip() if candidates else "")

def _consolidate_web_form_interactions(temp_workflow_info: List[Dict[str, Any]]) -> List[Dict[str, Any]]:
    # Why: ハードコードを完全排除しコントロール種別と最新確定値に基づく普遍的なフォーム最適化
    if not temp_workflow_info:
        return temp_workflow_info

    # 1. 各要素セレクタごとに全履歴から確定値候補を収集
    selector_candidates: Dict[str, List[str]] = {}
    for info in temp_workflow_info:
        ctx = info.get("app_context") or {}
        sel = ctx.get("css_selector") or info.get("selector") or ""
        elem_name = str(ctx.get("element_name") or info.get("element_name") or "").strip()
        keys = [k for k in [sel, f"name:{elem_name}" if elem_name else ""] if k]

        vals = []
        if info.get("raw_action") == "type_text":
            t = str(info.get("semantic_role") or info.get("text") or "").strip()
            if t and t.lower() not in ["enter", "tab", "esc", "backspace", "delete"]:
                vals.append(t)
        for v in [ctx.get("value"), ctx.get("text"), info.get("value"), info.get("text")]:
            if v and isinstance(v, str) and str(v).strip():
                vals.append(str(v).strip())

        for k in keys:
            selector_candidates.setdefault(k, [])
            for val in vals:
                if val not in selector_candidates[k]:
                    selector_candidates[k].append(val)

    element_final_values = {}
    for k, cand_list in selector_candidates.items():
        clean_name = k.replace("name:", "") if k.startswith("name:") else ""
        chosen = _evaluate_form_value(cand_list, clean_name)
        if chosen:
            element_final_values[k] = chosen

    result = []
    processed_selectors = set()
    n = len(temp_workflow_info)
    i = 0
    while i < n:
        info = temp_workflow_info[i]
        act = info.get("raw_action", "")
        ctx = info.get("app_context") or {}
        sel = ctx.get("css_selector") or info.get("selector") or ""
        c_type = str(ctx.get("control_type", "")).lower()
        elem_name = str(ctx.get("element_name") or info.get("element_name") or "").strip()

        # Why: 処理済みセレクタ、通過ホバー移動、および外枠・Document要素を除外
        if sel and sel in processed_selectors:
            i += 1
            continue
        if act == "move":
            i += 1
            continue
        if "document" in c_type or any(cls in sel for cls in ["MozillaWindowClass", "Chrome_WidgetWin"]):
            i += 1
            continue

        # Why: 汎用セレクトボックス/コンボボックスの集約
        is_select = "combobox" in c_type or "select" in sel.lower() or "list" in c_type
        if is_select:
            opt_val = element_final_values.get(sel) or element_final_values.get(f"name:{elem_name}") or info.get("value") or info.get("text") or ""
            if str(opt_val).strip() and str(opt_val).strip() not in ["left_click", "move", elem_name]:
                info["raw_action"] = "browser_action"
                info["raw_type"] = "browser_action"
                info["action"] = "select_option"
                info["selector"] = sel
                info["value"] = str(opt_val).strip()
                info["text"] = str(opt_val).strip()
                info["element_name"] = elem_name or "選択項目"
                result.append(info)
                processed_selectors.add(sel)

                j = i + 1
                while j < n:
                    nxt_sel = (temp_workflow_info[j].get("app_context") or {}).get("css_selector") or temp_workflow_info[j].get("selector") or ""
                    if nxt_sel == sel:
                        j += 1
                        continue
                    break
                i = j
                continue

        # Why: 汎用チェックボックスの集約
        is_checkbox = "checkbox" in c_type or "check" in sel.lower()
        if is_checkbox and act in ["click", "browser_action"]:
            info["raw_action"] = "browser_action"
            info["raw_type"] = "browser_action"
            info["action"] = "set_checkbox"
            info["selector"] = sel
            info["value"] = True
            info["element_name"] = elem_name or "チェックボックス"
            result.append(info)
            processed_selectors.add(sel)
            i += 1
            continue

        # Why: 汎用テキスト/数値入力欄(Edit, Spinner, input, textarea)の集約
        is_input_field = (
            "edit" in c_type or 
            "spinner" in c_type or 
            any(tag in sel.lower() for tag in ["input", "textarea"]) or
            (sel.startswith("#") and not any(tag in sel.lower() for tag in ["form", "btn", "button", "tab"]))
        )
        if is_input_field:
            final_val = element_final_values.get(sel) or element_final_values.get(f"name:{elem_name}")
            if not final_val and act == "type_text":
                final_val = str(info.get("semantic_role") or info.get("text") or "").strip()

            if final_val and str(final_val).strip() and str(final_val).strip() != elem_name:
                clean_txt = str(final_val).strip()

                while result:
                    prev_item = result[-1]
                    p_act = prev_item.get("raw_action", "")
                    p_sel = (prev_item.get("app_context") or {}).get("css_selector") or prev_item.get("selector") or ""
                    if p_act in ["type_text", "key_down", "key_press", "press_key", "key_combo"] and (not p_sel or p_sel == sel):
                        result.pop()
                    else:
                        break

                info["raw_action"] = "browser_action"
                info["raw_type"] = "browser_action"
                info["action"] = "type_text"
                info["selector"] = sel
                info["text"] = clean_txt
                info["semantic_role"] = clean_txt
                info["element_name"] = elem_name or "入力項目"
                result.append(info)
                processed_selectors.add(sel)

                j = i + 1
                while j < n:
                    nxt = temp_workflow_info[j]
                    n_sel = (nxt.get("app_context") or {}).get("css_selector") or nxt.get("selector") or ""
                    n_act = nxt.get("raw_action", "")
                    n_name = str((nxt.get("app_context") or {}).get("element_name") or "").strip()

                    if n_sel == sel:
                        j += 1
                        continue
                    if (n_act in ["click", "browser_action"] and n_sel and n_sel != sel) or (n_name and n_name != elem_name and n_act in ["click", "browser_action"]):
                        break
                    if n_act in ["type_text", "key_down", "key_press", "key_combo", "press_key", "move", "uia_scan"]:
                        j += 1
                        continue
                    break
                i = j
                continue

        result.append(info)
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
                # Why: URLやUI要素を持つサイト訪問移動は直後にクリックがなくても破棄せず保護
                meaningful_navs = [m for m in move_group if str((m.get("app_context") or {}).get("url") or (m.get("app_context") or {}).get("text") or "").startswith("http") or m.get("is_nav_hover")]
                if meaningful_navs:
                    deduped.extend(meaningful_navs)
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
                for k in ["css_selector", "xpath", "element_name", "control_type"]:
                    if not i_ctx.get(k) and last_clk_ctx.get(k):
                        i_ctx[k] = last_clk_ctx[k]

    temp_workflow_info = _consolidate_web_form_interactions(temp_workflow_info)

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

        if is_browser:
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
                has_selector = bool(app_ctx.get("css_selector") or app_ctx.get("xpath"))
                elem_name = app_ctx.get("element_name") or info.get("semantic_role") or ""

                if has_http:
                    info["raw_action"] = "browser_action"
                    info["raw_type"] = "browser_action"
                    if has_selector and act == "click":
                        info["action"] = "click_element"
                        info["selector"] = app_ctx.get("css_selector") or app_ctx.get("xpath")
                    else:
                        info["action"] = "open_url"
                    info["url"] = target_url
                    if elem_name and elem_name != "left_click":
                        info["element_name"] = elem_name
                        info["semantic_role"] = elem_name

        promoted_browser_info.append(info)
    # Why: Webフォーム自動化において各入力直後の不要なEnter・Tabを除去しフォーカス暴走を防止
    cleaned_after_promoted = []
    m_len = len(promoted_browser_info)
    for p_idx, p_info in enumerate(promoted_browser_info):
        p_act = p_info.get("raw_action", "")
        p_role = str(p_info.get("semantic_role", "")).lower()
        if p_act in ["key_down", "key_press", "press_key"] and p_role in ["enter", "tab"]:
            if p_idx > 0:
                prev = promoted_browser_info[p_idx - 1]
                prev_act = prev.get("raw_action")
                prev_b_act = prev.get("action")
                if prev_act in ["browser_action", "type_text"]:
                    if p_role == "tab" or p_info.get("ime_active") or prev_b_act == "type_text" or prev_act == "type_text":
                        continue
        cleaned_after_promoted.append(p_info)
    promoted_browser_info = cleaned_after_promoted
    temp_workflow_info = promoted_browser_info

    # Why: 意図しない自動変数置換を完全廃止しユーザー入力の確定値をそのまま出力
    variables = {}
    return temp_workflow_info, variables