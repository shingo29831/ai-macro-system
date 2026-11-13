"""Module: @role: クリック・スクロール順序の整流化、重複移動削除、および連続スクロール集約を担当する。"""

import logging
from typing import Any, Dict, List

from core.generator.hover_navigator import is_residual_hover

logger = logging.getLogger(__name__)


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
            while (
                j < n
                and temp_workflow_info[j].get("raw_action") == "scroll"
                and temp_workflow_info[j].get("window_name", "") == m_win
            ):
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


def _filter_trivial_moves(temp_workflow_info: List[Dict[str, Any]]) -> List[Dict[str, Any]]:
    n = len(temp_workflow_info)
    filtered: List[Dict[str, Any]] = []
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

            if next_act == "scroll":
                prev_act = filtered[-1].get("raw_action", "") if filtered else ""
                diff_val = curr.get("diff_val", 0.0)
                ctx = curr.get("app_context") or {}
                has_url = bool(str(ctx.get("url") or ctx.get("text") or ctx.get("value") or "").startswith("http"))
                has_ui_context = bool(ctx.get("element_name") or ctx.get("css_selector") or has_url)
                is_meaningful = (
                    curr.get("is_nav_hover")
                    or has_url
                    or diff_val >= 0.005
                    or (has_ui_context and prev_act != "scroll")
                )
                if not is_meaningful:
                    i += 1
                    continue

            if next_act == "click" and next_info:
                if curr.get("is_nav_hover"):
                    filtered.append(curr)
                    i += 1
                    continue

                if next_info.get("match_event_id") and next_info.get("match_event_id") == curr.get("event_id"):
                    filtered.append(curr)
                    i += 1
                    continue

                cx = next_info.get("cursor_x", next_info.get("x", 0))
                cy = next_info.get("cursor_y", next_info.get("y", 0))
                mx, my = curr.get("cursor_x", curr.get("x", 0)), curr.get("cursor_y", curr.get("y", 0))
                dist = ((cx - mx) ** 2 + (cy - my) ** 2) ** 0.5

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
    return filtered


def _dedupe_move_groups(filtered: List[Dict[str, Any]]) -> List[Dict[str, Any]]:
    deduped: List[Dict[str, Any]] = []
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
                cx = next_click.get("cursor_x", next_click.get("x", 0))
                cy = next_click.get("cursor_y", next_click.get("y", 0))
                c_elem = str((next_click.get("app_context") or {}).get("element_name", "")).strip()

                # Why: クリック前の一連の移動からメニュー親要素(起点ホバー)のみを最大1つ厳選し過渡移動を全消去
                best_hover = None
                for mv in move_group:
                    mx, my = mv.get("cursor_x", mv.get("x", 0)), mv.get("cursor_y", mv.get("y", 0))
                    if ((mx - cx) ** 2 + (my - cy) ** 2) ** 0.5 <= 35:
                        continue

                    m_ctx = mv.get("app_context") or {}
                    m_elem = str(m_ctx.get("element_name") or mv.get("element_name") or "").strip()
                    m_sel = str(m_ctx.get("css_selector") or "").lower()
                    diff_val = mv.get("diff_val", 0.0)

                    # Why: 要素名なしでも画面変化(Diff>=3%)を伴うメニュー展開ホバーを確実に保持
                    is_menu_parent = (
                        mv.get("is_nav_hover")
                        or any(k in m_sel for k in ["nav", "menu", "drop", "header"])
                        or (bool(m_elem) and m_elem != c_elem and m_elem not in ["move", "left_click"])
                        or diff_val >= 0.03
                    )
                    if is_menu_parent:
                        best_hover = mv
                        break

                if best_hover:
                    deduped.append(best_hover)
            else:
                meaningful_navs = [
                    mv
                    for mv in move_group
                    if str((mv.get("app_context") or {}).get("url") or "").startswith("http") or mv.get("is_nav_hover")
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
                        m_has_ui = bool(
                            m_ctx.get("element_name") or m_ctx.get("css_selector") or m_cand.get("element_name")
                        )
                        if has_win_transition or m_diff >= 0.02 or m_has_ui:
                            deduped.append(m_cand)
                            break

            i = j
            continue

        deduped.append(curr)
        i += 1
    return deduped


def _merge_consecutive_scrolls(deduped: List[Dict[str, Any]]) -> List[Dict[str, Any]]:
    result: List[Dict[str, Any]] = []
    k = len(deduped)
    i = 0
    while i < k:
        curr = deduped[i]
        act = curr.get("raw_action", "")

        if act == "scroll":
            tot_dx = curr.get("dx", 0.0)
            tot_dy = curr.get("dy", 0.0)
            curr_win = curr.get("window_name", "")
            last_eid = curr.get("event_id")
            last_ts = curr.get("timestamp", 0)
            evts = list(curr.get("fallback_events", [curr.get("event_id")]))

            j = i + 1
            while j < k:
                nxt = deduped[j]
                if nxt.get("raw_action", "") == "scroll":
                    n_win = nxt.get("window_name", "")
                    if curr_win and n_win and curr_win != n_win:
                        break
                    nxt_ts = nxt.get("timestamp", 0)
                    ts_gap = abs(nxt_ts - last_ts) if (nxt_ts and last_ts) else 0
                    # Why: スクロール操作中のカーソル移動に惑わされず起点位置でセッションを完全統合
                    if ts_gap > 800:
                        break

                    # Why: 行き過ぎ戻し等の逆方向スクロールも合算し正味移動量を完全集約
                    tot_dx += nxt.get("dx", 0.0)
                    tot_dy += nxt.get("dy", 0.0)
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


def cleanup_redundant_moves_and_scrolls(temp_workflow_info: List[Dict[str, Any]]) -> List[Dict[str, Any]]:
    # Why: スクロール合間の無駄な移動を除去してスクロールを集約しつつ、メニュー出現用ホバーを確実に保持
    if not temp_workflow_info:
        return temp_workflow_info

    filtered = _filter_trivial_moves(temp_workflow_info)
    deduped = _dedupe_move_groups(filtered)
    return _merge_consecutive_scrolls(deduped)
