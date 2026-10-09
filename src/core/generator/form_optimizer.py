"""Module: @role: Webフォーム入力項目（テキスト、セレクトボックス、チェックボックス、送信ボタン）の集約と最適値評価を担当する。"""

import logging
import re
from typing import Any, Dict, List, Optional, Tuple

logger = logging.getLogger(__name__)


def is_system_ui_event(evt: Dict[str, Any]) -> bool:
    ctx = evt.get("app_context") or {}
    elem_name = str(ctx.get("element_name", "")).lower()
    elem_text = str(ctx.get("text", "")).lower()
    role = str(evt.get("semantic_role", "")).lower()
    css = str(ctx.get("css_selector", "")).lower()
    c_type = str(ctx.get("control_type", "")).lower()
    system_terms = [
        "記録を終了", "記録中", "停止中", "実行中", "ai macro system", "マクロ生成中",
        "qapplication.qwidget", "windows 入力エクスペリエンス", "textinputhost",
        "corewindow", "windows.ui.core"
    ]
    return any(term in elem_name or term in elem_text or term in role or term in css or term in c_type for term in system_terms)


def select_candidate_with_llm(candidates: List[str], elem_name: str, config: Optional[Any] = None) -> Optional[str]:
    # Why: 小型モデル(Gemma 2B)でも崩れない番号選択プロンプトで曖昧な候補値を高精度判定
    if not config or len(candidates) < 2:
        return None
    try:
        from engines.llm.client import LLMClient
        client = LLMClient(host=config.llm_host, port=int(config.llm_port), timeout=3, max_retries=1)
        options_text = "\n".join(f"{idx + 1}: {c}" for idx, c in enumerate(candidates))
        prompt = (
            f"You are a form field input analyzer.\n"
            f"Target input field: \"{elem_name}\"\n"
            f"Candidates:\n{options_text}\n\n"
            f"Select the single most complete, definitive value intended for submission.\n"
            f"Respond ONLY with the choice number (e.g., 1 or 2)."
        )
        res = client.generate(prompt)
        if res.get("success") and "response" in res:
            content = res["response"]["choices"][0]["message"]["content"].strip()
            match = re.search(r'\b([1-9])\b', content)
            if match:
                selected_idx = int(match.group(1)) - 1
                if 0 <= selected_idx < len(candidates):
                    logger.info(f"LLM resolved field '{elem_name}' value to candidate [{selected_idx + 1}]: {candidates[selected_idx]}")
                    return candidates[selected_idx].strip()
    except Exception as e:
        logger.debug(f"LLM candidate evaluation skipped/failed: {e}")
    return None


def evaluate_form_value(candidates: List[str], elem_name: str, has_snapshot_val: Optional[str] = None, config: Optional[Any] = None) -> str:
    llm_val = select_candidate_with_llm(candidates, elem_name, config)
    if llm_val:
        return llm_val
    # Why: ブラウザ確定スナップショット値を絶対最優先し普遍判定で最適値を決定
    if has_snapshot_val and str(has_snapshot_val).strip():
        s_val = str(has_snapshot_val).strip()
        # Why: 候補に確定値の前方一致完全長文(例: 佐藤 -> 佐藤 健一)が存在する場合は完全版を採用
        for c in reversed(candidates):
            c_str = str(c).strip()
            if len(c_str) > len(s_val) and (c_str.startswith(s_val) or s_val in c_str):
                return c_str
        return s_val
    if not candidates:
        return ""

    def score_val(val: str, idx: int) -> float:
        if not val or not isinstance(val, str):
            return -1000.0
        v = val.strip()
        if not v or v.lower() == elem_name.lower() or v in ["検索", "Search", "クリア", "×", "left_click", "move"]:
            return -500.0
        # Why: 未確定ローマ字の末尾子音(kaisy, satou等)を大幅減点し確定値を救済
        is_raw_romaji = bool(re.fullmatch(r'[a-z]+', v.lower()) and any(v.lower().endswith(c) for c in ["sy", "ty", "ky", "sh", "ch", "ou", "ei"]))
        if is_raw_romaji:
            return -100.0 + min(len(v), 10)
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


def consolidate_web_form_interactions(temp_workflow_info: List[Dict[str, Any]], config: Optional[Any] = None) -> List[Dict[str, Any]]:
    # Why: ハードコードを完全排除しコントロール種別と最新確定値に基づく普遍的なフォーム最適化
    if not temp_workflow_info:
        return temp_workflow_info

    # 1. フォーム確定値スナップショットおよび有効物理座標を全イベントから包括集約
    initial_form_values: Dict[str, str] = {}
    global_form_snapshot: Dict[str, str] = {}
    selector_coords: Dict[str, Tuple[int, int]] = {}
    selector_names: Dict[str, str] = {}
    scanned_elements_map: Dict[str, Dict[str, Any]] = {}
    scanned_elements_values: Dict[str, str] = {}
    selector_candidates: Dict[str, List[str]] = {}
    post_submit_reset_detected = False

    for info in temp_workflow_info:
        ctx = info.get("app_context") or {}
        sel = ctx.get("css_selector") or info.get("selector") or ""
        ename = str(ctx.get("element_name") or info.get("element_name") or "").strip()
        cx = info.get("cursor_x", info.get("x", 0))
        cy = info.get("cursor_y", info.get("y", 0))

        if sel and cx > 20 and cy > 20:
            selector_coords[sel] = (cx, cy)
        if ename and cx > 20 and cy > 20:
            selector_coords[f"name:{ename}"] = (cx, cy)
            selector_coords[ename] = (cx, cy)
        if sel and ename:
            selector_names[sel] = ename

        for snap_src in [ctx.get("form_snapshot"), ctx.get("committed_values")]:
            if isinstance(snap_src, dict):
                # Why: __elements__詳細スキャンから真の要素物理座標と型を直接統合
                if "__elements__" in snap_src and isinstance(snap_src["__elements__"], list):
                    elements_list = snap_src["__elements__"]
                    empty_count = sum(1 for el in elements_list if str(el.get("value", "")).strip() == "" and str(el.get("control_type", "")).lower() in ["edit", "spinner"])
                    total_inputs = sum(1 for el in elements_list if str(el.get("control_type", "")).lower() in ["edit", "spinner"])
                    if total_inputs >= 2 and empty_count == total_inputs and len(global_form_snapshot) >= 2:
                        post_submit_reset_detected = True

                    for el in elements_list:
                        e_sel = el.get("selector") or (f"#{el.get('automation_id')}" if el.get("automation_id") else "")
                        e_name = el.get("element_name") or ""
                        e_x, e_y = el.get("x", 0), el.get("y", 0)
                        e_val = str(el.get("value", "")).strip()
                        if e_sel:
                            if e_sel not in scanned_elements_map or not scanned_elements_map[e_sel].get("control_type"):
                                scanned_elements_map[e_sel] = el
                            if e_x > 20 and e_y > 20:
                                selector_coords[e_sel] = (e_x, e_y)
                            if e_val and not post_submit_reset_detected:
                                scanned_elements_values[e_sel] = e_val
                        if e_name:
                            if f"name:{e_name}" not in scanned_elements_map:
                                scanned_elements_map[f"name:{e_name}"] = el
                            if e_x > 20 and e_y > 20:
                                selector_coords[f"name:{e_name}"] = (e_x, e_y)
                                selector_coords[e_name] = (e_x, e_y)
                            if e_val and not post_submit_reset_detected:
                                scanned_elements_values[f"name:{e_name}"] = e_val

                for sk, sv in snap_src.items():
                    if sk == "__elements__":
                        continue
                    if sv is not None and str(sv).strip():
                        val_str = str(sv).strip()
                        if sk not in initial_form_values:
                            initial_form_values[sk] = val_str

                        prev_val = global_form_snapshot.get(sk)
                        # Why: 初回値からの変更値を優先し末尾リセット空値による上書きを普遍防止
                        if prev_val and prev_val != val_str and val_str in ["", "None"]:
                            continue
                        # Why: フォーム送信後のリセットで初期値に戻った場合の上書きを完全防止
                        if prev_val and prev_val != initial_form_values.get(sk) and (val_str == initial_form_values.get(sk) or post_submit_reset_detected):
                            continue
                        # Why: 確定済みの完全長文値を入力途中の短縮プレフィックス値で上書き巻き戻ししない
                        if prev_val and val_str and len(val_str) < len(prev_val) and (prev_val.startswith(val_str) or val_str in prev_val):
                            continue
                        global_form_snapshot[sk] = val_str
        prev_c = ctx.get("committed_previous_value")
        if isinstance(prev_c, dict):
            ps = prev_c.get("selector")
            pv = prev_c.get("value")
            if ps and pv and str(pv).strip():
                prev_val = global_form_snapshot.get(ps)
                pv_str = str(pv).strip()
                if not (prev_val and pv_str in ["", "None"]):
                    if not (prev_val and prev_val != initial_form_values.get(ps) and (pv_str == initial_form_values.get(ps) or post_submit_reset_detected)):
                        if not (prev_val and len(pv_str) < len(prev_val) and (prev_val.startswith(pv_str) or pv_str in prev_val)):
                            global_form_snapshot[ps] = pv_str

    # 2. 各要素セレクタごとに全履歴から確定値候補を収集
    selector_candidates = {}
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
        snap_val = global_form_snapshot.get(k) or global_form_snapshot.get(clean_name) or global_form_snapshot.get(f"name:{clean_name}")
        chosen = evaluate_form_value(cand_list, clean_name, has_snapshot_val=snap_val, config=config)
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

        # Why: ページスキャン情報(scanned_elements_map)から真の要素型と要素名を動的補正
        scanned_elem = scanned_elements_map.get(sel) or scanned_elements_map.get(f"name:{elem_name}") or {}
        scanned_type = str(scanned_elem.get("control_type", "")).lower()
        scanned_name = str(scanned_elem.get("element_name", "")).strip()

        if scanned_type:
            c_type = scanned_type
        if scanned_name and (not elem_name or elem_name in ["left_click", "move"]):
            elem_name = scanned_name
        elif not elem_name and sel in selector_names:
            elem_name = selector_names[sel]

        # Why: システムUI、IME候補窓、処理済みセレクタ、通過ホバー移動、および外枠・Document要素を除外
        if is_system_ui_event(info):
            i += 1
            continue
        if sel and sel in processed_selectors:
            i += 1
            continue
        # Why: UIA要素名欠落時も画面変化(Diff>=3%)を伴うメニュー展開ホバーを確実に保持
        if act == "move":
            m_ctx = info.get("app_context") or {}
            m_sel = str(m_ctx.get("css_selector") or "").lower()
            diff_val = info.get("diff_val", 0.0)
            is_container = any(k in m_sel for k in ["root", "app", "main", "window", "mozilla"])
            is_valid_menu_hover = (
                info.get("is_nav_hover") or 
                any(k in m_sel for k in ["nav", "menu", "drop", "header"]) or
                (diff_val >= 0.03 and not is_container) or
                (bool(elem_name) and elem_name not in ["move", "left_click"] and not is_container)
            )
            if is_valid_menu_hover:
                result.append(info)
            i += 1
            continue
        # Why: 背景Documentの空クリックのみ除外対象とし、リンクやURL・要素名を持つサイト遷移クリックを完全保護
        is_document_container = "document" in c_type or any(cls in sel for cls in ["MozillaWindowClass", "Chrome_WidgetWin", "CoreWindow"])
        if is_document_container:
            has_nav_target = (
                bool(str(ctx.get("url") or ctx.get("text") or ctx.get("value") or "").startswith("http")) or
                (bool(elem_name) and elem_name not in ["left_click", "move", "click"]) or
                (bool(sel) and not any(cls in sel for cls in ["MozillaWindowClass", "Chrome_WidgetWin", "CoreWindow"]))
            )
            if not has_nav_target or act != "click":
                i += 1
                continue

        # Why: アドレスバー・検索バー操作はWebフォーム集約から除外して時系列を完全保護
        is_nav_bar = any(k in sel.lower() for k in ["urlbar", "address", "omnibox"]) or ctx.get("is_address_bar")
        if is_nav_bar:
            result.append(info)
            i += 1
            continue

        # Why: div等のモダンWeb要素(Group型)のクリックをフォーム枠誤認で消失させない
        cx = info.get("cursor_x", info.get("x", 0))
        cy = info.get("cursor_y", info.get("y", 0))
        is_valid_screen_click = cx > 20 and cy > 45
        is_redundant_input_click = act == "click" and not is_valid_screen_click and (
            sel in processed_selectors or
            sel.lower() in ["#entryform", "form"] or
            sel.startswith("div:has-text") or
            (c_type in ["pane", "window"] and not elem_name and not ctx.get("css_selector"))
        )
        if is_redundant_input_click:
            i += 1
            continue

        # Why: 送信ボタン(Button, submit)のクリックを専用ブラウザアクションへ即時昇格
        is_submit_btn = act == "click" and (
            "button" in c_type or
            any(k in sel.lower() for k in ["submit", "btn", "button"]) or
            any(k in elem_name.lower() for k in ["登録", "送信", "保存", "submit", "save", "ログイン"])
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

        # Why: 汎用セレクトボックス/コンボボックスの集約
        is_select = any(t in c_type for t in ["combo", "select", "list", "dropdown"]) or "select" in sel.lower()

        # Why: ComboBox/ドロップダウンに対するキー入力イベントはノイズとしてスキップし真の選択操作へ委譲
        if is_select and act in ["type_text", "key_down", "key_press", "key_combo"]:
            i += 1
            continue

        if is_select:
            # Why: スナップショット確定値およびイベント自身の選択テキストを最優先
            snap_val = global_form_snapshot.get(sel) or global_form_snapshot.get(f"name:{elem_name}")
            cur_val = info.get("value") or info.get("text") or ctx.get("value") or ctx.get("text")
            opt_val = ""
            if snap_val and str(snap_val).strip() and str(snap_val).strip() not in ["left_click", "move", elem_name]:
                opt_val = str(snap_val).strip()
            elif cur_val and str(cur_val).strip() and str(cur_val).strip() not in ["left_click", "move", elem_name]:
                opt_val = str(cur_val).strip()
            else:
                opt_val = element_final_values.get(sel) or element_final_values.get(f"name:{elem_name}") or ""

            if str(opt_val).strip() and str(opt_val).strip() not in ["left_click", "move", elem_name]:
                # Why: 有効な物理座標を履歴から確実に復元
                sc_x = scanned_elem.get("x", 0)
                sc_y = scanned_elem.get("y", 0)
                if sc_x > 20 and sc_y > 20:
                    info["cursor_x"], info["cursor_y"] = sc_x, sc_y
                    info["x"], info["y"] = sc_x, sc_y
                elif sel in selector_coords:
                    cx, cy = selector_coords[sel]
                    info["cursor_x"], info["cursor_y"] = cx, cy
                    info["x"], info["y"] = cx, cy

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

        # Why: 汎用チェックボックスの集約 (True/False状態を動的判定)
        is_checkbox = "checkbox" in c_type or "check" in sel.lower()
        if is_checkbox and act in ["click", "browser_action"]:
            chk_raw = str(element_final_values.get(sel) or global_form_snapshot.get(sel) or global_form_snapshot.get(f"name:{elem_name}") or info.get("value") or "").strip().lower()
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

        # Why: 未紐付けタイピングイベントも直前クリックや名前からセレクタを逆引き
        if act == "type_text" and not sel:
            for s_k, s_d in scanned_elements_map.items():
                if s_d.get("control_type") in ["Edit", "Spinner"] and (s_d.get("element_name") == elem_name or f"name:{elem_name}" == s_k):
                    sel = s_d.get("selector") or s_k
                    c_type = str(s_d.get("control_type", "")).lower()
                    break

        # Why: マウス移動やルートコンテナ(#root等)が入力欄へ誤化合してタイピングを破壊するのを厳密遮断
        is_input_control = any(t in c_type for t in ["edit", "spinner"]) or any(tag in sel.lower() for tag in ["input", "textarea"])
        is_container_id = any(k in sel.lower() for k in ["root", "app", "main", "container", "wrapper", "content", "body"])
        is_input_field = (
            not is_select and
            act in ["type_text", "key_down", "key_press"] and
            (is_input_control or (sel.startswith("#") and not is_container_id and not any(tag in sel.lower() for tag in ["form", "btn", "button", "tab"])))
            and "check" not in c_type and not sel.lower().endswith("newsletter")
        )
        if is_input_field:
            # Why: 網羅的スキャン値および確定スナップショット値を最優先
            scanned_item = scanned_elements_map.get(sel) or scanned_elements_map.get(f"name:{elem_name}") or {}
            scanned_val = scanned_elements_values.get(sel) or scanned_elements_values.get(f"name:{elem_name}") or scanned_item.get("value")
            snap_val = global_form_snapshot.get(sel) or global_form_snapshot.get(f"name:{elem_name}")
            final_val = scanned_val or snap_val or element_final_values.get(sel)
            # Why: 確定値より長文の完全版候補(佐藤 -> 佐藤 健一)が存在する場合は完全版を採用
            if final_val:
                f_str = str(final_val).strip()
                for cand in reversed(selector_candidates.get(sel, [])):
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
                for cand in reversed(selector_candidates.get(sel, [])):
                    if cand and str(cand).strip().lower() not in ["nan", "none", elem_name.lower()]:
                        final_val = str(cand).strip()
                        break

            if final_val and str(final_val).strip() and str(final_val).strip() != elem_name:
                clean_txt = str(final_val).strip()

                # Why: 既存の正当な完全長文テキストを未確定の1文字等の短縮ゴミ値で上書き破壊しない
                if result:
                    prev_item = result[-1]
                    prev_text = str(prev_item.get("semantic_role") or prev_item.get("text") or "").strip()
                    if len(prev_text) > len(clean_txt) and (prev_text.startswith(clean_txt) or clean_txt in prev_text):
                        i += 1
                        continue

                while result:
                    prev_item = result[-1]
                    p_act = prev_item.get("raw_action", "")
                    p_sel = (prev_item.get("app_context") or {}).get("css_selector") or prev_item.get("selector") or ""
                    # Why: 同一セレクタまたは未特定タイピングの残骸を安全に除去
                    if p_act in ["type_text", "key_down", "key_press", "press_key", "key_combo"] and ((p_sel and p_sel == sel) or (not p_sel and not sel.startswith("#submit"))):
                        result.pop()
                    else:
                        break

                # Why: スキャンされた正確な物理座標を最優先で割り当て
                target_x = scanned_item.get("x") or (selector_coords.get(sel)[0] if sel in selector_coords else None)
                target_y = scanned_item.get("y") or (selector_coords.get(sel)[1] if sel in selector_coords else None)
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

    # 4. スナップショットに存在する確定値のうち、イベント列で脱落したフォーム要素を完全復元
    # Why: フォーム末尾のイベントを参照して下部座標と正しい送信コンテキストを継承
    ref_evt = temp_workflow_info[-1] if temp_workflow_info else {}
    ref_eid = ref_evt.get("event_id", "evt_auto")
    ref_win = ref_evt.get("window_name", "")

    # スナップショットキーのうち、ブラウザ内部UIを除外した真の未処理IDセレクタを抽出
    snapshot_items = []
    internal_ui_keywords = [
        "contentselectdropdown", "select-popup", "datalist", "popup", "moz-", "chrome://",
        "urlbar", "addressbar", "omnibox", "identity-box", "tracking-protection"
    ]
    for k, v in global_form_snapshot.items():
        if not k.startswith("#") or k in processed_selectors:
            continue
        if any(w in k.lower() for w in internal_ui_keywords):
            continue
        v_str = str(v).strip()
        if not v_str:
            continue
        # Why: 近傍にカーソル移動があったチェックボックスは明示的な操作対象として復元
        scanned_elem = scanned_elements_map.get(k) or {}
        sc_x = scanned_elem.get("x", 0)
        sc_y = scanned_elem.get("y", 0)
        has_interaction = any(
            (e.get("app_context") or {}).get("css_selector") == k or e.get("selector") == k or
            (sc_x > 20 and sc_y > 20 and abs(e.get("cursor_x", e.get("x", 0)) - sc_x) <= 45 and abs(e.get("cursor_y", e.get("y", 0)) - sc_y) <= 45)
            for e in temp_workflow_info
        )
        if not has_interaction and v_str.lower() in ["false", ""]:
            continue
        snapshot_items.append((k, v_str))

    # 送信ボタンより前に挿入するため、既存result内の送信ボタン位置を特定
    insert_pos = len(result)
    for r_idx, r_item in enumerate(result):
        if r_item.get("action") == "click_element" and any(b in str(r_item.get("selector", "")).lower() for b in ["submit", "btn", "button"]):
            insert_pos = r_idx
            break

    for sel_id, val_str in snapshot_items:
        clean_name = selector_names.get(sel_id, sel_id.lstrip("#"))
        scanned_elem = scanned_elements_map.get(sel_id) or scanned_elements_map.get(f"name:{clean_name}") or {}
        # Why: 網羅的スキャンで特定された物理座標を最優先で復元
        sc_x = scanned_elem.get("x", 0)
        sc_y = scanned_elem.get("y", 0)
        coords = (sc_x, sc_y) if (sc_x > 20 and sc_y > 20) else selector_coords.get(sel_id)

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
                        coords = (cx, cy)
                        break

        if not coords:
            coords = (0, 0)

        win_x = ref_evt.get("win_x", 0)
        win_y = ref_evt.get("win_y", 0)
        win_w = ref_evt.get("win_w", 0)
        win_h = ref_evt.get("win_h", 0)

        # Why: スキャンされた真の要素型に基づき他要素の型誤感染を完全排除
        scanned_elem = scanned_elements_map.get(sel_id) or scanned_elements_map.get(f"name:{clean_name}") or {}
        scanned_type = str(scanned_elem.get("control_type", "")).lower()

        # Why: ボタン要素はselect_optionへ誤変換せずクリックアクションへ委譲
        if "button" in scanned_type or "btn" in sel_id.lower():
            continue

        is_chk = "check" in scanned_type or val_str.lower() in ["true", "false"]
        is_combo = any(t in scanned_type for t in ["combo", "select", "list", "dropdown"])

        if is_chk:
            chk_eid = f"{ref_eid}_{sel_id.lstrip('#')}"
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
                "win_x": win_x,
                "win_y": win_y,
                "win_w": win_w,
                "win_h": win_h,
                "event_id": chk_eid,
                "fallback_events": [chk_eid, ref_eid],
                "cursor_x": chk_x,
                "cursor_y": chk_y,
                "x": chk_x,
                "y": chk_y
            }
            result.insert(insert_pos, new_item)
            insert_pos += 1
            processed_selectors.add(sel_id)
            continue

        if is_combo:
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
                "win_x": win_x,
                "win_y": win_y,
                "win_w": win_w,
                "win_h": win_h,
                "event_id": f"{ref_eid}_{sel_id.lstrip('#')}",
                "fallback_events": [f"{ref_eid}_{sel_id.lstrip('#')}", ref_eid],
                "cursor_x": coords[0],
                "cursor_y": coords[1],
                "x": coords[0],
                "y": coords[1]
            }
            result.insert(insert_pos, new_item)
            insert_pos += 1
            processed_selectors.add(sel_id)
            continue

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
            "win_x": win_x,
            "win_y": win_y,
            "win_w": win_w,
            "win_h": win_h,
            "event_id": f"{ref_eid}_{sel_id.lstrip('#')}",
            "fallback_events": [f"{ref_eid}_{sel_id.lstrip('#')}", ref_eid],
            "cursor_x": coords[0],
            "cursor_y": coords[1],
            "x": coords[0],
            "y": coords[1]
        }
        result.insert(insert_pos, new_item)
        insert_pos += 1
        processed_selectors.add(sel_id)

    # Why: フォーム各要素を物理y座標順に並び替え、送信ボタンを常に末尾に保証
    form_inputs = []
    submit_buttons = []
    other_actions = []
    for item in result:
        act = item.get("action")
        sel = str(item.get("selector", "")).lower()
        if act == "click_element" and any(b in sel for b in ["submit", "btn", "button"]):
            submit_buttons.append(item)
        elif item.get("raw_action") == "browser_action" and act in ["type_text", "select_option", "set_checkbox"]:
            form_inputs.append(item)
        else:
            other_actions.append(item)

    # Why: ページスキャン情報に存在する正規の送信ボタンのみをフォーム末尾に確実に配置
    has_submit_btn = any(
        r.get("action") == "click_element" and any(k in str(r.get("selector", "")).lower() for k in ["submit", "btn"])
        for r in result
    )
    if not has_submit_btn:
        for s_key, s_data in scanned_elements_map.items():
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
                    "y": btn_y
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
    result = other_actions + form_inputs + submit_buttons

    return result
