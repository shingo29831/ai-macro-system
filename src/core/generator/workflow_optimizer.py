"""Module: @role: 統合されたイベントリストに対し、OSシェル操作のカット、ウィンドウ座標正規化、ナビゲーション最適化、およびブラウザ昇格を統合制御するオーケストレーター。"""

import logging
import re
import time
from typing import Any, Callable, Dict, List, Optional, Tuple

from core.executor.os_env_controller import is_link_or_url, normalize_text_width
from core.generator.form_optimizer import (
    consolidate_web_form_interactions as _consolidate_web_form_interactions,
    is_system_ui_event as _is_system_ui_event,
)
from core.generator.loop_detector import detect_and_optimize_loops
from core.generator.navigation_optimizer import (
    cleanup_redundant_moves_and_scrolls as _cleanup_redundant_moves_and_scrolls,
    optimize_typing_and_search_flow as _optimize_typing_and_search_flow,
    promote_navigation_hover_to_click as _promote_navigation_hover_to_click,
    reorder_displaced_clicks_before_scroll as _reorder_displaced_clicks_before_scroll,
)
from core.generator.office_optimizer import (
    clean_office_events,
    filter_web_excel_conflicts,
    propagate_excel_cells,
)

logger = logging.getLogger(__name__)


def _is_business_action(evt: Dict[str, Any]) -> bool:
    act = evt.get("raw_action", "")
    role = str(evt.get("semantic_role", "")).lower()
    if act in ["click", "type_text", "excel_action", "browser_action"]:
        return True
    if "key" in act and role not in ["win", "cmd", "windows"]:
        return True
    return False


def _extract_app(title: str) -> str:
    if not title:
        return ""
    parts = re.split(r"[\-—–―]", title)
    return parts[-1].strip().lower()


def optimize_workflow_events(
    temp_workflow_info: List[Dict[str, Any]],
    workflow_id: str,
    check_cancel_callback: Optional[Callable[[], bool]] = None,
    config: Optional[Any] = None,
    strategy: str = "hybrid",
) -> Tuple[List[Dict[str, Any]], Dict[str, str]]:
    last_cancel_check_time = time.time()

    def should_cancel() -> bool:
        nonlocal last_cancel_check_time
        current_time = time.time()
        if current_time - last_cancel_check_time > 0.1:
            last_cancel_check_time = current_time
            if check_cancel_callback and check_cancel_callback():
                return True
        return False

    temp_workflow_info = clean_office_events(temp_workflow_info, should_cancel)

    # OSシェル操作・起動前ウィンドウノイズのカット
    shell_cut_info: List[Dict[str, Any]] = []
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

    # 起動直後のウィンドウ移動・リサイズ操作を除去し最終調整後座標へ昇格
    app_first_business_op: Dict[str, int] = {}
    app_final_rect: Dict[str, Tuple[int, int, int, int]] = {}

    for i, info in enumerate(temp_workflow_info):
        app = _extract_app(info.get("window_name", ""))
        if not app:
            continue
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

    layout_cleaned: List[Dict[str, Any]] = []
    for info in temp_workflow_info:
        app = _extract_app(info.get("window_name", ""))
        orig_wx = info.get("win_x", 0)
        orig_wy = info.get("win_y", 0)
        raw_type = str(info.get("raw_type", "")).lower()
        raw_act = str(info.get("raw_action", "")).lower()
        cy = info.get("cursor_y", info.get("y", 0))
        cx = info.get("cursor_x", info.get("x", 0))

        # Why: 業務ドラッグやメニュー・タブ操作を保護し、完全な画面外座標のみ除外
        if cy < -50 or cx < -50:
            logger.info(f"[{workflow_id}] Omitted off-screen action (Event: {info.get('event_id')}) at ({cx}, {cy})")
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
    last_clk_ctx: Dict[str, Any] = {}
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

    # 端部ノイズ除去
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
            prev_events_win = [e.get("window_name", "") for e in temp_workflow_info[:-1] if e.get("window_name")]
            is_new_win = bool(prev_events_win and last_evt.get("window_name") and last_evt.get("window_name") != prev_events_win[0])
            if diff < 0.005 and not is_new_win and not last_evt.get("app_context", {}).get("element_name"):
                temp_workflow_info.pop()
            else:
                break
        else:
            break

    # ループパターンの解析・最適化
    temp_workflow_info = detect_and_optimize_loops(temp_workflow_info, workflow_id, should_cancel)

    # Excelセル情報の後続伝播および競合除去
    temp_workflow_info = propagate_excel_cells(temp_workflow_info)
    temp_workflow_info = filter_web_excel_conflicts(temp_workflow_info)

    # Why: 直前クリック先要素のセレクタ・コンテキストを入力イベント(type_text)へ安全にバインド
    last_browser_click_ctx: Dict[str, Any] = {}
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
    promoted_browser_info: List[Dict[str, Any]] = []
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
    cleaned_after_promoted: List[Dict[str, Any]] = []
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
                    if p_role == "tab" or (p_info.get("ime_active") and p_idx < m_len - 1 and promoted_browser_info[p_idx + 1].get("raw_action") in ["type_text", "key_press"]):
                        continue
        cleaned_after_promoted.append(p_info)

    # Why: 意図しない自動変数置換を完全廃止しユーザー入力の確定値をそのまま出力
    return cleaned_after_promoted, {}
