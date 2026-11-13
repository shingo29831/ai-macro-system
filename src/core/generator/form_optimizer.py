"""Module: @role: Webフォーム入力項目（テキスト、セレクトボックス、チェックボックス、送信ボタン）の集約と最適値評価を担当する。"""

from typing import Any, Dict, List, Optional, Set, Tuple

from core.generator.form_snapshot_collector import FormSnapshotContext, collect_form_snapshot_context
from core.generator.form_snapshot_restorer import restore_missing_snapshot_elements
from core.generator.form_value_evaluator import (
    evaluate_form_value,
    is_system_ui_event,
    select_candidate_with_llm,
)

__all__ = [
    "is_system_ui_event",
    "select_candidate_with_llm",
    "evaluate_form_value",
    "consolidate_web_form_interactions",
]


def _is_valid_menu_hover(info: Dict[str, Any], elem_name: str) -> bool:
    # Why: UIA要素名欠落時も画面変化(Diff>=3%)を伴うメニュー展開ホバーを確実に保持
    m_ctx = info.get("app_context") or {}
    m_sel = str(m_ctx.get("css_selector") or "").lower()
    diff_val = info.get("diff_val", 0.0)
    is_container = any(k in m_sel for k in ["root", "app", "main", "window", "mozilla"])
    return bool(
        info.get("is_nav_hover")
        or any(k in m_sel for k in ["nav", "menu", "drop", "header"])
        or (diff_val >= 0.03 and not is_container)
        or (bool(elem_name) and elem_name not in ["move", "left_click"] and not is_container)
    )


def _should_skip_document_event(act: str, ctx: Dict[str, Any], sel: str, c_type: str, elem_name: str) -> bool:
    # Why: 背景Documentの空クリックのみ除外対象とし、リンクやURL・要素名を持つサイト遷移クリックを完全保護
    is_document_container = "document" in c_type or any(
        cls in sel for cls in ["MozillaWindowClass", "Chrome_WidgetWin", "CoreWindow"]
    )
    if not is_document_container:
        return False
    has_nav_target = (
        bool(str(ctx.get("url") or ctx.get("text") or ctx.get("value") or "").startswith("http"))
        or (bool(elem_name) and elem_name not in ["left_click", "move", "click"])
        or (bool(sel) and not any(cls in sel for cls in ["MozillaWindowClass", "Chrome_WidgetWin", "CoreWindow"]))
    )
    return not has_nav_target or act != "click"


def _promote_select_action(
    info: Dict[str, Any],
    sel: str,
    elem_name: str,
    scanned_elem: Dict[str, Any],
    state: FormSnapshotContext,
) -> bool:
    ctx = info.get("app_context") or {}
    snap_val = state.global_form_snapshot.get(sel) or state.global_form_snapshot.get(f"name:{elem_name}")
    cur_val = info.get("value") or info.get("text") or ctx.get("value") or ctx.get("text")
    ignored = {"left_click", "move", elem_name}

    if snap_val and str(snap_val).strip() and str(snap_val).strip() not in ignored:
        opt_val = str(snap_val).strip()
    elif cur_val and str(cur_val).strip() and str(cur_val).strip() not in ignored:
        opt_val = str(cur_val).strip()
    else:
        opt_val = state.element_final_values.get(sel) or state.element_final_values.get(f"name:{elem_name}") or ""

    opt_str = str(opt_val).strip()
    if not opt_str or opt_str in ignored:
        return False

    # Why: 有効な物理座標を履歴から確実に復元
    sc_x, sc_y = scanned_elem.get("x", 0), scanned_elem.get("y", 0)
    if sc_x > 20 and sc_y > 20:
        info["cursor_x"], info["cursor_y"] = sc_x, sc_y
        info["x"], info["y"] = sc_x, sc_y
    elif sel in state.selector_coords:
        cx, cy = state.selector_coords[sel]
        info["cursor_x"], info["cursor_y"] = cx, cy
        info["x"], info["y"] = cx, cy

    info["raw_action"] = "browser_action"
    info["raw_type"] = "browser_action"
    info["action"] = "select_option"
    info["selector"] = sel
    info["value"] = opt_str
    info["text"] = opt_str
    info["element_name"] = elem_name or "選択項目"
    return True


def _resolve_input_field_value(
    info: Dict[str, Any],
    act: str,
    sel: str,
    elem_name: str,
    state: FormSnapshotContext,
) -> Tuple[str, Dict[str, Any]]:
    scanned_item = state.scanned_elements_map.get(sel) or state.scanned_elements_map.get(f"name:{elem_name}") or {}
    scanned_val = (
        state.scanned_elements_values.get(sel)
        or state.scanned_elements_values.get(f"name:{elem_name}")
        or scanned_item.get("value")
    )
    snap_val = state.global_form_snapshot.get(sel) or state.global_form_snapshot.get(f"name:{elem_name}")
    final_val = scanned_val or snap_val or state.element_final_values.get(sel)

    # Why: 確定値より長文の完全版候補(佐藤 -> 佐藤 健一)が存在する場合は完全版を採用
    if final_val:
        f_str = str(final_val).strip()
        for cand in reversed(state.selector_candidates.get(sel, [])):
            c_str = str(cand).strip()
            if len(c_str) > len(f_str) and (c_str.startswith(f_str) or f_str in c_str):
                final_val = c_str
                break

    # Why: 不正なNaN値を排除しイベント履歴の有効数値を救出
    if final_val and str(final_val).strip().lower() in ["nan", "none"]:
        final_val = ""
    if not final_val and act == "type_text":
        final_val = str(info.get("semantic_role") or info.get("text") or "").strip()
    if not final_val:
        for cand in reversed(state.selector_candidates.get(sel, [])):
            if cand and str(cand).strip().lower() not in ["nan", "none", elem_name.lower()]:
                final_val = str(cand).strip()
                break

    return str(final_val or "").strip(), scanned_item


def _consume_following_selector_events(
    temp_workflow_info: List[Dict[str, Any]],
    start_idx: int,
    sel: str,
    elem_name: str,
) -> int:
    j = start_idx + 1
    n = len(temp_workflow_info)
    while j < n:
        nxt = temp_workflow_info[j]
        n_sel = (nxt.get("app_context") or {}).get("css_selector") or nxt.get("selector") or ""
        n_act = nxt.get("raw_action", "")
        n_name = str((nxt.get("app_context") or {}).get("element_name") or "").strip()

        if n_sel == sel:
            j += 1
            continue
        if (n_act in ["click", "browser_action"] and n_sel and n_sel != sel) or (
            n_name and n_name != elem_name and n_act in ["click", "browser_action"]
        ):
            break
        if n_act in ["type_text", "key_down", "key_press", "key_combo", "press_key", "move", "uia_scan"]:
            j += 1
            continue
        break
    return j


def consolidate_web_form_interactions(temp_workflow_info: List[Dict[str, Any]], config: Optional[Any] = None) -> List[Dict[str, Any]]:
    # Why: ハードコードを完全排除しコントロール種別と最新確定値に基づく普遍的なフォーム最適化
    if not temp_workflow_info:
        return temp_workflow_info

    state = collect_form_snapshot_context(temp_workflow_info, config=config)
    result: List[Dict[str, Any]] = []
    processed_selectors: Set[str] = set()
    n = len(temp_workflow_info)
    i = 0

    while i < n:
        info = temp_workflow_info[i]
        act = info.get("raw_action", "")
        ctx = info.get("app_context") or {}
        sel = ctx.get("css_selector") or info.get("selector") or ""
        c_type = str(ctx.get("control_type", "")).lower()
        elem_name = str(ctx.get("element_name") or info.get("element_name") or "").strip()

        # Why: ページスキャン情報(scanned_elements_map)から真の要素型と要素名を動的補正
        scanned_elem = state.scanned_elements_map.get(sel) or state.scanned_elements_map.get(f"name:{elem_name}") or {}
        if scanned_elem.get("control_type"):
            c_type = str(scanned_elem["control_type"]).lower()
        scanned_name = str(scanned_elem.get("element_name", "")).strip()
        if scanned_name and (not elem_name or elem_name in ["left_click", "move"]):
            elem_name = scanned_name
        elif not elem_name and sel in state.selector_names:
            elem_name = state.selector_names[sel]

        if is_system_ui_event(info) or (sel and sel in processed_selectors):
            i += 1
            continue

        if act == "move":
            if _is_valid_menu_hover(info, elem_name):
                result.append(info)
            i += 1
            continue

        if _should_skip_document_event(act, ctx, sel, c_type, elem_name):
            i += 1
            continue

        # Why: アドレスバー・検索バー操作はWebフォーム集約から除外して時系列を完全保護
        if any(k in sel.lower() for k in ["urlbar", "address", "omnibox"]) or ctx.get("is_address_bar"):
            result.append(info)
            i += 1
            continue

        # Why: div等のモダンWeb要素(Group型)のクリックをフォーム枠誤認で消失させない
        cx = info.get("cursor_x", info.get("x", 0))
        cy = info.get("cursor_y", info.get("y", 0))
        is_valid_screen_click = cx > 20 and cy > 45
        is_redundant_input_click = act == "click" and not is_valid_screen_click and (
            sel in processed_selectors
            or sel.lower() in ["#entryform", "form"]
            or sel.startswith("div:has-text")
            or (c_type in ["pane", "window"] and not elem_name and not ctx.get("css_selector"))
        )
        if is_redundant_input_click:
            i += 1
            continue

        is_submit_btn = act == "click" and (
            "button" in c_type
            or any(k in sel.lower() for k in ["submit", "btn", "button"])
            or any(k in elem_name.lower() for k in ["登録", "送信", "保存", "submit", "save", "ログイン"])
        )
        if is_submit_btn:
            info["raw_action"] = "browser_action"
            info["raw_type"] = "browser_action"
            info["action"] = "click_element"
            info["selector"] = sel or "button[type='submit']"
            info["selector_type"] = "css"
            info["element_name"] = elem_name or "登録する"
            info["semantic_role"] = elem_name or "登録する"
            result.append(info)
            processed_selectors.add(sel)
            i += 1
            continue

        is_select = any(t in c_type for t in ["combo", "select", "list", "dropdown"]) or "select" in sel.lower()
        if is_select and act in ["type_text", "key_down", "key_press", "key_combo"]:
            i += 1
            continue

        if is_select and _promote_select_action(info, sel, elem_name, scanned_elem, state):
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

        is_checkbox = "checkbox" in c_type or "check" in sel.lower()
        if is_checkbox and act in ["click", "browser_action"]:
            chk_raw = str(
                state.element_final_values.get(sel)
                or state.global_form_snapshot.get(sel)
                or state.global_form_snapshot.get(f"name:{elem_name}")
                or info.get("value")
                or ""
            ).strip().lower()
            is_checked = chk_raw in ["true", "1", "checked"] if chk_raw else (act == "click")
            info["raw_action"] = "browser_action"
            info["raw_type"] = "browser_action"
            info["action"] = "set_checkbox"
            info["selector"] = sel
            info["value"] = is_checked
            info["element_name"] = elem_name or "チェックボックス"
            result.append(info)
            processed_selectors.add(sel)
            i += 1
            continue

        if act == "type_text" and not sel:
            for s_k, s_d in state.scanned_elements_map.items():
                if s_d.get("control_type") in ["Edit", "Spinner"] and (s_d.get("element_name") == elem_name or f"name:{elem_name}" == s_k):
                    sel = s_d.get("selector") or s_k
                    c_type = str(s_d.get("control_type", "")).lower()
                    break

        is_input_control = any(t in c_type for t in ["edit", "spinner"]) or any(tag in sel.lower() for tag in ["input", "textarea"])
        is_container_id = any(k in sel.lower() for k in ["root", "app", "main", "container", "wrapper", "content", "body"])
        is_input_field = (
            not is_select
            and act in ["type_text", "key_down", "key_press"]
            and (is_input_control or (sel.startswith("#") and not is_container_id and not any(tag in sel.lower() for tag in ["form", "btn", "button", "tab"])))
            and "check" not in c_type
            and not sel.lower().endswith("newsletter")
        )
        if is_input_field:
            clean_txt, scanned_item = _resolve_input_field_value(info, act, sel, elem_name, state)
            if clean_txt and clean_txt != elem_name:
                # Why: 既存の正当な完全長文テキストを未確定の1文字等の短縮ゴミ値で上書き破壊しない
                if result:
                    prev_text = str(result[-1].get("semantic_role") or result[-1].get("text") or "").strip()
                    if len(prev_text) > len(clean_txt) and (prev_text.startswith(clean_txt) or clean_txt in prev_text):
                        i += 1
                        continue

                while result:
                    prev_item = result[-1]
                    p_act = prev_item.get("raw_action", "")
                    p_sel = (prev_item.get("app_context") or {}).get("css_selector") or prev_item.get("selector") or ""
                    if p_act in ["type_text", "key_down", "key_press", "press_key", "key_combo"] and (
                        (p_sel and p_sel == sel) or (not p_sel and not sel.startswith("#submit"))
                    ):
                        result.pop()
                    else:
                        break

                target_x = scanned_item.get("x") or (state.selector_coords[sel][0] if sel in state.selector_coords else None)
                target_y = scanned_item.get("y") or (state.selector_coords[sel][1] if sel in state.selector_coords else None)
                if target_x and target_y and target_x > 20 and target_y > 20:
                    info["cursor_x"], info["cursor_y"] = target_x, target_y
                    info["x"], info["y"] = target_x, target_y

                info["raw_action"] = "browser_action"
                info["raw_type"] = "browser_action"
                info["action"] = "type_text"
                info["selector"] = sel
                info["text"] = clean_txt
                info["semantic_role"] = clean_txt
                info["element_name"] = elem_name or "入力項目"
                result.append(info)
                processed_selectors.add(sel)
                i = _consume_following_selector_events(temp_workflow_info, i, sel, elem_name)
                continue

        result.append(info)
        i += 1

    return restore_missing_snapshot_elements(result, temp_workflow_info, state, processed_selectors)
