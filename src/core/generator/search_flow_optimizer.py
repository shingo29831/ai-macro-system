"""Module: @role: タイピング前フォーカス確保および入力後の補完・サジェスト手ブレノイズ除去を担当する。"""

import logging
from typing import Any, Dict, List

logger = logging.getLogger(__name__)


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
                logger.info(
                    f"Promoted pre-typing move to click for focus at ({curr.get('cursor_x')}, {curr.get('cursor_y')})"
                )

        # 2. タイピング直前の非入力UI(ブラウザロゴ等)クリックを除去しフォーカス奪取を防止
        if act == "click":
            ctx = curr.get("app_context") or {}
            sel = str(ctx.get("css_selector", "")).lower()
            is_browser = any(
                b in curr.get("window_name", "").lower() for b in ["firefox", "chrome", "edge", "brave", "opera"]
            )
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
                is_completion_key = n_act in ["key_down", "key_press", "press_key"] and n_role in [
                    "tab",
                    "down",
                    "up",
                    "right",
                    "left",
                ]
                is_intervening_click = n_act == "click"
                is_intervening_move = n_act == "move"

                if is_completion_key or is_intervening_click or is_intervening_move:
                    noise_indices.add(k)
                    k += 1
                else:
                    break

            if found_target_event and noise_indices:
                logger.info(
                    f"Cleaned {len(noise_indices)} completion/suggest noise events between typing and search execution"
                )
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
