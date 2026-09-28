"""Module: @role: マクロアクションブロックを縦シークエンス状に配置し、実行フローやループ構造の矢印描画、ドラッグ＆ドロップによる並び替えを制御するキャンバス。"""

from pathlib import Path
from PySide6.QtWidgets import QWidget, QVBoxLayout
from PySide6.QtGui import QPainter, QPen, QColor, QDropEvent, QDragEnterEvent, QDrag
from PySide6.QtCore import Qt, Signal, QPoint, QMimeData

from .action_block_widget import ActionBlockWidget
from .loop_widgets import LoopCountWidget, WarningWidget


class MacroVisualCanvas(QWidget):
    commands_changed = Signal()

    def __init__(self, commands: list, workflow_dir: Path, parent=None):
        super().__init__(parent)
        self.commands = commands
        self.workflow_dir = workflow_dir
        self.blocks = []
        self.loops = []
        self.loop_widgets = []
        self.warning_widgets = []
        self._drag_loop_info = None
        
        self.setAcceptDrops(True)
        self.main_layout = QVBoxLayout(self)
        self.main_layout.setAlignment(Qt.AlignmentFlag.AlignHCenter | Qt.AlignmentFlag.AlignTop)
        # Why: 線の横に配置される詳細設定カード(幅270px)がはみ出さないよう右余白を拡張
        self.main_layout.setContentsMargins(60, 40, 360, 40)
        self.main_layout.setSpacing(40)
        
        self.rebuild()
        
    def rebuild(self):
        self._analyze_loops()
        
        while self.main_layout.count():
            item = self.main_layout.takeAt(0)
            widget = item.widget()
            if widget:
                widget.deleteLater()
                
        for w in self.loop_widgets:
            w.deleteLater()
        self.loop_widgets.clear()
        
        for w in self.warning_widgets:
            if w:
                w.deleteLater()
        self.warning_widgets.clear()
        
        self.blocks.clear()
        
        in_loop_set = set()
        for loop in self.loops:
            if not loop.get("is_reversed"):
                start = loop["loop_start_idx"]
                end = loop["loop_end_idx"]
                for j in range(start + 1, end):
                    in_loop_set.add(j)
        
        for i, cmd in enumerate(self.commands):
            method = cmd.get("method")
            if method in ["loop_start", "loop_end"]:
                continue
                
            is_in_loop = i in in_loop_set
            block = ActionBlockWidget(cmd, i, self.workflow_dir, is_in_loop)
            block.delete_requested.connect(self._on_delete_requested)
            block.content_changed.connect(self.commands_changed.emit)
            
            self.main_layout.addWidget(block)
            self.blocks.append(block)

        for loop in self.loops:
            start_cmd = self.commands[loop["loop_start_idx"]]
            w = LoopCountWidget(loop["loop_start_idx"], start_cmd, self.workflow_dir, self)
            if loop.get("is_reversed"):
                w.setStyleSheet("""
                    #LoopCountWidget {
                        background-color: #fff9f9;
                        border: 2px solid #d13438;
                        border-radius: 6px;
                    }
                """)
                
            w.count_changed.connect(self._on_loop_count_changed)
            w.settings_changed.connect(self.commands_changed.emit)
            w.delete_requested.connect(self._on_loop_delete_requested)
            w.show()
            self.loop_widgets.append(w)
            
            if loop.get("is_reversed"):
                warn_w = WarningWidget(self)
                warn_w.show()
                self.warning_widgets.append(warn_w)
            else:
                self.warning_widgets.append(None)
            
        self.update()

    def _on_loop_delete_requested(self, loop_start_idx: int):
        # Why: ループ解除ボタン押下時にloop_startとloop_endのペアを安全に除去
        if 0 <= loop_start_idx < len(self.commands):
            target_id = self.commands[loop_start_idx].get("loop_id")
            self.commands = [c for c in self.commands if not (c.get("method") in ["loop_start", "loop_end"] and c.get("loop_id") == target_id)]
            self.commands_changed.emit()
            self.rebuild()

    def _on_loop_count_changed(self, loop_start_idx: int, new_count: int):
        if 0 <= loop_start_idx < len(self.commands):
            cmd = self.commands[loop_start_idx]
            if cmd.get("method") == "loop_start":
                if "args" not in cmd:
                    cmd["args"] = {}
                cmd["args"]["loop_count"] = new_count
                self.commands_changed.emit()

    def get_block_widget(self, index: int) -> QWidget:
        for block in self.blocks:
            if block.cmd_index == index:
                return block
        return None

    def _get_template_for_action(self, action_type: str) -> dict:
        templates = {
            "click": {"method": "click", "args": {"x": 0, "y": 0, "button": "left", "clicks": 1}},
            "move": {"method": "move", "args": {"x": 0, "y": 0}},
            "scroll": {"method": "scroll", "args": {"dx": 0.0, "dy": -5.0, "x": 0, "y": 0}},
            "type_text": {"method": "type_text", "args": {"text": ""}},
            "wait": {"method": "wait", "args": {"duration": 1.0}},
            "press_key": {"method": "press_key", "args": {"key": "enter"}}
        }
        return templates.get(action_type, {"method": action_type, "args": {}})

    def _validate_loops(self) -> bool:
        loop_dict = {}
        for i, cmd in enumerate(self.commands):
            method = cmd.get("method")
            if method in ["loop_start", "loop_end"]:
                loop_id = cmd.get("loop_id")
                if not loop_id:
                    continue
                if loop_id not in loop_dict:
                    loop_dict[loop_id] = {}
                if method == "loop_start":
                    loop_dict[loop_id]["start"] = i
                else:
                    loop_dict[loop_id]["end"] = i
                    
        for loop_id, info in loop_dict.items():
            if "start" in info and "end" in info:
                top_idx = min(info["start"], info["end"])
                bottom_idx = max(info["start"], info["end"])
                
                has_action = False
                for j in range(top_idx + 1, bottom_idx):
                    if self.commands[j].get("method") not in ["loop_start", "loop_end"]:
                        has_action = True
                        break
                if not has_action:
                    return False
        return True

    def _on_delete_requested(self, cmd_index: int):
        if 0 <= cmd_index < len(self.commands):
            import copy
            backup_commands = copy.deepcopy(self.commands)
            
            self.commands.pop(cmd_index)
            
            if not self._validate_loops():
                self.commands = backup_commands
                return
                
            self.commands_changed.emit()
            self.rebuild()

    def mousePressEvent(self, event):
        pos = event.pos()
        for loop in self.loops:
            start_idx = loop["start_action_idx"]
            end_idx = loop["end_action_idx"]
            
            start_block = self.get_block_widget(start_idx)
            end_block = self.get_block_widget(end_idx)
            
            if not start_block or not end_block:
                continue
                
            x_top = start_block.geometry().right()
            y_top = start_block.geometry().center().y()
            
            x_bottom = end_block.geometry().right()
            y_bottom = end_block.geometry().center().y()
            
            if start_idx == end_idx:
                y_top = start_block.geometry().top() + 15
                y_bottom = start_block.geometry().bottom() - 15
                if loop.get("is_reversed", False):
                    y_top, y_bottom = y_bottom, y_top
            
            if not loop.get("is_reversed", False):
                start_handle_pos = QPoint(x_top, y_top)
                end_handle_pos = QPoint(x_bottom, y_bottom)
            else:
                start_handle_pos = QPoint(x_bottom, y_bottom)
                end_handle_pos = QPoint(x_top, y_top)
            
            # 終了地点ハンドル
            if (pos.x() - end_handle_pos.x())**2 + (pos.y() - end_handle_pos.y())**2 < 100:
                drag = QDrag(self)
                mime_data = QMimeData()
                mime_data.setText(f"loop_handle:end:{loop['loop_end_idx']}")
                drag.setMimeData(mime_data)
                drag.exec_(Qt.DropAction.MoveAction)
                return
                
            # 開始地点ハンドル
            if (pos.x() - start_handle_pos.x())**2 + (pos.y() - start_handle_pos.y())**2 < 100:
                drag = QDrag(self)
                mime_data = QMimeData()
                mime_data.setText(f"loop_handle:start:{loop['loop_start_idx']}")
                drag.setMimeData(mime_data)
                drag.exec_(Qt.DropAction.MoveAction)
                return
                
        super().mousePressEvent(event)

    def dragEnterEvent(self, event: QDragEnterEvent):
        mime_text = event.mimeData().text()
        if mime_text.startswith("action_block:") or mime_text.startswith("new_action:") or mime_text.startswith("loop_handle:"):
            event.acceptProposedAction()

    def dragMoveEvent(self, event):
        mime_text = event.mimeData().text()
        if mime_text.startswith("loop_handle:"):
            drop_y = event.pos().y()
            target_idx = len(self.commands)
            for block in self.blocks:
                if drop_y < block.geometry().center().y():
                    target_idx = block.cmd_index
                    break
            
            parts = mime_text.split(":")
            handle_type = parts[1]
            cmd_idx = int(parts[2])
            
            if 0 <= cmd_idx < len(self.commands):
                loop_id = self.commands[cmd_idx].get("loop_id")
                self._drag_loop_info = {
                    "type": handle_type,
                    "loop_id": loop_id,
                    "target_idx": target_idx
                }
                self.update()
            event.acceptProposedAction()
        elif mime_text.startswith("action_block:") or mime_text.startswith("new_action:"):
            event.acceptProposedAction()
        else:
            event.ignore()

    def dragLeaveEvent(self, event):
        self._drag_loop_info = None
        self.update()
        super().dragLeaveEvent(event)

    def dropEvent(self, event: QDropEvent):
        self._drag_loop_info = None
        mime_text = event.mimeData().text()
        drop_y = event.pos().y()
        target_idx = len(self.commands)
        
        for block in self.blocks:
            if drop_y < block.geometry().center().y():
                target_idx = block.cmd_index
                break
                
        import copy
        backup_commands = copy.deepcopy(self.commands)
        
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
            if action_type == "loop":
                # Why: ループ配置時はloop_startとloop_endをペアで生成し整合性を保証
                new_id = f"loop_{len(self.loops) + 1}"
                start_cmd = {
                    "method": "loop_start",
                    "loop_id": new_id,
                    "args": {"loop_count": 10, "data_source": "static"}
                }
                end_cmd = {
                    "method": "loop_end",
                    "loop_id": new_id,
                    "args": {}
                }
                ins_start = max(0, min(target_idx, len(self.commands)))
                self.commands.insert(ins_start, start_cmd)
                self.commands.append(end_cmd)
            else:
                cmd_template = self._get_template_for_action(action_type)
                self.commands.insert(target_idx, copy.deepcopy(cmd_template))
            
        elif mime_text.startswith("loop_handle:"):
            parts = mime_text.split(":")
            cmd_idx = int(parts[2])
            
            if 0 <= cmd_idx < len(self.commands):
                cmd = self.commands.pop(cmd_idx)
                if target_idx > cmd_idx:
                    target_idx -= 1
                self.commands.insert(target_idx, cmd)
                
        if not self._validate_loops():
            self.commands = backup_commands
            return
            
        self.commands_changed.emit()
        self.rebuild()

    def _ensure_loop_ids(self):
        stack = []
        loop_counter = 0
        
        for cmd in self.commands:
            if "loop_id" in cmd:
                try:
                    num = int(cmd["loop_id"].split("_")[1])
                    loop_counter = max(loop_counter, num + 1)
                except:
                    pass

        for cmd in self.commands:
            if cmd.get("method") == "loop_start":
                if "loop_id" not in cmd:
                    cmd["loop_id"] = f"loop_{loop_counter}"
                    loop_counter += 1
                stack.append(cmd["loop_id"])
            elif cmd.get("method") == "loop_end":
                if "loop_id" not in cmd:
                    if stack:
                        cmd["loop_id"] = stack.pop()
                    else:
                        cmd["loop_id"] = f"loop_{loop_counter}_orphan"
                        loop_counter += 1
                else:
                    if stack and stack[-1] == cmd["loop_id"]:
                        stack.pop()

    def _analyze_loops(self):
        self._ensure_loop_ids()
        
        loop_dict = {}
        for i, cmd in enumerate(self.commands):
            method = cmd.get("method")
            if method in ["loop_start", "loop_end"]:
                loop_id = cmd.get("loop_id")
                if not loop_id:
                    continue
                if loop_id not in loop_dict:
                    loop_dict[loop_id] = {"loop_count": 1}
                
                if method == "loop_start":
                    loop_dict[loop_id]["loop_start_idx"] = i
                    loop_dict[loop_id]["loop_count"] = cmd.get("args", {}).get("loop_count", 1)
                else:
                    loop_dict[loop_id]["loop_end_idx"] = i

        self.loops = []
        for loop_id, info in loop_dict.items():
            if "loop_start_idx" in info and "loop_end_idx" in info:
                start_idx = info["loop_start_idx"]
                end_idx = info["loop_end_idx"]
                
                is_reversed = start_idx > end_idx
                
                top_idx = min(start_idx, end_idx)
                bottom_idx = max(start_idx, end_idx)
                
                first_action_idx = -1
                last_action_idx = -1
                for j in range(top_idx + 1, bottom_idx):
                    if self.commands[j].get("method") not in ["loop_start", "loop_end"]:
                        if first_action_idx == -1:
                            first_action_idx = j
                        last_action_idx = j
                        
                if first_action_idx != -1 and last_action_idx != -1:
                    info["start_action_idx"] = first_action_idx
                    info["end_action_idx"] = last_action_idx
                    info["size"] = last_action_idx - first_action_idx
                    info["is_reversed"] = is_reversed
                    self.loops.append(info)
                    
        self.loops.sort(key=lambda x: x["size"], reverse=True)
        right_loops = []
        
        for i, loop in enumerate(self.loops):
            overlapping_lanes = []
            for other in right_loops:
                if not (loop["end_action_idx"] < other["start_action_idx"] or loop["start_action_idx"] > other["end_action_idx"]):
                    overlapping_lanes.append(other["lane"])
                    
            lane = max(overlapping_lanes) + 1 if overlapping_lanes else 0
            loop["lane"] = lane
            right_loops.append(loop)

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
            
            arrow_size = 12
            painter.drawLine(x, y2, x - arrow_size, y2 - arrow_size)
            painter.drawLine(x, y2, x + arrow_size, y2 - arrow_size)

        for i, loop in enumerate(self.loops):
            start_idx = loop["start_action_idx"]
            end_idx = loop["end_action_idx"]
            is_reversed = loop.get("is_reversed", False)
            
            start_block = self.get_block_widget(start_idx)
            end_block = self.get_block_widget(end_idx)
            
            if not start_block or not end_block:
                continue
            
            x_top = start_block.geometry().right()
            y_top = start_block.geometry().center().y()
            
            x_bottom = end_block.geometry().right()
            y_bottom = end_block.geometry().center().y()
            
            if start_idx == end_idx:
                y_top = start_block.geometry().top() + 15
                y_bottom = start_block.geometry().bottom() - 15
            
            base_offset = 40
            lane_width = 40
            offset = base_offset + loop["lane"] * lane_width
            
            x_turn = max(x_top, x_bottom) + offset
            
            if is_reversed:
                loop_pen = QPen(QColor("#d13438"), 2, Qt.PenStyle.SolidLine, Qt.PenCapStyle.RoundCap, Qt.PenJoinStyle.RoundJoin)
            else:
                loop_pen = QPen(QColor("#0078d4"), 2, Qt.PenStyle.SolidLine, Qt.PenCapStyle.RoundCap, Qt.PenJoinStyle.RoundJoin)
            painter.setPen(loop_pen)
                
            painter.drawLine(x_bottom, y_bottom, x_turn, y_bottom)
            painter.drawLine(x_turn, y_bottom, x_turn, y_top)
            painter.drawLine(x_turn, y_top, x_top, y_top)
            
            arrow_size = 14
            handle_radius = 4
            painter.setBrush(QColor("#ffffff"))
            
            if not is_reversed:
                painter.drawLine(x_top, y_top, x_top + arrow_size, y_top - arrow_size)
                painter.drawLine(x_top, y_top, x_top + arrow_size, y_top + arrow_size)
                
                painter.setPen(QPen(QColor("#0078d4"), 2))
                painter.drawEllipse(QPoint(x_bottom, y_bottom), handle_radius, handle_radius)
                painter.drawEllipse(QPoint(x_top, y_top), handle_radius, handle_radius)
            else:
                painter.drawLine(x_bottom, y_bottom, x_bottom + arrow_size, y_bottom - arrow_size)
                painter.drawLine(x_bottom, y_bottom, x_bottom + arrow_size, y_bottom + arrow_size)
                
                painter.setPen(QPen(QColor("#d13438"), 2))
                painter.drawEllipse(QPoint(x_top, y_top), handle_radius, handle_radius)
                painter.drawEllipse(QPoint(x_bottom, y_bottom), handle_radius, handle_radius)
            
            if i < len(self.loop_widgets):
                w = self.loop_widgets[i]
                w.adjustSize()
                target_x = x_turn + 10
                # Why: ループ線の垂直中央位置に設定カードを安定配置
                target_y = max(10, int((y_top + y_bottom) / 2 - w.height() / 2))
                if w.pos() != QPoint(target_x, target_y):
                    w.move(target_x, target_y)
                    
            if i < len(self.warning_widgets) and self.warning_widgets[i]:
                warn_w = self.warning_widgets[i]
                warn_w.adjustSize()
                w = self.loop_widgets[i]
                warn_x = target_x + w.width() + 4
                warn_y = int((y_top + y_bottom) / 2 - warn_w.height() / 2)
                if warn_w.pos() != QPoint(warn_x, warn_y):
                    warn_w.move(warn_x, warn_y)

        # プレビューの描画
        if getattr(self, '_drag_loop_info', None):
            info = self._drag_loop_info
            loop_id = info["loop_id"]
            handle_type = info["type"]
            target_idx = info["target_idx"]
            
            current_loop = None
            for loop in self.loops:
                start_cmd = self.commands[loop["loop_start_idx"]]
                if start_cmd.get("loop_id") == loop_id:
                    current_loop = loop
                    break
                    
            if current_loop:
                target_block = self.get_block_widget(target_idx)
                if target_block:
                    target_y = target_block.geometry().center().y()
                    target_x = target_block.geometry().right()
                else:
                    if self.blocks:
                        last_block = self.blocks[-1]
                        target_y = last_block.geometry().bottom() + 20
                        target_x = last_block.geometry().right()
                    else:
                        target_y = 0
                        target_x = 0
                
                fixed_block = None
                fixed_idx = -1
                if handle_type == "start":
                    fixed_idx = current_loop["end_action_idx"]
                    fixed_block = self.get_block_widget(fixed_idx)
                    if fixed_block:
                        if target_idx == fixed_idx:
                            y_top = target_block.geometry().top() + 15
                            y_bottom = target_block.geometry().bottom() - 15
                            x_top = target_x
                            x_bottom = target_x
                        else:
                            y_top = target_y
                            x_top = target_x
                            y_bottom = fixed_block.geometry().center().y()
                            x_bottom = fixed_block.geometry().right()
                else:
                    fixed_idx = current_loop["start_action_idx"]
                    fixed_block = self.get_block_widget(fixed_idx)
                    if fixed_block:
                        if target_idx == fixed_idx:
                            y_top = target_block.geometry().top() + 15
                            y_bottom = target_block.geometry().bottom() - 15
                            x_top = target_x
                            x_bottom = target_x
                        else:
                            y_top = fixed_block.geometry().center().y()
                            x_top = fixed_block.geometry().right()
                            y_bottom = target_y
                            x_bottom = target_x
                        
                if fixed_block:
                    is_reversed = y_top > y_bottom
                    
                    base_offset = 40
                    lane_width = 40
                    offset = base_offset + current_loop["lane"] * lane_width
                    x_turn = max(x_top, x_bottom) + offset
                    
                    if is_reversed:
                        preview_pen = QPen(QColor(209, 52, 56, 120), 2, Qt.PenStyle.DashLine, Qt.PenCapStyle.RoundCap, Qt.PenJoinStyle.RoundJoin)
                    else:
                        preview_pen = QPen(QColor(0, 120, 212, 120), 2, Qt.PenStyle.DashLine, Qt.PenCapStyle.RoundCap, Qt.PenJoinStyle.RoundJoin)
                    
                    painter.setPen(preview_pen)
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