# @role: ブラウザ特有のUIA情報（URLバー、検索ボックス等）を取得し、主要ブラウザからのURL・検索クエリ抽出およびDOM探索を担当する実装
from typing import Any, Dict, Optional, List
import traceback
import logging
import urllib.parse
import ctypes
from .base_inspector import BaseInspector

logger = logging.getLogger(__name__)

class BrowserInspector(BaseInspector):
    _last_input_element = None
    _last_input_selector: Optional[str] = None
    _last_input_name: Optional[str] = None
    _last_input_hwnd: Optional[int] = None
    _form_values_cache: Dict[str, str] = {}

    @classmethod
    def _commit_previous_element(cls) -> Optional[Dict[str, str]]:
        # Why: フォーカス離脱(blur)時に直前入力要素の最新確定値を再取得してコミット
        if not cls._last_input_element and not cls._last_input_selector:
            return None
        try:
            val = ""
            elem = cls._last_input_element
            if elem:
                val = cls._extract_value_from_elem(elem)
            sel = cls._last_input_selector
            name = cls._last_input_name
            if not val and cls._last_input_hwnd and sel:
                try:
                    import uiautomation as auto
                    auto_id = sel.lstrip("#")
                    win_ctrl = auto.ControlFromHandle(cls._last_input_hwnd)
                    if win_ctrl and win_ctrl.Exists(0, 0):
                        ctrl = win_ctrl.Control(AutomationId=auto_id)
                        if ctrl and ctrl.Exists(0, 0):
                            val = cls._extract_value_from_uia(ctrl)
                except Exception:
                    pass
            if val and str(val).strip() and str(val).strip() != name:
                committed_val = str(val).strip()
                if sel: cls._form_values_cache[sel] = committed_val
                if name: cls._form_values_cache[f"name:{name}"] = committed_val
                return {"selector": sel or "", "element_name": name or "", "value": committed_val}
        except Exception as e:
            logger.debug(f"[BrowserInspector] _commit_previous_element error: {e}")
        return None

    @staticmethod
    def _extract_value_from_uia(ctrl) -> str:
        if not ctrl: return ""
        c_type = getattr(ctrl, "ControlTypeName", "") or ""
        
        # 1. チェックボックスのON/OFF状態判定
        if "CheckBox" in c_type:
            try:
                return "true" if ctrl.GetTogglePattern().ToggleState == 1 else "false"
            except Exception:
                try:
                    return "true" if (ctrl.GetLegacyIAccessiblePattern().CurrentState & 0x10) else "false"
                except Exception:
                    return "false"

        # 2. セレクトボックス(ComboBox)の現在選択値取得
        if "ComboBox" in c_type:
            try:
                sel_pat = ctrl.GetSelectionPattern()
                if sel_pat:
                    sel_items = sel_pat.GetSelection()
                    if sel_items and len(sel_items) > 0:
                        s_name = getattr(sel_items[0], "Name", "")
                        if s_name and str(s_name).strip():
                            return str(s_name).strip()
            except Exception:
                pass
            try:
                val = ctrl.GetLegacyIAccessiblePattern().CurrentValue
                if val and str(val).strip():
                    return str(val).strip()
            except Exception:
                pass
            try:
                val = ctrl.GetValuePattern().Value
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

        # 3. テキスト欄・数値欄(Edit/Spinner)の入力値取得
        try:
            val = ctrl.GetValuePattern().Value
            if val is not None and str(val).strip():
                return str(val).strip()
        except Exception:
            pass

        try:
            val = ctrl.GetLegacyIAccessiblePattern().CurrentValue
            if val is not None and str(val).strip():
                return str(val).strip()
        except Exception:
            pass

        try:
            r_val = ctrl.GetRangeValuePattern().Value
            if r_val is not None:
                return str(int(r_val) if float(r_val).is_integer() else r_val).strip()
        except Exception:
            pass

        name = getattr(ctrl, "Name", "") or ""
        return str(name or "").strip()

    @classmethod
    def extract_form_snapshot(cls, window_info: Dict[str, Any]) -> Dict[str, Any]:
        # Why: ページ全域を網羅的に走査し各フォーム要素のセレクタ・確定値・物理座標を一括抽出
        snapshot: Dict[str, Any] = dict(cls._form_values_cache)
        hwnd = window_info.get("hwnd") or window_info.get("handle")
        if not hwnd: return snapshot
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
                if c_name == "DocumentControl" or any(cls_name in c_class for cls_name in ["MozillaContent", "Chrome_Render", "Internet Explorer_Server"]):
                    doc_ctrl = c
                    break

            scan_root = doc_ctrl if doc_ctrl and doc_ctrl.Exists(0, 0) else win_ctrl
            elements_detail: List[Dict[str, Any]] = []
            internal_ids = ["urlbar", "address", "search", "tab", "identity", "tracking"]

            target_types = ["EditControl", "ComboBoxControl", "CheckBoxControl", "SpinnerControl", "ButtonControl"]
            for ctrl, depth in auto.WalkControl(scan_root, maxDepth=16):
                c_type = getattr(ctrl, "ControlTypeName", "") or ""
                if c_type not in target_types:
                    continue

                aid = getattr(ctrl, "AutomationId", "") or ""
                name = getattr(ctrl, "Name", "") or ""
                # Why: ブラウザ自身のUIコントロールを完全に除外
                if any(w in aid.lower() for w in internal_ids) or any(w in name.lower() for w in ["アドレス", "タブ", "閉じる"]):
                    continue

                rect = ctrl.BoundingRectangle
                cx = (rect.left + rect.right) // 2 if rect else 0
                cy = (rect.top + rect.bottom) // 2 if rect else 0
                # Why: 画面上部ツールバー内の誤検出を座標で安全に遮断
                if cy < 110:
                    continue

                val = cls._extract_value_from_uia(ctrl)
                sel = f"#{aid}" if aid else ""
                clean_name = name.strip()

                elem_data = {
                    "selector": sel,
                    "automation_id": aid,
                    "element_name": clean_name,
                    "control_type": c_type.replace("Control", ""),
                    "value": val,
                    "x": cx,
                    "y": cy,
                    "rect": [rect.left, rect.top, rect.right, rect.bottom] if rect else [0, 0, 0, 0]
                }
                elements_detail.append(elem_data)

                if val and str(val).strip():
                    v_str = str(val).strip()
                    if sel:
                        snapshot[sel] = v_str
                        cls._form_values_cache[sel] = v_str
                    if clean_name:
                        snapshot[f"name:{clean_name}"] = v_str
                        cls._form_values_cache[f"name:{clean_name}"] = v_str

            if elements_detail:
                snapshot["__elements__"] = elements_detail

        except Exception as e:
            logger.debug(f"[BrowserInspector] extract_form_snapshot error: {e}")
        return snapshot

    @staticmethod
    def _extract_value_from_elem(elem) -> str:
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
                if name and len(name.strip()) > 0 and name not in ["検索", "Search", "クリア", "×"]:
                    return name.strip()
        except Exception:
            pass

        return ""

    @staticmethod
    def parse_url_details(raw_url: str) -> Dict[str, Any]:
        """URL文字列からドメイン、パス、検索クエリ(q=等)を安全にデコード・パースする"""
        if not raw_url or not isinstance(raw_url, str):
            return {}
        text = raw_url.strip()
        if not text:
            return {}

        # Why: ブラウザUIで省略されるスキーム(https://)を補完して正規URL化
        candidate = text
        if not (candidate.startswith("http://") or candidate.startswith("https://") or candidate.startswith("about:") or candidate.startswith("chrome://") or candidate.startswith("edge://")):
            if "." in candidate.split("/")[0] or "?" in candidate:
                candidate = "https://" + candidate

        try:
            parsed = urllib.parse.urlsplit(candidate)
            if not parsed.netloc and parsed.scheme not in ["about", "chrome", "edge"]:
                return {}

            query_dict = urllib.parse.parse_qs(parsed.query)
            search_query = None
            for key in ["q", "query", "p", "wd", "word", "search_query", "text"]:
                if key in query_dict and query_dict[key]:
                    # Why: URLエンコード文字列(%E7%B9%94...)を日本語平文に確実に復元
                    search_query = urllib.parse.unquote_plus(query_dict[key][0])
                    break

            return {
                "raw_text": text,
                "full_url": candidate,
                "scheme": parsed.scheme,
                "domain": parsed.netloc,
                "path": parsed.path,
                "query": search_query,
                "params": {k: urllib.parse.unquote_plus(v[0]) if len(v) == 1 else [urllib.parse.unquote_plus(x) for x in v] for k, v in query_dict.items()}
            }
        except Exception:
            return {}

    def get_address_bar_info(self, window_info: Dict[str, Any], desktop=None) -> Dict[str, Any]:
        """UIAを用いて主要ブラウザのアドレスバーから確定URLおよび検索クエリを取得する"""
        result = {"url": "", "query": None, "url_details": {}, "address_bar_text": ""}
        try:
            import pywinauto
            if desktop is None:
                desktop = pywinauto.Desktop(backend="uia")

            # Why: window_info内のキー名差異(hwnd / handle)とフォアグラウンドHWNDをフォールバック解決
            hwnd = window_info.get("hwnd") or window_info.get("handle")
            if not hwnd:
                try:
                    hwnd = ctypes.windll.user32.GetForegroundWindow()
                except Exception:
                    hwnd = None

            window = None
            if hwnd:
                try:
                    window = desktop.window(handle=int(hwnd))
                except Exception as e:
                    logger.debug(f"[BrowserInspector] HWNDからのウィンドウ特定失敗: {e}")

            if not window:
                return result

            # Why: Firefox(urlbar-input)やChromiumの定数IDが存在する場合は即座に特定
            known_ids = ["urlbar-input", "address-edit-box", "view_1020"]
            for aid in known_ids:
                try:
                    edit = window.child_window(auto_id=aid, control_type="Edit")
                    if edit.exists(timeout=0.1):
                        val = self._extract_value_from_elem(edit)
                        if val:
                            result["address_bar_text"] = val
                            parsed = self.parse_url_details(val)
                            if parsed:
                                result["url"] = parsed.get("full_url", "")
                                result["query"] = parsed.get("query")
                                result["url_details"] = parsed
                                return result
                except Exception:
                    pass

            # Why: 全子孫走査によるUIフリーズを回避し、上部ツールバー内のEditのみを高速走査
            candidates = []
            try:
                top_edits = []
                for child in window.children():
                    c_type = getattr(child.element_info, "control_type", "")
                    if c_type in ["ToolBar", "Pane", "Custom"]:
                        top_edits.extend(child.children(control_type="Edit"))
                if not top_edits:
                    top_edits = window.children(control_type="Edit")
            except Exception:
                top_edits = []

            for edit in top_edits[:10]:
                try:
                    rect = edit.rectangle()
                    if rect.top > 250:
                        continue
                    auto_id = getattr(edit.element_info, "automation_id", "") or ""
                    name = edit.window_text() or ""
                    val = self._extract_value_from_elem(edit)

                    score = 0
                    if any(kw in auto_id.lower() for kw in ["url", "address", "omnibox"]):
                        score += 50
                    if any(kw in name.lower() for kw in ["アドレス", "address", "url", "検索"]):
                        score += 40
                    if val and any(ind in val for ind in ["http", "www.", ".com", ".jp"]):
                        score += 30
                    if val and score > 0:
                        candidates.append((score, val, rect))
                except Exception:
                    continue

            if candidates:
                candidates.sort(key=lambda x: x[0], reverse=True)
                best_val = candidates[0][1]
                result["address_bar_text"] = best_val
                result["address_bar_rect"] = {"left": candidates[0][2].left, "top": candidates[0][2].top, "right": candidates[0][2].right, "bottom": candidates[0][2].bottom}
                parsed = self.parse_url_details(best_val)
                if parsed:
                    result["url"] = parsed.get("full_url", "")
                    result["query"] = parsed.get("query")
                    result["url_details"] = parsed

        except Exception as e:
            logger.debug(f"[BrowserInspector] アドレスバー特定失敗: {e}")
        return result

    def inspect(self, window_info: Dict[str, Any], x: Optional[int] = None, y: Optional[int] = None) -> Dict[str, Any]:
        result = {
            "app": "Browser",
            "element_name": "",
            "control_type": "",
            "text": "",
            "value": "",
            "url": "",
            "query": None,
            "url_details": {},
            "debug_log": []
        }

        def log_debug(msg: str):
            logger.debug(f"[BrowserInspector] {msg}")
            result["debug_log"].append(msg)

        log_debug(f"インスペクト開始: 座標({x}, {y}), ウィンドウ: {window_info.get('title', 'Unknown')}")

        try:
            import pywinauto
            import pythoncom
            pythoncom.CoInitialize()

            desktop = pywinauto.Desktop(backend="uia")
            target_elements = []

            # 1. 座標からの取得試行
            # Why: 負の座標や画面外でのdesktop.from_pointによる0x80070057パラメーター不正例外を防止
            if x is not None and y is not None and x >= 0 and y >= 0:
                try:
                    elem = desktop.from_point(int(x), int(y))
                    if elem:
                        try:
                            r = elem.rectangle()
                            if r.left <= x <= r.right and r.top <= y <= r.bottom:
                                target_elements.append(("PointElement", elem))
                                log_debug("座標からのUI要素特定に成功しました")
                            else:
                                log_debug(f"PointElementが座標({x}, {y})を含まないためスキップ (rect: {r.left},{r.top},{r.right},{r.bottom})")
                        except Exception:
                            target_elements.append(("PointElement", elem))
                except Exception as e:
                    log_debug(f"座標からの要素特定に失敗: {e}")
                    try:
                        import uiautomation as auto
                        u_ctrl = auto.ControlFromPoint(int(x), int(y))
                        if u_ctrl and u_ctrl.NativeWindowHandle:
                            val = self._extract_value_from_uia(u_ctrl)
                            aid = getattr(u_ctrl, "AutomationId", "") or ""
                            c_name = getattr(u_ctrl, "Name", "") or ""
                            ct_name = getattr(u_ctrl, "ControlTypeName", "") or ""
                            if aid or c_name:
                                result["element_name"] = c_name
                                result["control_type"] = ct_name.replace("Control", "")
                                result["text"] = val
                                result["value"] = val
                                if aid:
                                    result["css_selector"] = f"#{aid}"
                                    result["xpath"] = f"//*[@id='{aid}']"
                                log_debug(f"uiautomation座標特定成功: id='{aid}', name='{c_name}', val='{val}'")
                    except Exception as e2:
                        log_debug(f"uiautomation座標フォールバック失敗: {e2}")
            else:
                # Why: キー入力時は現在フォーカス要素または直前編集要素から確定入力値を救出
                try:
                    import uiautomation as auto
                    focused = auto.GetFocusedControl()
                    if focused:
                        val = self._extract_value_from_uia(focused)
                        auto_id = getattr(focused, "AutomationId", "") or ""
                        ctrl_type = getattr(auto, "ControlTypeName", lambda ct: "Edit")(focused.ControlType)
                        elem_name = getattr(focused, "Name", "") or getattr(focused, "CurrentName", "") or ""
                        if val or auto_id or elem_name:
                            result["text"] = val
                            result["value"] = val
                            result["control_type"] = ctrl_type
                            result["element_name"] = elem_name
                            if auto_id:
                                result["css_selector"] = f"#{auto_id}"
                                result["xpath"] = f"//*[@id='{auto_id}']"
                            log_debug(f"フォーカス要素直接取得成功: id='{auto_id}', name='{elem_name}', val='{val}'")
                except Exception as ef:
                    log_debug(f"フォーカス要素特定例外: {ef}")
                    if BrowserInspector._last_input_selector and BrowserInspector._last_input_selector in BrowserInspector._form_values_cache:
                        result["css_selector"] = BrowserInspector._last_input_selector
                        result["value"] = BrowserInspector._form_values_cache[BrowserInspector._last_input_selector]
                        result["text"] = result["value"]

            extracted_text = ""
            primary_elem = None

            # 2. メイン探索＆子孫ツリー探索
            for source_name, elem in target_elements:
                if not elem: continue
                if primary_elem is None:
                    primary_elem = elem

                try:
                    c_type = getattr(elem.element_info, "control_type", "") or ""
                    e_name = elem.window_text() or ""
                    log_debug(f"[{source_name}] 評価中 - Type: {c_type}, Name: '{e_name}'")

                    if not result["control_type"]: result["control_type"] = c_type
                    if not result["element_name"]: result["element_name"] = e_name
                except Exception:
                    continue

                extracted_text = self._extract_value_from_elem(elem)
                if extracted_text:
                    break

                try:
                    children = elem.children()[:20]
                    for idx, child in enumerate(children):
                        # Why: 座標指定時はクリック地点を含まない無関係な子要素テキストの誤取得を完全防止
                        if x is not None and y is not None:
                            try:
                                r = child.rectangle()
                                if not (r.left <= x <= r.right and r.top <= y <= r.bottom):
                                    continue
                            except Exception:
                                continue
                        child_text = self._extract_value_from_elem(child)
                        if child_text:
                            extracted_text = child_text
                            log_debug(f"[{source_name}] 子要素({idx})からテキストを救出")
                            try:
                                result["control_type"] = getattr(child.element_info, "control_type", "") or result["control_type"]
                            except Exception:
                                pass
                            break
                except Exception as e:
                    log_debug(f"[{source_name}] 子要素走査例外: {e}")

                if extracted_text:
                    break

            result["text"] = extracted_text
            result["value"] = extracted_text

            # Why: 直前編集要素のコミット値と現在フォームのスナップショットをコンテキストへ統合
            committed_prev = None
            if x is not None and y is not None:
                committed_prev = BrowserInspector._commit_previous_element()
            if committed_prev:
                result["committed_previous_value"] = committed_prev
            if BrowserInspector._form_values_cache:
                result["committed_values"] = dict(BrowserInspector._form_values_cache)

            # 3. DOM/CSSセレクタ推定
            if primary_elem:
                try:
                    auto_id = getattr(primary_elem.element_info, "automation_id", "") or ""
                    class_name = getattr(primary_elem.element_info, "class_name", "") or ""
                    ctrl_type = (getattr(primary_elem.element_info, "control_type", "") or "").lower()

                    tag_map = {"button": "button", "edit": "input", "hyperlink": "a", "combobox": "select", "checkbox": "input[type='checkbox']"}
                    tag = tag_map.get(ctrl_type, "div")

                    elem_text = result.get("element_name") or ""
                    if auto_id:
                        result["css_selector"] = f"#{auto_id}"
                        result["xpath"] = f"//*[@id='{auto_id}']"
                    elif class_name and not class_name.startswith("Chrome_"):
                        first_class = class_name.split()[0]
                        result["css_selector"] = f"{tag}.{first_class}"
                        result["xpath"] = f"//{tag}[contains(@class, '{first_class}')]"
                    elif elem_text and not elem_text.startswith("http"):
                        # Why: 表示テキストが存在する場合はURLではなく有為なテキストでセレクタ生成
                        clean_elem_text = elem_text.strip().replace("'", "\\'")[:30]
                        result["css_selector"] = f"{tag}:has-text('{clean_elem_text}')"
                        result["xpath"] = f"//{tag}[contains(text(), '{clean_elem_text}')]"
                    elif extracted_text and extracted_text.startswith(("http://", "https://")):
                        # Why: URLの場合はhas-textではなくhref属性一致セレクタを安全に生成
                        try:
                            parsed_u = urllib.parse.urlsplit(extracted_text)
                            u_path = parsed_u.path.rstrip("/")
                            if u_path:
                                result["css_selector"] = f"{tag}[href*='{u_path}']"
                                result["xpath"] = f"//{tag}[contains(@href, '{u_path}')]"
                            else:
                                result["css_selector"] = f"{tag}[href*='{parsed_u.netloc}']"
                                result["xpath"] = f"//{tag}[contains(@href, '{parsed_u.netloc}')]"
                        except Exception:
                            result["css_selector"] = tag
                            result["xpath"] = f"//{tag}"
                    elif extracted_text:
                        clean_ext = extracted_text.strip().replace("'", "\\'")[:25]
                        result["css_selector"] = f"{tag}:has-text('{clean_ext}')"
                        result["xpath"] = f"//{tag}[contains(text(), '{clean_ext}')]"
                    else:
                        result["css_selector"] = tag
                        result["xpath"] = f"//{tag}"
                except Exception as e:
                    log_debug(f"セレクタ推定中にエラー: {e}")

            # 4. UIAアドレスバー探索による確定URL・検索クエリの取得
            addr_info = self.get_address_bar_info(window_info, desktop=desktop)
            result["page_url"] = addr_info.get("url", "")
            
            # Why: クリック座標がアドレスバー内か判定し、Web内要素とアドレスバー操作を明確に分離
            is_address_bar = False
            addr_rect = addr_info.get("address_bar_rect")
            if addr_rect and x is not None and y is not None:
                if addr_rect["left"] <= x <= addr_rect["right"] and addr_rect["top"] <= y <= addr_rect["bottom"]:
                    is_address_bar = True
            elif primary_elem:
                aid = getattr(primary_elem.element_info, "automation_id", "").lower()
                aname = (primary_elem.window_text() or "").lower()
                if any(k in aid for k in ["urlbar", "address", "omnibox"]) or any(k in aname for k in ["アドレス", "address bar"]):
                    is_address_bar = True
            elif x is None and y is None and addr_info.get("address_bar_text"):
                # Why: キー入力時にアドレスバーテキストが存在する場合はアドレスバー操作と判定
                is_address_bar = True

            result["is_address_bar"] = is_address_bar
            if is_address_bar:
                result["url"] = addr_info.get("url", "")
                result["query"] = addr_info.get("query")
                result["url_details"] = addr_info.get("url_details", {})
                if not result["text"]:
                    result["text"] = addr_info.get("address_bar_text", "")
                    result["value"] = result["text"]
            else:
                # Why: 画面内テキストにURLが含まれるだけの非リンク誤認を防ぎHyperlink時のみURL設定
                is_link_control = "hyperlink" in str(result.get("control_type", "")).lower()
                if is_link_control and extracted_text and (extracted_text.startswith("http://") or extracted_text.startswith("https://")):
                    result["url"] = extracted_text
                else:
                    result["url"] = ""

            # Why: 登録ボタン等のクリック時または入力要素移動時にフォームスナップショットを更新
            c_type_l = str(result.get("control_type", "")).lower()
            e_name_l = str(result.get("element_name", "")).lower()
            sel_l = str(result.get("css_selector", "")).lower()
            is_submit_action = "button" in c_type_l or any(k in e_name_l for k in ["登録", "送信", "保存", "submit", "save"]) or "submit" in sel_l
            if is_submit_action or (x is not None and y is not None):
                snapshot = BrowserInspector.extract_form_snapshot(window_info)
                if snapshot:
                    result["form_snapshot"] = snapshot

            # Why: コントロール種別およびタグ構造に基づく普遍的な入力要素判定
            input_controls = {"edit", "combobox", "checkbox", "spinner", "radiobutton", "listitem"}
            input_tags = {"input", "select", "textarea", "button"}
            is_input = (
                any(t in c_type_l for t in input_controls) or
                any(sel_l.startswith(tag) or f">{tag}" in sel_l for tag in input_tags) or
                "textbox" in c_type_l or "editable" in c_type_l
            )
            if is_input and primary_elem:
                BrowserInspector._last_input_element = primary_elem
                BrowserInspector._last_input_selector = result.get("css_selector")
                BrowserInspector._last_input_name = result.get("element_name")
                BrowserInspector._last_input_hwnd = window_info.get("hwnd") or window_info.get("handle")

        except ImportError:
            error_msg = "pywinauto がインストールされていません"
            result["error"] = error_msg
            log_debug(error_msg)
        except Exception as e:
            error_msg = f"インスペクト例外: {str(e)}"
            result["error"] = error_msg
            log_debug(error_msg)
            traceback.print_exc()
        finally:
            try:
                import pythoncom
                pythoncom.CoUninitialize()
            except Exception:
                pass

        log_debug(f"インスペクト完了. URL: '{result.get('url')}', クエリ: '{result.get('query')}'")
        return result
