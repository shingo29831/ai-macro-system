# @role: ブラウザ特有のUIA情報（URLバー、検索ボックス等）を取得し、主要ブラウザからのURL・検索クエリ抽出およびDOM探索を担当する実装
from typing import Any, Dict, Optional, List
import traceback
import logging
import urllib.parse
import ctypes
from .base_inspector import BaseInspector

logger = logging.getLogger(__name__)

class BrowserInspector(BaseInspector):
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

            # Why: 多言語・ブラウザバージョン差異に対応するため上部Edit候補をスコアリング走査
            candidates = []
            try:
                edits = window.descendants(control_type="Edit")
            except Exception:
                edits = []

            for edit in edits[:25]:
                try:
                    name = edit.window_text() or ""
                    auto_id = getattr(edit.element_info, "automation_id", "") or ""
                    val = self._extract_value_from_elem(edit)
                    rect = edit.rectangle()

                    score = 0
                    if any(kw in auto_id.lower() for kw in ["url", "address"]):
                        score += 50
                    if any(kw in name.lower() for kw in ["アドレス", "address", "url", "検索または", "search or enter"]):
                        score += 40
                    if val and any(ind in val for ind in ["http", "www.", ".com", ".org", ".jp", ".net", "search?"]):
                        score += 40
                    if rect.top < 300:
                        score += 20

                    if val and score > 0:
                        candidates.append((score, val))
                except Exception:
                    continue

            if candidates:
                candidates.sort(key=lambda x: x[0], reverse=True)
                best_val = candidates[0][1]
                result["address_bar_text"] = best_val
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
            if x is not None and y is not None:
                try:
                    elem = desktop.from_point(int(x), int(y))
                    target_elements.append(("PointElement", elem))
                    log_debug("座標からのUI要素特定に成功しました")
                except Exception as e:
                    log_debug(f"座標からの要素特定に失敗: {e}")

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

            # 3. DOM/CSSセレクタ推定
            if primary_elem:
                try:
                    auto_id = getattr(primary_elem.element_info, "automation_id", "") or ""
                    class_name = getattr(primary_elem.element_info, "class_name", "") or ""
                    ctrl_type = (getattr(primary_elem.element_info, "control_type", "") or "").lower()

                    tag_map = {"button": "button", "edit": "input", "hyperlink": "a", "combobox": "select", "checkbox": "input[type='checkbox']"}
                    tag = tag_map.get(ctrl_type, "div")

                    if auto_id:
                        result["css_selector"] = f"#{auto_id}"
                        result["xpath"] = f"//*[@id='{auto_id}']"
                    elif class_name and not class_name.startswith("Chrome_"):
                        first_class = class_name.split()[0]
                        result["css_selector"] = f"{tag}.{first_class}"
                        result["xpath"] = f"//{tag}[contains(@class, '{first_class}')]"
                    elif extracted_text:
                        result["css_selector"] = f"{tag}:has-text('{extracted_text[:20]}')"
                        result["xpath"] = f"//{tag}[contains(text(), '{extracted_text[:20]}')]"
                    else:
                        result["css_selector"] = tag
                        result["xpath"] = f"//{tag}"
                except Exception as e:
                    log_debug(f"セレクタ推定中にエラー: {e}")

            # 4. UIAアドレスバー探索による確定URL・検索クエリの取得
            addr_info = self.get_address_bar_info(window_info, desktop=desktop)
            if addr_info.get("url"):
                result["url"] = addr_info["url"]
                result["query"] = addr_info.get("query")
                result["url_details"] = addr_info.get("url_details", {})
                log_debug(f"アドレスバーからURL取得成功: {result['url']}, クエリ: '{result['query']}'")
            elif extracted_text:
                parsed = self.parse_url_details(extracted_text)
                if parsed:
                    result["url"] = parsed.get("full_url", "")
                    result["query"] = parsed.get("query")
                    result["url_details"] = parsed
                    log_debug(f"抽出テキストからURL識別: '{result['url']}'")

            # Why: 入力補完確定直後などで要素テキストが空の場合、クエリ文字列で安全に補完
            if not result["text"] and result["query"]:
                result["text"] = result["query"]
                result["value"] = result["query"]

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
