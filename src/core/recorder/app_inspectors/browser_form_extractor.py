"""Module: @role: ブラウザフォーム要素の値抽出・スナップショット収集・直前入力要素トラッキングおよびセレクタ推定を担当する。"""

import logging
import math
import urllib.parse
from typing import Any, Dict, List, Optional, Tuple

logger = logging.getLogger(__name__)

_TAG_MAP = {
    "button": "button",
    "edit": "input",
    "hyperlink": "a",
    "combobox": "select",
    "checkbox": "input[type='checkbox']",
}
_INTERNAL_BROWSER_IDS = ("urlbar", "address", "search", "tab", "identity", "tracking")
_DOCUMENT_CLASSES = ("MozillaContent", "Chrome_Render", "Internet Explorer_Server")
_SNAPSHOT_TARGET_TYPES = ("EditControl", "ComboBoxControl", "CheckBoxControl", "SpinnerControl", "ButtonControl")


def extract_value_from_uia(ctrl: Any) -> str:
    """uiautomationコントロールから現在入力値・選択値・チェック状態を抽出する。"""
    if not ctrl:
        return ""
    c_type = getattr(ctrl, "ControlTypeName", "") or ""

    if "CheckBox" in c_type:
        try:
            return "true" if ctrl.GetTogglePattern().ToggleState == 1 else "false"
        except Exception:
            try:
                return "true" if (ctrl.GetLegacyIAccessiblePattern().CurrentState & 0x10) else "false"
            except Exception:
                return "false"

    if "ComboBox" in c_type:
        combo_val = _extract_combobox_value(ctrl)
        if combo_val:
            return combo_val

    for getter in (
        lambda: ctrl.GetValuePattern().Value,
        lambda: ctrl.GetLegacyIAccessiblePattern().CurrentValue,
    ):
        try:
            val = getter()
            if val is not None and str(val).strip():
                return str(val).strip()
        except Exception:
            pass

    try:
        r_val = ctrl.GetRangeValuePattern().Value
        if r_val is not None:
            f_val = float(r_val)
            # Why: 未入力数値欄が返すNaN(非数)を除外し不正文字列化を防止
            if not math.isnan(f_val):
                return str(int(f_val) if f_val.is_integer() else f_val).strip()
    except Exception:
        pass

    # Why: Edit/Spinnerではラベル名(Name)を入力値として偽装返却せず空値を保証
    if any(t in c_type for t in ("Edit", "Spinner")):
        return ""

    name = getattr(ctrl, "Name", "") or ""
    return str(name or "").strip()


def _extract_combobox_value(ctrl: Any) -> str:
    try:
        sel_pat = ctrl.GetSelectionPattern()
        if sel_pat:
            sel_items = sel_pat.GetSelection()
            if sel_items:
                s_name = getattr(sel_items[0], "Name", "")
                if s_name and str(s_name).strip():
                    return str(s_name).strip()
    except Exception:
        pass

    for getter in (
        lambda: ctrl.GetLegacyIAccessiblePattern().CurrentValue,
        lambda: ctrl.GetValuePattern().Value,
    ):
        try:
            val = getter()
            if val and str(val).strip():
                return str(val).strip()
        except Exception:
            pass

    try:
        for child in ctrl.GetChildren():
            try:
                if child.GetSelectionItemPattern().IsSelected:
                    return str(child.Name or "").strip()
            except Exception:
                pass
    except Exception:
        pass
    return ""


def extract_value_from_elem(elem: Any) -> str:
    """pywinauto要素からValuePattern/LegacyIAccessible等を順次試行して値を抽出する。"""
    # Why: ブラウザごとにValuePattern/LegacyIAccessibleの対応が分かれるため順次試行
    try:
        if elem.is_value_pattern_available():
            val = elem.get_value()
            if val and str(val).strip():
                return str(val).strip()
    except Exception:
        pass

    try:
        legacy_val = elem.legacy_properties().get("Value", "")
        if legacy_val and str(legacy_val).strip():
            return str(legacy_val).strip()
    except Exception:
        pass

    try:
        # Why: 数値入力欄(input type=number)やSpinnerから確定数値を確実に抽出
        if hasattr(elem, "is_range_value_pattern_available") and elem.is_range_value_pattern_available():
            r_val = elem.get_range_value()
            if r_val is not None and str(r_val).strip():
                return str(r_val).strip()
    except Exception:
        pass

    try:
        # Why: チェックボックスのON/OFFトグル状態を確実に抽出
        ctrl_type = getattr(elem.element_info, "control_type", "") or ""
        if "check" in ctrl_type.lower():
            if hasattr(elem, "is_toggle_pattern_available") and elem.is_toggle_pattern_available():
                return "true" if elem.get_toggle_state() == 1 else "false"
            leg_state = elem.legacy_properties().get("State", 0)
            if isinstance(leg_state, int) and (leg_state & 0x10):
                return "true"
            return "false"
    except Exception:
        pass

    try:
        ctrl_type = getattr(elem.element_info, "control_type", "") or ""
        name = elem.window_text() or ""
        if "edit" in ctrl_type.lower() or "document" in ctrl_type.lower():
            if name and name.strip() and name not in ("検索", "Search", "クリア", "×"):
                return name.strip()
    except Exception:
        pass

    return ""


def infer_element_selectors(
    primary_elem: Any,
    element_name: str,
    extracted_text: str,
) -> Tuple[Optional[str], Optional[str]]:
    """pywinauto要素のAutomationId・クラス・表示テキスト・URLからCSSセレクタとXPathを推定する。"""
    auto_id = getattr(primary_elem.element_info, "automation_id", "") or ""
    class_name = getattr(primary_elem.element_info, "class_name", "") or ""
    ctrl_type = (getattr(primary_elem.element_info, "control_type", "") or "").lower()
    tag = _TAG_MAP.get(ctrl_type, "div")

    if auto_id:
        return f"#{auto_id}", f"//*[@id='{auto_id}']"
    if class_name and not class_name.startswith("Chrome_"):
        first_class = class_name.split()[0]
        return f"{tag}.{first_class}", f"//{tag}[contains(@class, '{first_class}')]"
    if element_name and not element_name.startswith("http"):
        # Why: 表示テキストが存在する場合はURLではなく有為なテキストでセレクタ生成
        clean_elem_text = element_name.strip().replace("'", "\\'")[:30]
        return f"{tag}:has-text('{clean_elem_text}')", f"//{tag}[contains(text(), '{clean_elem_text}')]"
    if extracted_text and extracted_text.startswith(("http://", "https://")):
        # Why: URLの場合はhas-textではなくhref属性一致セレクタを安全に生成
        try:
            parsed_u = urllib.parse.urlsplit(extracted_text)
            u_path = parsed_u.path.rstrip("/")
            target_part = u_path if u_path else parsed_u.netloc
            return f"{tag}[href*='{target_part}']", f"//{tag}[contains(@href, '{target_part}')]"
        except Exception:
            return tag, f"//{tag}"
    if extracted_text:
        clean_ext = extracted_text.strip().replace("'", "\\'")[:25]
        return f"{tag}:has-text('{clean_ext}')", f"//{tag}[contains(text(), '{clean_ext}')]"
    return tag, f"//{tag}"


class BrowserFormTracker:
    """記録セッション中のフォーム入力キャッシュ・直前入力要素コミット・ページ全域スナップショットを管理する。"""

    def __init__(self) -> None:
        self.last_input_element: Any = None
        self.last_input_selector: Optional[str] = None
        self.last_input_name: Optional[str] = None
        self.last_input_hwnd: Optional[int] = None
        self.form_values_cache: Dict[str, str] = {}

    def reset(self) -> None:
        """記録セッション間での直前セレクタ・値キャッシュの混入を防止するため状態をリセットする。"""
        self.last_input_element = None
        self.last_input_selector = None
        self.last_input_name = None
        self.last_input_hwnd = None
        self.form_values_cache.clear()

    def record_input_element(
        self,
        element: Any,
        selector: Optional[str],
        element_name: Optional[str],
        hwnd: Optional[int],
    ) -> None:
        self.last_input_element = element
        self.last_input_selector = selector
        self.last_input_name = element_name
        self.last_input_hwnd = hwnd

    def get_fallback_for_hwnd(self, cur_hwnd: Optional[int]) -> Optional[Tuple[str, str]]:
        # Why: 別コントロールの残留セレクタ誤適用を防ぎ同一HWNDの直前要素のみ厳密継承
        if cur_hwnd and self.last_input_hwnd == cur_hwnd and self.last_input_selector:
            if self.last_input_selector in self.form_values_cache:
                return self.last_input_selector, self.form_values_cache[self.last_input_selector]
        return None

    def commit_previous_element(self) -> Optional[Dict[str, str]]:
        """フォーカス離脱(blur)時に直前入力要素の最新確定値を再取得してコミットする。"""
        if not self.last_input_element and not self.last_input_selector:
            return None
        try:
            val = extract_value_from_elem(self.last_input_element) if self.last_input_element else ""
            sel = self.last_input_selector
            name = self.last_input_name
            if not val and self.last_input_hwnd and sel:
                try:
                    import uiautomation as auto

                    auto_id = sel.lstrip("#")
                    win_ctrl = auto.ControlFromHandle(self.last_input_hwnd)
                    if win_ctrl and win_ctrl.Exists(0, 0):
                        ctrl = win_ctrl.Control(AutomationId=auto_id)
                        if ctrl and ctrl.Exists(0, 0):
                            val = extract_value_from_uia(ctrl)
                except Exception:
                    pass
            if val and str(val).strip() and str(val).strip() != name:
                committed_val = str(val).strip()
                if sel:
                    self.form_values_cache[sel] = committed_val
                if name:
                    self.form_values_cache[f"name:{name}"] = committed_val
                return {"selector": sel or "", "element_name": name or "", "value": committed_val}
        except Exception as e:
            logger.debug(f"[BrowserFormTracker] commit_previous_element error: {e}")
        return None

    def extract_form_snapshot(self, window_info: Dict[str, Any]) -> Dict[str, Any]:
        """ページ全域を走査し各フォーム要素のセレクタ・確定値・物理座標を一括抽出する。"""
        snapshot: Dict[str, Any] = dict(self.form_values_cache)
        hwnd = window_info.get("hwnd") or window_info.get("handle")
        if not hwnd:
            return snapshot
        try:
            import uiautomation as auto

            win_ctrl = auto.ControlFromHandle(int(hwnd))
            if not win_ctrl or not win_ctrl.Exists(0, 0):
                return snapshot

            # Why: ブラウザのアドレスバー・タブを除外しWebドキュメント領域へ探索を限定
            doc_ctrl = None
            for c in win_ctrl.GetChildren():
                c_name = getattr(c, "ControlTypeName", "") or ""
                c_class = getattr(c, "ClassName", "") or ""
                if c_name == "DocumentControl" or any(cn in c_class for cn in _DOCUMENT_CLASSES):
                    doc_ctrl = c
                    break

            scan_root = doc_ctrl if doc_ctrl and doc_ctrl.Exists(0, 0) else win_ctrl
            elements_detail: List[Dict[str, Any]] = []

            for ctrl, _depth in auto.WalkControl(scan_root, maxDepth=16):
                c_type = getattr(ctrl, "ControlTypeName", "") or ""
                if c_type not in _SNAPSHOT_TARGET_TYPES:
                    continue

                aid = getattr(ctrl, "AutomationId", "") or ""
                name = getattr(ctrl, "Name", "") or ""
                # Why: ブラウザ自身のUIコントロールを完全に除外
                if any(w in aid.lower() for w in _INTERNAL_BROWSER_IDS) or any(
                    w in name.lower() for w in ("アドレス", "タブ", "閉じる")
                ):
                    continue

                rect = ctrl.BoundingRectangle
                cx = (rect.left + rect.right) // 2 if rect else 0
                cy = (rect.top + rect.bottom) // 2 if rect else 0
                # Why: 画面上部ツールバー内の誤検出を座標で安全に遮断
                if cy < 110:
                    continue

                val = extract_value_from_uia(ctrl)
                sel = f"#{aid}" if aid else ""
                clean_name = name.strip()

                elements_detail.append({
                    "selector": sel,
                    "automation_id": aid,
                    "element_name": clean_name,
                    "control_type": c_type.replace("Control", ""),
                    "value": val,
                    "x": cx,
                    "y": cy,
                    "rect": [rect.left, rect.top, rect.right, rect.bottom] if rect else [0, 0, 0, 0],
                })

                # Why: ボタンやNaN値は入力キャッシュに混入させず純粋な入力値のみ保持
                if val and str(val).strip() and str(val).strip().lower() != "nan" and c_type != "ButtonControl":
                    v_str = str(val).strip()
                    if sel:
                        snapshot[sel] = v_str
                        self.form_values_cache[sel] = v_str
                    if clean_name:
                        snapshot[f"name:{clean_name}"] = v_str
                        self.form_values_cache[f"name:{clean_name}"] = v_str

            if elements_detail:
                snapshot["__elements__"] = elements_detail

        except Exception as e:
            logger.debug(f"[BrowserFormTracker] extract_form_snapshot error: {e}")
        return snapshot
