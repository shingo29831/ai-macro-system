"""Module: @role: イベント列から脱落したスナップショット確定要素の復元、物理Y座標順への整流化、および送信ボタン末尾配置を担当する。"""

from typing import Any, Dict, List, Set, Tuple

from core.generator.form_snapshot_collector import FormSnapshotContext

_INTERNAL_UI_KEYWORDS = [
    "contentselectdropdown", "select-popup", "datalist", "popup", "moz-", "chrome://",
    "urlbar", "addressbar", "omnibox", "identity-box", "tracking-protection",
]


def _resolve_snapshot_coords(
    sel_id: str,
    val_str: str,
    clean_name: str,
    temp_workflow_info: List[Dict[str, Any]],
    state: FormSnapshotContext,
) -> Tuple[int, int]:
    scanned_elem = state.scanned_elements_map.get(sel_id) or state.scanned_elements_map.get(f"name:{clean_name}") or {}
    # Why: 網羅的スキャンで特定された物理座標を最優先で復元
    sc_x = scanned_elem.get("x", 0)
    sc_y = scanned_elem.get("y", 0)
    coords = (sc_x, sc_y) if (sc_x > 20 and sc_y > 20) else state.selector_coords.get(sel_id)

    # Why: セレクタ名に依存せず値がコミットされた実イベントから座標を動的逆引き
    if not coords or coords[0] <= 20 or coords[1] <= 20:
        for info in temp_workflow_info:
            i_ctx = info.get("app_context") or {}
            i_snap = i_ctx.get("form_snapshot") or {}
            i_comm = i_ctx.get("committed_values") or {}
            if i_snap.get(sel_id) == val_str or i_comm.get(sel_id) == val_str:
                cx = info.get("cursor_x", info.get("x", 0))
                cy = info.get("cursor_y", info.get("y", 0))
                if cx > 20 and cy > 20:
                    return (cx, cy)

    return coords or (0, 0)


def restore_missing_snapshot_elements(
    result: List[Dict[str, Any]],
    temp_workflow_info: List[Dict[str, Any]],
    state: FormSnapshotContext,
    processed_selectors: Set[str],
) -> List[Dict[str, Any]]:
    # Why: フォーム末尾のイベントを参照して下部座標と正しい送信コンテキストを継承
    ref_evt = temp_workflow_info[-1] if temp_workflow_info else {}
    ref_eid = ref_evt.get("event_id", "evt_auto")
    ref_win = ref_evt.get("window_name", "")

    snapshot_items: List[Tuple[str, str]] = []
    for k, v in state.global_form_snapshot.items():
        if not k.startswith("#") or k in processed_selectors:
            continue
        if any(w in k.lower() for w in _INTERNAL_UI_KEYWORDS):
            continue
        v_str = str(v).strip()
        if not v_str:
            continue
        # Why: 近傍にカーソル移動があったチェックボックスは明示的な操作対象として復元
        scanned_elem = state.scanned_elements_map.get(k) or {}
        sc_x = scanned_elem.get("x", 0)
        sc_y = scanned_elem.get("y", 0)
        has_interaction = any(
            (e.get("app_context") or {}).get("css_selector") == k
            or e.get("selector") == k
            or (
                sc_x > 20 and sc_y > 20
                and abs(e.get("cursor_x", e.get("x", 0)) - sc_x) <= 45
                and abs(e.get("cursor_y", e.get("y", 0)) - sc_y) <= 45
            )
            for e in temp_workflow_info
        )
        if not has_interaction and v_str.lower() in ["false", ""]:
            continue
        snapshot_items.append((k, v_str))

    insert_pos = len(result)
    for r_idx, r_item in enumerate(result):
        if r_item.get("action") == "click_element" and any(
            b in str(r_item.get("selector", "")).lower() for b in ["submit", "btn", "button"]
        ):
            insert_pos = r_idx
            break

    win_x, win_y = ref_evt.get("win_x", 0), ref_evt.get("win_y", 0)
    win_w, win_h = ref_evt.get("win_w", 0), ref_evt.get("win_h", 0)

    for sel_id, val_str in snapshot_items:
        clean_name = state.selector_names.get(sel_id, sel_id.lstrip("#"))
        coords = _resolve_snapshot_coords(sel_id, val_str, clean_name, temp_workflow_info, state)
        scanned_elem = state.scanned_elements_map.get(sel_id) or state.scanned_elements_map.get(f"name:{clean_name}") or {}
        scanned_type = str(scanned_elem.get("control_type", "")).lower()

        # Why: ボタン要素はselect_optionへ誤変換せずクリックアクションへ委譲
        if "button" in scanned_type or "btn" in sel_id.lower():
            continue

        is_chk = "check" in scanned_type or val_str.lower() in ["true", "false"]
        is_combo = any(t in scanned_type for t in ["combo", "select", "list", "dropdown"])
        eid = f"{ref_eid}_{sel_id.lstrip('#')}"

        if is_chk:
            chk_x = coords[0] if coords[0] > 20 else ref_evt.get("cursor_x", ref_evt.get("x", 0))
            chk_y = coords[1] if coords[1] > 20 else ref_evt.get("cursor_y", ref_evt.get("y", 0))
            new_item = {
                "raw_action": "browser_action",
                "raw_type": "browser_action",
                "action": "set_checkbox",
                "selector": sel_id,
                "selector_type": "css",
                "value": val_str.lower() == "true",
                "text": str(val_str.lower() == "true"),
                "element_name": clean_name or sel_id.lstrip("#"),
                "semantic_role": clean_name or sel_id.lstrip("#"),
                "window_name": ref_win,
                "win_x": win_x, "win_y": win_y, "win_w": win_w, "win_h": win_h,
                "event_id": eid,
                "fallback_events": [eid, ref_eid],
                "cursor_x": chk_x, "cursor_y": chk_y, "x": chk_x, "y": chk_y,
            }
        elif is_combo:
            new_item = {
                "raw_action": "browser_action",
                "raw_type": "browser_action",
                "action": "select_option",
                "selector": sel_id,
                "selector_type": "css",
                "value": val_str,
                "text": val_str,
                "element_name": clean_name or "選択項目",
                "semantic_role": val_str,
                "window_name": ref_win,
                "win_x": win_x, "win_y": win_y, "win_w": win_w, "win_h": win_h,
                "event_id": eid,
                "fallback_events": [eid, ref_eid],
                "cursor_x": coords[0], "cursor_y": coords[1], "x": coords[0], "y": coords[1],
            }
        else:
            new_item = {
                "raw_action": "browser_action",
                "raw_type": "browser_action",
                "action": "type_text",
                "selector": sel_id,
                "selector_type": "css",
                "text": val_str,
                "semantic_role": val_str,
                "element_name": clean_name or "入力項目",
                "window_name": ref_win,
                "win_x": win_x, "win_y": win_y, "win_w": win_w, "win_h": win_h,
                "event_id": eid,
                "fallback_events": [eid, ref_eid],
                "cursor_x": coords[0], "cursor_y": coords[1], "x": coords[0], "y": coords[1],
            }

        result.insert(insert_pos, new_item)
        insert_pos += 1
        processed_selectors.add(sel_id)

    return finalize_form_order(result, temp_workflow_info, state)


def finalize_form_order(
    result: List[Dict[str, Any]],
    temp_workflow_info: List[Dict[str, Any]],
    state: FormSnapshotContext,
) -> List[Dict[str, Any]]:
    ref_evt = temp_workflow_info[-1] if temp_workflow_info else {}
    ref_eid = ref_evt.get("event_id", "evt_auto")
    ref_win = ref_evt.get("window_name", "")

    form_inputs: List[Dict[str, Any]] = []
    submit_buttons: List[Dict[str, Any]] = []
    other_actions: List[Dict[str, Any]] = []

    for item in result:
        act = item.get("action")
        sel = str(item.get("selector", "")).lower()
        if act == "click_element" and any(b in sel for b in ["submit", "btn", "button"]):
            submit_buttons.append(item)
        elif item.get("raw_action") == "browser_action" and act in ["type_text", "select_option", "set_checkbox"]:
            form_inputs.append(item)
        else:
            other_actions.append(item)

    has_submit_btn = any(
        r.get("action") == "click_element"
        and any(k in str(r.get("selector", "")).lower() for k in ["submit", "btn"])
        for r in result
    )
    if not has_submit_btn:
        for s_key, s_data in state.scanned_elements_map.items():
            s_sel = str(s_data.get("selector", "")).lower()
            s_name = str(s_data.get("element_name", "")).lower()
            c_type = str(s_data.get("control_type", "")).lower()
            is_real_submit = (
                ("submit" in s_sel or "submit" in s_key.lower() or any(w in s_name for w in ["登録", "送信", "保存", "submit"]))
                and "button" in c_type
            )
            if is_real_submit:
                btn_x = s_data.get("x", 0)
                btn_y = s_data.get("y", 0)
                btn_name = s_data.get("element_name") or "登録する"
                btn_eid = f"{ref_eid}_submit_btn"
                submit_buttons.append({
                    "raw_action": "browser_action",
                    "raw_type": "browser_action",
                    "action": "click_element",
                    "selector": s_data.get("selector") or "#submit-btn",
                    "selector_type": "css",
                    "element_name": btn_name,
                    "semantic_role": btn_name,
                    "text": btn_name,
                    "window_name": ref_win,
                    "win_x": ref_evt.get("win_x", 0),
                    "win_y": ref_evt.get("win_y", 0),
                    "win_w": ref_evt.get("win_w", 0),
                    "win_h": ref_evt.get("win_h", 0),
                    "event_id": btn_eid,
                    "fallback_events": [btn_eid, ref_eid],
                    "cursor_x": btn_x,
                    "cursor_y": btn_y,
                    "x": btn_x,
                    "y": btn_y,
                })
                break

    if form_inputs:
        # Why: 辞書検索index()を完全排除しタプルインデックスで安全かつ安定に整流化
        indexed_inputs = list(enumerate(form_inputs))
        indexed_inputs.sort(key=lambda pair: (
            pair[1].get("cursor_y", pair[1].get("y", 0))
            if pair[1].get("cursor_y", pair[1].get("y", 0)) > 45
            else (10000 + pair[0])
        ))
        form_inputs = [item for _, item in indexed_inputs]

    # Why: form_inputsの有無に関わらず末尾送信ボタンを常に結合保証
    return other_actions + form_inputs + submit_buttons
