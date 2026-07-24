# src/ui/views/macro_editor/macro_visual_canvas.py
from pathlib import Path
from PySide6.QtWidgets import QWidget, QVBoxLayout
from PySide6.QtGui import QPainter, QPen, QColor, QDropEvent, QDragEnterEvent
from PySide6.QtCore import Qt, Signal

from .action_block_widget import ActionBlockWidget

class MacroVisualCanvas(QWidget):
    commands_changed = Signal()

    def __init__(self, commands: list, workflow_dir: Path, parent=None):
        super().__init__(parent)
        self.commands = commands
        self.workflow_dir = workflow_dir
        self.blocks = []
        self.loops = []
        
        self.setAcceptDrops(True)
        self.main_layout = QVBoxLayout(self)
        self.main_layout.setAlignment(Qt.AlignmentFlag.AlignHCenter | Qt.AlignmentFlag.AlignTop)
        self.main_layout.setContentsMargins(100, 40, 100, 40)
        self.main_layout.setSpacing(40) # ブロック間の間隔
        
        self.rebuild()
        
    def rebuild(self):
        self._analyze_loops()
        
        while self.main_layout.count():
            item = self.main_layout.takeAt(0)
            widget = item.widget()
            if widget:
                widget.deleteLater()
                
        self.blocks.clear()
        
        current_loop_depth = 0
        for i, cmd in enumerate(self.commands):
            method = cmd.get("method")
            if method == "loop_start":
                current_loop_depth += 1
                
            is_in_loop = current_loop_depth > 0
            block = ActionBlockWidget(cmd, i, self.workflow_dir, is_in_loop)
            block.delete_requested.connect(self._on_delete_requested)
            block.content_changed.connect(self.commands_changed.emit)
            
            self.main_layout.addWidget(block)
            self.blocks.append(block)
            
            if method == "loop_end":
                current_loop_depth = max(0, current_loop_depth - 1)
            
        self.update()

    def get_block_widget(self, index: int) -> QWidget:
        for block in self.blocks:
            if block.cmd_index == index:
                return block
        return None

    def _get_template_for_action(self, action_type: str) -> dict:
        templates = {
            "click": {"method": "click", "args": {"x": 0, "y": 0, "button": "left", "clicks": 1}},
            "type_text": {"method": "type_text", "args": {"text": ""}},
            "wait": {"method": "wait", "args": {"duration": 1.0}},
            "press_key": {"method": "press_key", "args": {"key": "enter"}}
        }
        return templates.get(action_type, {"method": action_type, "args": {}})

    def _on_delete_requested(self, cmd_index: int):
        if 0 <= cmd_index < len(self.commands):
            self.commands.pop(cmd_index)
            self.commands_changed.emit()
            self.rebuild()

    def dragEnterEvent(self, event: QDragEnterEvent):
        mime_text = event.mimeData().text()
        if mime_text.startswith("action_block:") or mime_text.startswith("new_action:"):
            event.acceptProposedAction()

    def dropEvent(self, event: QDropEvent):
        mime_text = event.mimeData().text()
        drop_y = event.pos().y()
        target_idx = len(self.commands)
        
        for block in self.blocks:
            if drop_y < block.geometry().center().y():
                target_idx = block.cmd_index
                break
                
        if mime_text.startswith("action_block:"):
            source_idx = int(mime_text.split(":")[1])
            if source_idx == target_idx or source_idx == target_idx - 1:
                return
                
            cmd = self.commands.pop(source_idx)
            if target_idx > source_idx:
                target_idx -= 1
            self.commands.insert(target_idx, cmd)
            
        elif mime_text.startswith("new_action:"):
            action_type = mime_text.split(":")[1]
            cmd_template = self._get_template_for_action(action_type)
            import copy
            self.commands.insert(target_idx, copy.deepcopy(cmd_template))
            
        self.commands_changed.emit()
        self.rebuild()

    def _analyze_loops(self):
        self.loops = []
        loop_stack = []
        action_idx = 0
        
        for cmd in self.commands:
            method = cmd.get("method")
            if method == "loop_start":
                args = cmd.get("args", {})
                loop_stack.append({
                    "start_action_idx": action_idx,
                    "loop_count": args.get("loop_count", 1)
                })
            elif method == "loop_end":
                if loop_stack:
                    loop_info = loop_stack.pop()
                    loop_info["end_action_idx"] = action_idx
                    loop_info["size"] = loop_info["end_action_idx"] - loop_info["start_action_idx"]
                    self.loops.append(loop_info)
            action_idx += 1
                
        self.loops.sort(key=lambda x: x["size"], reverse=True)
        right_loops = []
        left_loops = []
        
        for i, loop in enumerate(self.loops):
            direction = "right" if i % 2 == 0 else "left"
            target_list = right_loops if direction == "right" else left_loops
                
            overlapping_lanes = []
            for other in target_list:
                if not (loop["end_action_idx"] < other["start_action_idx"] or loop["start_action_idx"] > other["end_action_idx"]):
                    overlapping_lanes.append(other["lane"])
                    
            lane = max(overlapping_lanes) + 1 if overlapping_lanes else 0
            loop["direction"] = direction
            loop["lane"] = lane
            target_list.append(loop)

    def paintEvent(self, event):
        super().paintEvent(event)
        if not self.blocks:
            return
            
        painter = QPainter(self)
        painter.setRenderHint(QPainter.RenderHint.Antialiasing)
        
        flow_pen = QPen(QColor(180, 180, 180), 3, Qt.PenStyle.SolidLine, Qt.PenCapStyle.RoundCap, Qt.PenJoinStyle.RoundJoin)
        painter.setPen(flow_pen)
        
        for i in range(len(self.blocks) - 1):
            block1 = self.blocks[i]
            block2 = self.blocks[i+1]
            
            x = block1.geometry().center().x()
            y1 = block1.geometry().bottom()
            y2 = block2.geometry().top()
            
            painter.drawLine(x, y1, x, y2)
            
            arrow_size = 8
            painter.drawLine(x, y2, x - arrow_size, y2 - arrow_size)
            painter.drawLine(x, y2, x + arrow_size, y2 - arrow_size)

        loop_pen = QPen(QColor("#0078d4"), 2, Qt.PenStyle.SolidLine, Qt.PenCapStyle.RoundCap, Qt.PenJoinStyle.RoundJoin)
        painter.setPen(loop_pen)
        
        font = painter.font()
        font.setBold(True)
        painter.setFont(font)
        fm = painter.fontMetrics()
        
        for loop in self.loops:
            start_idx = loop["start_action_idx"]
            end_idx = loop["end_action_idx"]
            
            if start_idx < 0 or end_idx >= len(self.blocks) or start_idx > end_idx:
                continue
                
            start_block = self.blocks[start_idx]
            end_block = self.blocks[end_idx]
            
            x_center = start_block.geometry().center().x()
            y_bottom = end_block.geometry().bottom() + 20
            y_top = start_block.geometry().top() - 20
            
            base_offset = 190
            lane_width = 40
            offset = base_offset + loop["lane"] * lane_width
            
            x_turn = x_center + offset if loop["direction"] == "right" else x_center - offset
                
            painter.drawLine(x_center, y_bottom, x_turn, y_bottom)
            painter.drawLine(x_turn, y_bottom, x_turn, y_top)
            painter.drawLine(x_turn, y_top, x_center, y_top)
            
            arrow_size = 8
            painter.drawLine(x_center, y_top, x_center - arrow_size, y_top - arrow_size)
            painter.drawLine(x_center, y_top, x_center + arrow_size, y_top - arrow_size)
            
            text = f"{loop['loop_count']}回"
            text_y = (y_top + y_bottom) / 2
            
            if loop["direction"] == "right":
                painter.drawText(x_turn + 8, text_y, text)
            else:
                text_width = fm.horizontalAdvance(text)
                painter.drawText(x_turn - text_width - 8, text_y, text)