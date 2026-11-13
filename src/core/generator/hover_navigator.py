"""Module: @role: ナビゲーションホバーの残留判定と画面遷移契機要素のクリック昇格・修復を担当する。"""

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


def _detect_transition(curr_info: Dict[str, Any], prev_info: Dict[str, Any]) -> bool:
    curr_win = curr_info.get("window_name", "")
    prev_win = prev_info.get("window_name", "")
    curr_ctx = curr_info.get("app_context") or {}
    curr_url = str(curr_ctx.get("url") or curr_ctx.get("text") or curr_ctx.get("value") or "").strip()

    if prev_win and curr_win and prev_win != curr_win:
        w1_parts = [p.strip().lower() for p in re.split(r"[\-—–―]", prev_win)]
        w2_parts = [p.strip().lower() for p in re.split(r"[\-—–―]", curr_win)]
        if w1_parts and w2_parts and w1_parts[-1] == w2_parts[-1]:
            return True

    if curr_url.startswith("http"):
        prev_ctx = prev_info.get("app_context") or {}
        prev_url = str(prev_ctx.get("url") or prev_ctx.get("text") or prev_ctx.get("value") or "").strip()
        if prev_url and prev_url != curr_url:
            if prev_url.split("#")[0].rstrip("/") != curr_url.split("#")[0].rstrip("/"):
                return True

    return False


def _reconcile_click_with_title(
    target_click: Dict[str, Any], clean_target_title: str, target_kws: List[str]
) -> None:
    if not clean_target_title:
        return
    click_elem_name = str(target_click.get("element_name") or target_click.get("semantic_role") or "").strip()
    click_ctx = target_click.setdefault("app_context", {})
    click_url = str(click_ctx.get("url") or target_click.get("url") or "").strip()

    title_mismatch = False
    if target_kws and click_elem_name:
        if not any(kw.lower() in click_elem_name.lower() for kw in target_kws):
            title_mismatch = True

    # Why: 記録時UIA誤判定や無名divクリックを実遷移先タイトルで完全修復
    if title_mismatch or not click_elem_name or click_elem_name in ["left_click", "move", "click"]:
        logger.info(
            f"Reconciling clicked element '{click_elem_name}' with actual destination window title '{clean_target_title}'"
        )
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


def promote_navigation_hover_to_click(temp_workflow_info: List[Dict[str, Any]]) -> List[Dict[str, Any]]:
    # Why: ドロップダウン等のホバー操作を保持しつつ遷移の契機となった要素のクリックを正しく生成
    if not temp_workflow_info:
        return temp_workflow_info

    i = 1
    while i < len(temp_workflow_info):
        curr_info = temp_workflow_info[i]
        prev_info = temp_workflow_info[i - 1]

        if not _detect_transition(curr_info, prev_info):
            i += 1
            continue

        # Why: 遷移先自身がクリック、または直近にEnter・テキスト入力があればキー操作遷移のためホバー昇格不要
        if curr_info.get("raw_action") == "click":
            i += 1
            continue

        prev_win = prev_info.get("window_name", "")
        curr_win = curr_info.get("window_name", "")
        curr_ctx = curr_info.get("app_context") or {}
        target_url = str(curr_ctx.get("url") or curr_ctx.get("text") or curr_ctx.get("value") or "").strip()
        target_title = curr_win

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
            logger.info(
                f"Promoted navigation link move to click for Event: {nav_link_cand.get('event_id')} targeting '{clean_target_title}'"
            )
            i += 1
            continue

        target_click = (
            recent_click_info if recent_click_info else (curr_info if curr_info.get("raw_action") == "click" else None)
        )
        if target_click:
            _reconcile_click_with_title(target_click, clean_target_title, target_kws)

        if has_recent_nav_action or curr_info.get("raw_action") == "click":
            i += 1
            continue

        curr_act = curr_info.get("raw_action", "")
        prev_act = prev_info.get("raw_action", "")
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

                if clean_target_title:
                    curr_info["element_name"] = clean_target_title
                    curr_info["semantic_role"] = clean_target_title
                    curr_info.setdefault("app_context", {})["element_name"] = clean_target_title

                logger.info(f"Sub-menu item click promoted at ({cx}, {cy}) for Event: {curr_info.get('event_id')}")
                i += 1
                continue

        candidate_idx = None
        best_match_score = -1

        for k in range(i - 1, max(-1, i - 6), -1):
            cand = temp_workflow_info[k]
            cand_act = cand.get("raw_action", "")

            if cand_act in ["click", "type_text", "key_down", "key_press", "press_key"]:
                break
            if cand.get("window_name") != prev_win:
                break
            if is_residual_hover(cand, temp_workflow_info, k):
                continue
            if cand_act != "move":
                continue

            cand_ctx = cand.get("app_context") or {}
            c_type = str(cand_ctx.get("control_type", "")).lower()
            c_url = str(cand_ctx.get("url") or cand_ctx.get("text") or cand_ctx.get("value") or "").strip()
            c_name = str(cand_ctx.get("element_name", "")).strip()

            is_interactive = (
                "hyperlink" in c_type
                or "button" in c_type
                or "menuitem" in c_type
                or c_url.startswith("http")
                or bool(cand_ctx.get("css_selector"))
                or bool(cand_ctx.get("xpath"))
                or cand.get("diff_val", 0.0) > 0.02
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
                clean_lower = clean_target_title.lower()
                if clean_lower and (clean_lower in c_name.lower() or clean_lower in c_url.lower()):
                    score = max(score, 9)

            if score > best_match_score:
                best_match_score = score
                candidate_idx = k

        if candidate_idx is not None:
            target_cand = temp_workflow_info[candidate_idx]
            has_real_parent = False
            if candidate_idx > 0:
                p_cand = temp_workflow_info[candidate_idx - 1]
                if p_cand.get("raw_action") == "move" and not is_residual_hover(
                    p_cand, temp_workflow_info, candidate_idx - 1
                ):
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

                if clean_target_title:
                    nav_click["element_name"] = clean_target_title
                    nav_click["semantic_role"] = clean_target_title
                    nav_click.setdefault("app_context", {})["element_name"] = clean_target_title

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

            if clean_target_title:
                curr_info["element_name"] = clean_target_title
                curr_info["semantic_role"] = clean_target_title
                curr_info.setdefault("app_context", {})["element_name"] = clean_target_title

            temp_workflow_info.insert(i, lead_hover)
            logger.info(
                f"Direct nav hover and click promoted at ({curr_info.get('cursor_x')}, {curr_info.get('cursor_y')}) "
                f"for Event: {curr_info.get('event_id')}"
            )
            i += 2
        else:
            i += 1

    return temp_workflow_info
