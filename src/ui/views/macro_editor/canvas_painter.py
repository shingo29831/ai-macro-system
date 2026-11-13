"""Module: @role: マクロエディタキャンバス上のフロー接続線、ループ矢印、ドラッグプレビュー描画およびハンドル当たり判定を担当する。"""

from typing import Any, Callable, Dict, List, Optional, Tuple
from PySide6.QtCore import QPoint, Qt
from PySide6.QtGui import QColor, QPainter, QPen
from PySide6.QtWidgets import QWidget

BASE_LOOP_OFFSET = 40
LOOP_LANE_WIDTH = 40
HANDLE_HIT_RADIUS_SQ = 100


def _compute_loop_endpoints(
    start_block: QWidget,
    end_block: QWidget,
    same_block: bool,
) -> Tuple[int, int, int, int]:
    x_top = start_block.geometry().right()
    y_top = start_block.geometry().center().y()
    x_bottom = end_block.geometry().right()
    y_bottom = end_block.geometry().center().y()
    if same_block:
        y_top = start_block.geometry().top() + 15
        y_bottom = start_block.geometry().bottom() - 15
    return x_top, y_top, x_bottom, y_bottom


def hit_test_loop_handle(
    pos: QPoint,
    loops: List[Dict[str, Any]],
    get_block_fn: Callable[[int], Optional[QWidget]],
) -> Optional[Tuple[str, int]]:
    for loop in loops:
        start_idx = loop["start_action_idx"]
        end_idx = loop["end_action_idx"]
        start_block = get_block_fn(start_idx)
        end_block = get_block_fn(end_idx)
        if not start_block or not end_block:
            continue

        x_top, y_top, x_bottom, y_bottom = _compute_loop_endpoints(start_block, end_block, start_idx == end_idx)
        if start_idx == end_idx and loop.get("is_reversed", False):
            y_top, y_bottom = y_bottom, y_top

        if not loop.get("is_reversed", False):
            start_handle_pos = QPoint(x_top, y_top)
            end_handle_pos = QPoint(x_bottom, y_bottom)
        else:
            start_handle_pos = QPoint(x_bottom, y_bottom)
            end_handle_pos = QPoint(x_top, y_top)

        if (pos.x() - end_handle_pos.x()) ** 2 + (pos.y() - end_handle_pos.y()) ** 2 < HANDLE_HIT_RADIUS_SQ:
            return ("end", loop["loop_end_idx"])
        if (pos.x() - start_handle_pos.x()) ** 2 + (pos.y() - start_handle_pos.y()) ** 2 < HANDLE_HIT_RADIUS_SQ:
            return ("start", loop["loop_start_idx"])
    return None


def _draw_flow_connectors(painter: QPainter, blocks: List[QWidget]) -> None:
    flow_pen = QPen(QColor(180, 180, 180), 3, Qt.PenStyle.SolidLine, Qt.PenCapStyle.RoundCap, Qt.PenJoinStyle.RoundJoin)
    painter.setPen(flow_pen)
    arrow_size = 12
    for i in range(len(blocks) - 1):
        block1 = blocks[i]
        block2 = blocks[i + 1]
        x = block1.geometry().center().x()
        y1 = block1.geometry().bottom()
        y2 = block2.geometry().top()
        painter.drawLine(x, y1, x, y2)
        painter.drawLine(x, y2, x - arrow_size, y2 - arrow_size)
        painter.drawLine(x, y2, x + arrow_size, y2 - arrow_size)


def _draw_loop_arcs(
    painter: QPainter,
    loops: List[Dict[str, Any]],
    loop_widgets: List[QWidget],
    warning_widgets: List[Optional[QWidget]],
    get_block_fn: Callable[[int], Optional[QWidget]],
) -> None:
    arrow_size = 14
    handle_radius = 4
    for i, loop in enumerate(loops):
        start_idx = loop["start_action_idx"]
        end_idx = loop["end_action_idx"]
        is_reversed = loop.get("is_reversed", False)
        start_block = get_block_fn(start_idx)
        end_block = get_block_fn(end_idx)
        if not start_block or not end_block:
            continue

        x_top, y_top, x_bottom, y_bottom = _compute_loop_endpoints(start_block, end_block, start_idx == end_idx)
        offset = BASE_LOOP_OFFSET + loop["lane"] * LOOP_LANE_WIDTH
        x_turn = max(x_top, x_bottom) + offset

        color_hex = "#d13438" if is_reversed else "#0078d4"
        painter.setPen(QPen(QColor(color_hex), 2, Qt.PenStyle.SolidLine, Qt.PenCapStyle.RoundCap, Qt.PenJoinStyle.RoundJoin))
        painter.drawLine(x_bottom, y_bottom, x_turn, y_bottom)
        painter.drawLine(x_turn, y_bottom, x_turn, y_top)
        painter.drawLine(x_turn, y_top, x_top, y_top)

        painter.setBrush(QColor("#ffffff"))
        if not is_reversed:
            painter.drawLine(x_top, y_top, x_top + arrow_size, y_top - arrow_size)
            painter.drawLine(x_top, y_top, x_top + arrow_size, y_top + arrow_size)
        else:
            painter.drawLine(x_bottom, y_bottom, x_bottom + arrow_size, y_bottom - arrow_size)
            painter.drawLine(x_bottom, y_bottom, x_bottom + arrow_size, y_bottom + arrow_size)

        painter.setPen(QPen(QColor(color_hex), 2))
        painter.drawEllipse(QPoint(x_bottom, y_bottom), handle_radius, handle_radius)
        painter.drawEllipse(QPoint(x_top, y_top), handle_radius, handle_radius)

        target_x = x_turn + 10
        if i < len(loop_widgets):
            w = loop_widgets[i]
            w.adjustSize()
            # Why: ループ線の垂直中央位置に設定カードを安定配置
            target_y = max(10, int((y_top + y_bottom) / 2 - w.height() / 2))
            if w.pos() != QPoint(target_x, target_y):
                w.move(target_x, target_y)

        if i < len(warning_widgets) and warning_widgets[i] and i < len(loop_widgets):
            warn_w = warning_widgets[i]
            warn_w.adjustSize()
            w = loop_widgets[i]
            warn_x = target_x + w.width() + 4
            warn_y = int((y_top + y_bottom) / 2 - warn_w.height() / 2)
            if warn_w.pos() != QPoint(warn_x, warn_y):
                warn_w.move(warn_x, warn_y)


def _draw_drag_loop_preview(
    painter: QPainter,
    drag_loop_info: Dict[str, Any],
    loops: List[Dict[str, Any]],
    blocks: List[QWidget],
    commands: List[Dict[str, Any]],
    get_block_fn: Callable[[int], Optional[QWidget]],
) -> None:
    loop_id = drag_loop_info["loop_id"]
    handle_type = drag_loop_info["type"]
    target_idx = drag_loop_info["target_idx"]

    current_loop = next(
        (loop for loop in loops if commands[loop["loop_start_idx"]].get("loop_id") == loop_id),
        None,
    )
    if not current_loop:
        return

    target_block = get_block_fn(target_idx)
    if target_block:
        target_y = target_block.geometry().center().y()
        target_x = target_block.geometry().right()
    elif blocks:
        last_block = blocks[-1]
        target_y = last_block.geometry().bottom() + 20
        target_x = last_block.geometry().right()
    else:
        target_y, target_x = 0, 0

    fixed_idx = current_loop["end_action_idx"] if handle_type == "start" else current_loop["start_action_idx"]
    fixed_block = get_block_fn(fixed_idx)
    if not fixed_block:
        return

    if target_idx == fixed_idx and target_block:
        y_top = target_block.geometry().top() + 15
        y_bottom = target_block.geometry().bottom() - 15
        x_top = target_x
        x_bottom = target_x
    elif handle_type == "start":
        y_top, x_top = target_y, target_x
        y_bottom, x_bottom = fixed_block.geometry().center().y(), fixed_block.geometry().right()
    else:
        y_top, x_top = fixed_block.geometry().center().y(), fixed_block.geometry().right()
        y_bottom, x_bottom = target_y, target_x

    is_reversed = y_top > y_bottom
    x_turn = max(x_top, x_bottom) + BASE_LOOP_OFFSET + current_loop["lane"] * LOOP_LANE_WIDTH
    color = QColor(209, 52, 56, 120) if is_reversed else QColor(0, 120, 212, 120)
    painter.setPen(QPen(color, 2, Qt.PenStyle.DashLine, Qt.PenCapStyle.RoundCap, Qt.PenJoinStyle.RoundJoin))

    painter.drawLine(x_bottom, y_bottom, x_turn, y_bottom)
    painter.drawLine(x_turn, y_bottom, x_turn, y_top)
    painter.drawLine(x_turn, y_top, x_top, y_top)

    arrow_size = 14
    if not is_reversed:
        painter.drawLine(x_top, y_top, x_top + arrow_size, y_top - arrow_size)
        painter.drawLine(x_top, y_top, x_top + arrow_size, y_top + arrow_size)
    else:
        painter.drawLine(x_bottom, y_bottom, x_bottom + arrow_size, y_bottom - arrow_size)
        painter.drawLine(x_bottom, y_bottom, x_bottom + arrow_size, y_bottom + arrow_size)


def paint_canvas_flows(
    painter: QPainter,
    blocks: List[QWidget],
    loops: List[Dict[str, Any]],
    loop_widgets: List[QWidget],
    warning_widgets: List[Optional[QWidget]],
    drag_loop_info: Optional[Dict[str, Any]],
    commands: List[Dict[str, Any]],
    get_block_fn: Callable[[int], Optional[QWidget]],
) -> None:
    _draw_flow_connectors(painter, blocks)
    _draw_loop_arcs(painter, loops, loop_widgets, warning_widgets, get_block_fn)
    if drag_loop_info:
        _draw_drag_loop_preview(painter, drag_loop_info, loops, blocks, commands, get_block_fn)
