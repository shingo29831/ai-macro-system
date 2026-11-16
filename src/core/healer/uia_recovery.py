"""Module: @role: BrowserおよびWindows UI Automationを用いたStage 1自己修復とウィンドウ境界解決を担当する。"""

import ctypes
import logging
import platform
import threading
from typing import Any, Dict, List, Optional, Tuple

from core.shared.window_utils import get_window_bounds, is_browser_window, is_ignored_system_window

logger = logging.getLogger(__name__)

_GENERIC_SELECTOR_TAGS = {"div", "span", "p", "a", "li", "ul", "ol", "body", "html", "section", "header", "footer"}
_CONTAINER_CONTROL_TYPES = {"windowcontrol", "documentcontrol", "titlebarcontrol", "scrollbarcontrol"}


def get_effective_hwnd(window_hwnd: int) -> int:
    """明示されたHWNDが有効かつ自システム/除外対象外である場合のみ返す。"""
    if platform.system() != "Windows" or not window_hwnd:
        return 0
    try:
        user32 = ctypes.windll.user32
        hwnd_int = int(window_hwnd)
        if not user32.IsWindow(hwnd_int):
            return 0
        ln = user32.GetWindowTextLengthW(hwnd_int)
        if ln > 0:
            buf = ctypes.create_unicode_buffer(ln + 1)
            user32.GetWindowTextW(hwnd_int, buf, ln + 1)
            if is_ignored_system_window(buf.value):
                return 0
        return hwnd_int
    except Exception:
        return 0


def get_window_crop_box(
    hwnd: int, monitor_info: Any, img_w: int, img_h: int
) -> Optional[Tuple[int, int, int, int]]:
    """対象ウィンドウ矩形をスクリーンショット内の切り抜き座標 (x1, y1, x2, y2) へ変換する。"""
    if platform.system() != "Windows" or not hwnd:
        return None
    try:
        bounds = get_window_bounds(int(hwnd))
        if not bounds:
            return None
        left, top, right, bottom = bounds
        m_left = int(monitor_info.get("left", 0)) if isinstance(monitor_info, dict) else 0
        m_top = int(monitor_info.get("top", 0)) if isinstance(monitor_info, dict) else 0
        x1 = max(0, min(img_w, left - m_left))
        y1 = max(0, min(img_h, top - m_top))
        x2 = max(0, min(img_w, right - m_left))
        y2 = max(0, min(img_h, bottom - m_top))
        if (x2 - x1) >= 40 and (y2 - y1) >= 40:
            return x1, y1, x2, y2
    except Exception:
        pass
    return None


def try_browser_uia_recovery(
    workflow_id: str,
    target_context: Optional[Dict[str, Any]],
    last_win_args: Optional[Dict[str, Any]],
    candidate_labels: List[str],
) -> Optional[Dict[str, Any]]:
    """ブラウザ対象時にDOM/UIA名およびスクロール可視化を用いて要素座標を修復する。"""
    if not target_context:
        return None
    win_title = str(last_win_args.get("window_title") or "") if last_win_args else ""
    selector = str(target_context.get("selector") or "").strip()
    sel_usable = bool(selector and selector.lower() not in _GENERIC_SELECTOR_TAGS)
    url = str(target_context.get("url") or "").strip()
    if not (is_browser_window(win_title) or sel_usable or url.startswith("http")):
        return None

    elem_name = str(target_context.get("element_name") or "").strip() or (
        candidate_labels[0] if candidate_labels else ""
    )
    if not (sel_usable or elem_name or url.startswith("http")):
        return None

    try:
        from core.executor.browser_uia_scroller import scroll_into_view
        from core.executor.browser_uia_searcher import find_uia_element

        # Why: ズレた旧座標でのControlFromPoint誤爆を防ぐため座標を渡さずDOM/UIA名で探索
        elem = find_uia_element(
            selector=selector if sel_usable else None,
            last_win_args=last_win_args,
            timeout_sec=1.2,
            element_name=elem_name or None,
            url=url or None,
            text=target_context.get("text"),
        )
        if not elem:
            return None

        rect = elem.rectangle()
        if rect and rect.width() > 0 and rect.height() > 0:
            cx = (rect.left + rect.right) // 2
            cy = (rect.top + rect.bottom) // 2
            target_hwnd = last_win_args.get("mapped_hwnd") if last_win_args else None
            win_bounds = get_window_bounds(target_hwnd)
            in_viewport = cx > 0 and cy > 0
            if win_bounds:
                wl, wt, wr, wb = win_bounds
                in_viewport = wl <= cx <= wr and (wt + 80) <= cy <= (wb - 40)
            if in_viewport:
                logger.info(
                    f"[{workflow_id}] Stage 1 (Browser UIA) recovered '{elem_name or selector}' at ({cx}, {cy})."
                )
                return {
                    "success": True,
                    "stage": 1,
                    "method": "browser_uia",
                    "new_coordinates": {"x": int(cx), "y": int(cy)},
                }

            scrolled_coords = scroll_into_view(
                selector=selector if sel_usable else None,
                last_win_args=last_win_args,
                timeout_sec=1.0,
                element_name=elem_name or None,
                url=url or None,
                text=target_context.get("text"),
            )
            if scrolled_coords and scrolled_coords[0] > 0 and scrolled_coords[1] > 0:
                logger.info(
                    f"[{workflow_id}] Stage 1 (Browser Scroll) recovered '{elem_name or selector}' at {scrolled_coords}."
                )
                return {
                    "success": True,
                    "stage": 1,
                    "method": "browser_scroll",
                    "new_coordinates": {"x": int(scrolled_coords[0]), "y": int(scrolled_coords[1])},
                }
    except Exception as b_err:
        logger.debug(f"[{workflow_id}] Browser UIA recovery bypassed: {b_err}")
    return None


def _is_valid_uia_target_rect(rect: Any, win_bounds: Optional[Tuple[int, int, int, int]]) -> bool:
    if not rect or rect.width() <= 0 or rect.height() <= 0:
        return False
    cx = rect.left + rect.width() // 2
    cy = rect.top + rect.height() // 2
    if win_bounds:
        wl, wt, wr, wb = win_bounds
        if not (wl <= cx <= wr and wt <= cy <= wb):
            return False
        win_w = max(1, wr - wl)
        win_h = max(1, wb - wt)
        # Why: タブやページ全体など巨大コンテナの部分一致誤爆を防止
        if rect.width() >= win_w * 0.85 and rect.height() >= win_h * 0.7:
            return False
        if rect.height() >= win_h * 0.8:
            return False
    return True


def try_windows_uia_recovery(
    workflow_id: str,
    effective_hwnd: int,
    candidate_labels: List[str],
    timeout_sec: float = 1.8,
) -> Optional[Dict[str, Any]]:
    """Windows UI Automationツリーを別スレッドでタイムアウト付き走査し、要素座標を修復する。"""
    if platform.system() != "Windows" or not effective_hwnd or not candidate_labels:
        return None

    result_holder: List[Optional[Dict[str, Any]]] = [None]
    done_event = threading.Event()
    win_bounds = get_window_bounds(effective_hwnd)

    def _worker() -> None:
        try:
            import uiautomation as auto

            # Why: 実行スレッドのCOMアパートメント競合と無制限走査フリーズを回避
            with auto.UIAutomationInitializerInThread():
                root_ctrl = auto.ControlFromHandle(effective_hwnd)
                if not (root_ctrl and root_ctrl.Exists(0, 0)):
                    return

                for label in candidate_labels:
                    matched_ctrl = root_ctrl.Control(searchDepth=16, Name=label)
                    if not matched_ctrl or not matched_ctrl.Exists(0, 0):
                        matched_ctrl = root_ctrl.Control(searchDepth=16, SubName=label)
                    if matched_ctrl and matched_ctrl.Exists(0, 0) and not getattr(matched_ctrl, "IsOffscreen", False):
                        ct_name = str(getattr(matched_ctrl, "ControlTypeName", "") or "").lower()
                        rect = matched_ctrl.BoundingRectangle
                        if ct_name not in _CONTAINER_CONTROL_TYPES and _is_valid_uia_target_rect(rect, win_bounds):
                            cx = rect.left + rect.width() // 2
                            cy = rect.top + rect.height() // 2
                            logger.info(f"[{workflow_id}] Stage 1 (UIA) recovered '{label}' at ({cx}, {cy}).")
                            result_holder[0] = {
                                "success": True,
                                "stage": 1,
                                "method": "uia",
                                "new_coordinates": {"x": int(cx), "y": int(cy)},
                            }
                            return

                lower_labels = [lb.lower() for lb in candidate_labels if len(lb) >= 2]
                for ctrl, _depth in auto.WalkControl(root_ctrl, maxDepth=16):
                    if done_event.is_set():
                        return
                    if getattr(ctrl, "IsOffscreen", False):
                        continue
                    ct_name = str(getattr(ctrl, "ControlTypeName", "") or "").lower()
                    if ct_name in _CONTAINER_CONTROL_TYPES:
                        continue
                    rect = getattr(ctrl, "BoundingRectangle", None)
                    if not _is_valid_uia_target_rect(rect, win_bounds):
                        continue
                    name = str(getattr(ctrl, "Name", "") or "").strip().lower()
                    aid = str(getattr(ctrl, "AutomationId", "") or "").strip().lower()
                    if not name and not aid:
                        continue
                    if any(lb == name or lb == aid or lb in name or lb in aid for lb in lower_labels):
                        cx = rect.left + rect.width() // 2
                        cy = rect.top + rect.height() // 2
                        logger.info(f"[{workflow_id}] Stage 1 (UIA Walk) recovered '{name or aid}' at ({cx}, {cy}).")
                        result_holder[0] = {
                            "success": True,
                            "stage": 1,
                            "method": "uia_walk",
                            "new_coordinates": {"x": int(cx), "y": int(cy)},
                        }
                        return
        except Exception as uia_err:
            logger.debug(f"[{workflow_id}] UIA recovery bypassed: {uia_err}")
        finally:
            done_event.set()

    t = threading.Thread(target=_worker, daemon=True)
    t.start()
    if done_event.wait(timeout=timeout_sec):
        return result_holder[0]
    done_event.set()
    logger.debug(f"[{workflow_id}] Stage 1 (UIA) timed out after {timeout_sec}s.")
    return None
