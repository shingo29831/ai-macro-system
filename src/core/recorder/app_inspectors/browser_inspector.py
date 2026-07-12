# @role: ブラウザ特有のUIA情報（URLバー、検索ボックス等）を取得し、子孫ノードまでアグレッシブに探索して文字を捕捉する実装
from typing import Any, Dict, Optional, List
import traceback
import logging
from .base_inspector import BaseInspector

logger = logging.getLogger(__name__)

class BrowserInspector(BaseInspector):
    def inspect(self, window_info: Dict[str, Any], x: Optional[int] = None, y: Optional[int] = None) -> Dict[str, Any]:
        result = {
            "app": "Browser",
            "element_name": "",
            "control_type": "",
            "text": "",
            "value": "",
            "url": "",
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

            # 1. 座標からの取得を試行
            if x is not None and y is not None:
                try:
                    elem = desktop.from_point(int(x), int(y))
                    target_elements.append(("PointElement", elem))
                    log_debug("座標からのUI要素特定に成功しました")
                except Exception as e:
                    log_debug(f"座標からの要素特定に失敗: {e}")

            # 2. 現在アクティブ（フォーカス）な要素を確実に追加
            try:
                active_elem = desktop.get_active()
                target_elements.append(("ActiveElement", active_elem))
                log_debug("フォーカス要素を探索候補に追加しました")
            except Exception as e:
                log_debug(f"フォーカス要素の取得に失敗: {e}")

            # --- 内部関数: エレメントからテキストを抽出する試行 ---
            def extract_from_element(elem, prefix: str) -> str:
                # 試行A: ValuePattern
                try:
                    if elem.is_value_pattern_available():
                        val = elem.get_value()
                        if val and len(str(val).strip()) > 0:
                            log_debug(f"[{prefix}] ValuePatternから発見: '{val}'")
                            return str(val).strip()
                except Exception:
                    pass

                # 試行B: LegacyIAccessible
                try:
                    legacy_val = elem.legacy_properties().get("Value", "")
                    if legacy_val and len(str(legacy_val).strip()) > 0:
                        log_debug(f"[{prefix}] LegacyValueから発見: '{legacy_val}'")
                        return str(legacy_val).strip()
                except Exception:
                    pass

                # 試行C: Edit/Documentコントロールの場合の WindowText (名前)
                try:
                    ctrl_type = getattr(elem.element_info, "control_type", "") or ""
                    name = elem.window_text() or ""
                    if "edit" in ctrl_type.lower() or "document" in ctrl_type.lower():
                        if name and len(name.strip()) > 0 and name not in ["検索", "Search", "クリア", "×"]:
                            log_debug(f"[{prefix}] Edit/DocのNameから発見: '{name}'")
                            return name.strip()
                except Exception:
                    pass

                return ""

            extracted_text = ""
            primary_elem = None

            # 3. メイン探索＆子孫ツリー探索 (Tree Walking)
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

                # その要素自身から抽出
                extracted_text = extract_from_element(elem, source_name)
                if extracted_text:
                    break

                # --- 自身がコンテナ(Pane/Group等)で空の場合、子要素を探索する ---
                log_debug(f"[{source_name}] 直接の値が空のため、子孫ツリー(最大20件)を再帰探索します...")
                try:
                    children = elem.children()[:20] # パフォーマンスのため制限
                    for idx, child in enumerate(children):
                        child_text = extract_from_element(child, f"{source_name}_Child{idx}")
                        if child_text:
                            extracted_text = child_text
                            log_debug(f"[{source_name}] 子要素({idx})からテキストのレスキューに成功しました")
                            try:
                                result["control_type"] = getattr(child.element_info, "control_type", "") or result["control_type"]
                            except Exception:
                                pass
                            break
                except Exception as e:
                    log_debug(f"[{source_name}] 子要素の探索中にエラー: {e}")

                if extracted_text:
                    break

            result["text"] = extracted_text
            result["value"] = extracted_text

            # 4. URL判定
            if extracted_text and (extracted_text.startswith("http://") or extracted_text.startswith("https://") or "www." in extracted_text):
                result["url"] = extracted_text
                log_debug(f"URLとして識別: '{extracted_text}'")

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
                
        log_debug(f"インスペクト完了. 最終取得テキスト: '{result.get('text')}'")
        return result