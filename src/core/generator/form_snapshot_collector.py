"""Module: @role: 記録イベント列からWebフォーム確定値スナップショット・物理座標・スキャン要素情報および入力値候補を集約する。"""

from dataclasses import dataclass, field
from typing import Any, Dict, List, Optional, Tuple

from core.generator.form_value_evaluator import evaluate_form_value


@dataclass
class FormSnapshotContext:
    """Webフォーム集約処理で共有するスナップショット・座標・候補値の集約コンテキスト"""

    initial_form_values: Dict[str, str] = field(default_factory=dict)
    global_form_snapshot: Dict[str, str] = field(default_factory=dict)
    selector_coords: Dict[str, Tuple[int, int]] = field(default_factory=dict)
    selector_names: Dict[str, str] = field(default_factory=dict)
    scanned_elements_map: Dict[str, Dict[str, Any]] = field(default_factory=dict)
    scanned_elements_values: Dict[str, str] = field(default_factory=dict)
    selector_candidates: Dict[str, List[str]] = field(default_factory=dict)
    element_final_values: Dict[str, str] = field(default_factory=dict)


def _should_skip_snapshot_update(
    prev_val: Optional[str],
    new_val: str,
    initial_val: Optional[str],
    post_submit_reset_detected: bool,
) -> bool:
    # Why: 初回値からの変更値を優先し末尾リセット空値による上書きを普遍防止
    if prev_val and prev_val != new_val and new_val in ["", "None"]:
        return True
    # Why: フォーム送信後のリセットで初期値に戻った場合の上書きを完全防止
    if prev_val and prev_val != initial_val and (new_val == initial_val or post_submit_reset_detected):
        return True
    # Why: 確定済みの完全長文値を入力途中の短縮プレフィックス値で上書き巻き戻ししない
    if prev_val and new_val and len(new_val) < len(prev_val) and (prev_val.startswith(new_val) or new_val in prev_val):
        return True
    return False


def _ingest_scanned_elements(
    elements_list: List[Dict[str, Any]],
    ctx_state: FormSnapshotContext,
    post_submit_reset_detected: bool,
) -> bool:
    empty_count = sum(
        1 for el in elements_list
        if str(el.get("value", "")).strip() == ""
        and str(el.get("control_type", "")).lower() in ["edit", "spinner"]
    )
    total_inputs = sum(
        1 for el in elements_list
        if str(el.get("control_type", "")).lower() in ["edit", "spinner"]
    )
    if total_inputs >= 2 and empty_count == total_inputs and len(ctx_state.global_form_snapshot) >= 2:
        post_submit_reset_detected = True

    for el in elements_list:
        e_sel = el.get("selector") or (f"#{el.get('automation_id')}" if el.get("automation_id") else "")
        e_name = el.get("element_name") or ""
        e_x, e_y = el.get("x", 0), el.get("y", 0)
        e_val = str(el.get("value", "")).strip()
        if e_sel:
            if e_sel not in ctx_state.scanned_elements_map or not ctx_state.scanned_elements_map[e_sel].get("control_type"):
                ctx_state.scanned_elements_map[e_sel] = el
            if e_x > 20 and e_y > 20:
                ctx_state.selector_coords[e_sel] = (e_x, e_y)
            if e_val and not post_submit_reset_detected:
                ctx_state.scanned_elements_values[e_sel] = e_val
        if e_name:
            if f"name:{e_name}" not in ctx_state.scanned_elements_map:
                ctx_state.scanned_elements_map[f"name:{e_name}"] = el
            if e_x > 20 and e_y > 20:
                ctx_state.selector_coords[f"name:{e_name}"] = (e_x, e_y)
                ctx_state.selector_coords[e_name] = (e_x, e_y)
            if e_val and not post_submit_reset_detected:
                ctx_state.scanned_elements_values[f"name:{e_name}"] = e_val

    return post_submit_reset_detected


def collect_form_snapshot_context(
    temp_workflow_info: List[Dict[str, Any]],
    config: Optional[Any] = None,
) -> FormSnapshotContext:
    state = FormSnapshotContext()
    post_submit_reset_detected = False

    for info in temp_workflow_info:
        ctx = info.get("app_context") or {}
        sel = ctx.get("css_selector") or info.get("selector") or ""
        ename = str(ctx.get("element_name") or info.get("element_name") or "").strip()
        cx = info.get("cursor_x", info.get("x", 0))
        cy = info.get("cursor_y", info.get("y", 0))

        if sel and cx > 20 and cy > 20:
            state.selector_coords[sel] = (cx, cy)
        if ename and cx > 20 and cy > 20:
            state.selector_coords[f"name:{ename}"] = (cx, cy)
            state.selector_coords[ename] = (cx, cy)
        if sel and ename:
            state.selector_names[sel] = ename

        for snap_src in [ctx.get("form_snapshot"), ctx.get("committed_values")]:
            if isinstance(snap_src, dict):
                if "__elements__" in snap_src and isinstance(snap_src["__elements__"], list):
                    # Why: __elements__詳細スキャンから真の要素物理座標と型を直接統合
                    post_submit_reset_detected = _ingest_scanned_elements(
                        snap_src["__elements__"], state, post_submit_reset_detected
                    )
                for sk, sv in snap_src.items():
                    if sk == "__elements__" or sv is None or not str(sv).strip():
                        continue
                    val_str = str(sv).strip()
                    if sk not in state.initial_form_values:
                        state.initial_form_values[sk] = val_str
                    prev_val = state.global_form_snapshot.get(sk)
                    if not _should_skip_snapshot_update(
                        prev_val, val_str, state.initial_form_values.get(sk), post_submit_reset_detected
                    ):
                        state.global_form_snapshot[sk] = val_str

        prev_c = ctx.get("committed_previous_value")
        if isinstance(prev_c, dict):
            ps = prev_c.get("selector")
            pv = prev_c.get("value")
            if ps and pv and str(pv).strip():
                pv_str = str(pv).strip()
                prev_val = state.global_form_snapshot.get(ps)
                if not _should_skip_snapshot_update(
                    prev_val, pv_str, state.initial_form_values.get(ps), post_submit_reset_detected
                ):
                    state.global_form_snapshot[ps] = pv_str

    _collect_and_evaluate_candidates(temp_workflow_info, state, config)
    return state


def _collect_and_evaluate_candidates(
    temp_workflow_info: List[Dict[str, Any]],
    state: FormSnapshotContext,
    config: Optional[Any] = None,
) -> None:
    for info in temp_workflow_info:
        ctx = info.get("app_context") or {}
        sel = ctx.get("css_selector") or info.get("selector") or ""
        elem_name = str(ctx.get("element_name") or info.get("element_name") or "").strip()
        keys = [k for k in [sel, f"name:{elem_name}" if elem_name else ""] if k]

        vals: List[str] = []
        if info.get("raw_action") == "type_text":
            t = str(info.get("semantic_role") or info.get("text") or "").strip()
            if t and t.lower() not in ["enter", "tab", "esc", "backspace", "delete"]:
                vals.append(t)
        for v in [ctx.get("value"), ctx.get("text"), info.get("value"), info.get("text")]:
            if v and isinstance(v, str) and str(v).strip():
                vals.append(str(v).strip())

        for k in keys:
            state.selector_candidates.setdefault(k, [])
            for val in vals:
                if val not in state.selector_candidates[k]:
                    state.selector_candidates[k].append(val)

    for k, cand_list in state.selector_candidates.items():
        clean_name = k.replace("name:", "") if k.startswith("name:") else ""
        snap_val = (
            state.global_form_snapshot.get(k)
            or state.global_form_snapshot.get(clean_name)
            or state.global_form_snapshot.get(f"name:{clean_name}")
        )
        chosen = evaluate_form_value(cand_list, clean_name, has_snapshot_val=snap_val, config=config)
        if chosen:
            state.element_final_values[k] = chosen
