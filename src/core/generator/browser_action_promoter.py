"""Module: @role: クリック要素コンテキストの入力イベント継承、ブラウザアクション昇格、および昇格後キーノイズ除去を担当する。"""

from typing import Any, Dict, List, Optional

from core.shared.app_log_profile import AppProfile, detect_app_profile, has_explicit_app_identity
from core.shared.os_env import is_link_or_url, normalize_text_width
from core.shared.window_utils import is_browser_window


def inherit_click_context_before_form(temp_workflow_info: List[Dict[str, Any]]) -> None:
    # Why: 同一アプリ種別内のみクリックコンテキストを継承し、ブラウザからOffice/Desktopへのセレクタ漏洩を完全防止
    last_clk_ctx: Dict[str, Any] = {}
    last_clk_win = ""
    last_clk_prof: Optional[AppProfile] = None
    for info in temp_workflow_info:
        act = info.get("raw_action", "")
        w_name = info.get("window_name", "")
        has_id = has_explicit_app_identity(info)
        cur_prof = detect_app_profile(info) if has_id else last_clk_prof
        c = info.get("app_context") or {}
        elem = c.get("element_name") or ""
        sel = c.get("css_selector") or ""

        if has_id and last_clk_prof is not None and cur_prof != last_clk_prof:
            last_clk_ctx = {}
            last_clk_win = ""
            last_clk_prof = cur_prof

        if act == "click":
            if sel or elem or c.get("xpath"):
                last_clk_ctx = c.copy()
                last_clk_win = w_name
                last_clk_prof = cur_prof
        elif act in ["type_text", "key_combo", "key_down", "key_press"] and last_clk_ctx:
            is_win_match = (
                (w_name == last_clk_win)
                or (not last_clk_win and cur_prof == last_clk_prof)
                or (not w_name and cur_prof == last_clk_prof)
                or (is_browser_window(w_name) and is_browser_window(last_clk_win))
            )
            if is_win_match:
                i_ctx = info.setdefault("app_context", {})
                allow_dom_sel = cur_prof == AppProfile.BROWSER
                keys_to_copy = ["css_selector", "xpath", "element_name", "control_type"] if allow_dom_sel else ["element_name", "control_type"]
                if not i_ctx.get("css_selector") and not i_ctx.get("xpath"):
                    for k in keys_to_copy:
                        if last_clk_ctx.get(k):
                            i_ctx[k] = last_clk_ctx[k]
                elif i_ctx.get("css_selector") == last_clk_ctx.get("css_selector"):
                    if not i_ctx.get("element_name") and last_clk_ctx.get("element_name"):
                        i_ctx["element_name"] = last_clk_ctx["element_name"]
                    if not i_ctx.get("control_type") and last_clk_ctx.get("control_type"):
                        i_ctx["control_type"] = last_clk_ctx["control_type"]


def bind_click_context_to_inputs(temp_workflow_info: List[Dict[str, Any]]) -> None:
    # Why: 直前クリック先要素のセレクタ・コンテキストを同一アプリ種別の入力イベントへ安全にバインド
    last_browser_click_ctx: Dict[str, Any] = {}
    last_browser_win = ""
    last_prof: Optional[AppProfile] = None
    for info in temp_workflow_info:
        act = info.get("raw_action", "")
        w_name = info.get("window_name", "")
        has_id = has_explicit_app_identity(info)
        cur_prof = detect_app_profile(info) if has_id else last_prof
        if has_id and last_prof is not None and cur_prof != last_prof:
            last_browser_click_ctx = {}
            last_browser_win = ""
            last_prof = cur_prof
        if act == "click":
            ctx = info.get("app_context") or {}
            c_type = str(ctx.get("control_type", "")).lower()
            if "combobox" not in c_type and (ctx.get("css_selector") or ctx.get("xpath") or ctx.get("element_name")):
                last_browser_click_ctx = ctx.copy()
                last_browser_win = w_name
                last_prof = cur_prof
        elif act in ["type_text", "key_combo"] and last_browser_click_ctx:
            if w_name == last_browser_win and cur_prof == last_prof:
                if not info.get("app_context"):
                    info["app_context"] = last_browser_click_ctx.copy()
                else:
                    info_ctx = info["app_context"]
                    if not info_ctx.get("css_selector") and not info_ctx.get("xpath"):
                        for k, v in last_browser_click_ctx.items():
                            if not info_ctx.get(k):
                                info_ctx[k] = v


def promote_browser_actions(temp_workflow_info: List[Dict[str, Any]], strategy: str) -> List[Dict[str, Any]]:
    # Why: ブラウザ内のリンククリック、要素操作、URL入力を専用ブラウザアクションへ網羅昇格
    promoted_browser_info: List[Dict[str, Any]] = []
    for info in temp_workflow_info:
        act = info.get("raw_action", "")
        app_ctx = info.get("app_context") or {}
        is_browser = detect_app_profile(info) == AppProfile.BROWSER

        # Why: ビジュアル戦略時はブラウザ昇格を抑止、hybridは確実なセレクタ中心、semanticは積極昇格
        if is_browser and strategy != "visual":
            if act == "type_text":
                role_text = str(info.get("semantic_role", "")).strip()
                css_sel = str(app_ctx.get("css_selector") or "")
                xpath_sel = str(app_ctx.get("xpath") or "")
                elem_l = str(app_ctx.get("element_name") or "").lower()
                is_addr = bool(
                    app_ctx.get("is_address_bar")
                    or any(k in css_sel.lower() for k in ("urlbar", "omnibox", "addressbar"))
                    or any(k in elem_l for k in ("アドレスバー", "address bar", "address and search bar", "url bar"))
                )
                # Why: 通常フォーム欄へのURL文字列入力をopen_urlへ誤昇格させないようアドレスバー限定
                if is_addr and (
                    is_link_or_url(role_text)
                    or any(ext in role_text.lower() for ext in [".jp", ".com", ".net", ".org", "http"])
                ):
                    norm_url = normalize_text_width(role_text)
                    if not norm_url.startswith(("http://", "https://")):
                        norm_url = f"https://{norm_url}"
                    info["raw_action"] = "browser_action"
                    info["raw_type"] = "browser_action"
                    info["action"] = "open_url"
                    info["url"] = norm_url
                    info["semantic_role"] = norm_url
                elif css_sel or xpath_sel:
                    info["raw_action"] = "browser_action"
                    info["raw_type"] = "browser_action"
                    info["action"] = "type_text"
                    info["selector"] = css_sel or xpath_sel
                    info["selector_type"] = "css" if css_sel else "xpath"
                    info["text"] = role_text
            elif act == "click":
                target_url = app_ctx.get("url") or app_ctx.get("value") or app_ctx.get("text")
                target_url = target_url.strip() if target_url and isinstance(target_url, str) else ""

                has_http = target_url.startswith(("http://", "https://"))
                css_sel = info.get("selector") or app_ctx.get("css_selector")
                xpath_sel = app_ctx.get("xpath")
                cur_sel = css_sel or xpath_sel
                has_selector = bool(cur_sel)
                elem_name = info.get("element_name") or app_ctx.get("element_name") or info.get("semantic_role") or ""

                # Why: move(単なるホバー)は除外し、hybridでは明示セレクタのないURL直接置換を抑制
                should_promote = has_selector or (has_http and strategy == "semantic")
                if should_promote:
                    info["raw_action"] = "browser_action"
                    info["raw_type"] = "browser_action"
                    if has_selector:
                        info["action"] = "click_element"
                        info["selector"] = cur_sel
                        info["selector_type"] = info.get("selector_type") or ("css" if css_sel else "xpath")
                    else:
                        info["action"] = "open_url"
                    if has_http:
                        info["url"] = target_url
                    if elem_name and elem_name != "left_click":
                        info["element_name"] = elem_name
                        info["semantic_role"] = elem_name

        promoted_browser_info.append(info)

    return promoted_browser_info


def clean_post_promotion_keys(promoted_browser_info: List[Dict[str, Any]]) -> List[Dict[str, Any]]:
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
                if (
                    p_role in ["enter", "return"]
                    and prev_role in ["enter", "return"]
                    and prev_act in ["key_down", "key_press", "press_key"]
                ):
                    continue
                if prev_act in ["browser_action", "type_text"]:
                    if p_role == "tab" or (
                        p_info.get("ime_active")
                        and p_idx < m_len - 1
                        and promoted_browser_info[p_idx + 1].get("raw_action") in ["type_text", "key_press"]
                    ):
                        continue
        cleaned_after_promoted.append(p_info)

    return cleaned_after_promoted
