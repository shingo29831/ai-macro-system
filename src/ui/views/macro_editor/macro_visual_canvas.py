"""Module: @role: マクロアクションブロックを縦シークエンス状に配置し、実行フローやループ構造の矢印描画、ドラッグ＆ドロップによる並び替えを制御するキャンバス。"""

import copy
from pathlib import Path
from PySide6.QtWidgets import QWidget, QVBoxLayout
from PySide6.QtGui import QPainter, QDropEvent, QDragEnterEvent, QDrag
from PySide6.QtCore import Qt, Signal, QMimeData

from .action_block_widget import ActionBlockWidget
from .canvas_loop_logic import (
    analyze_loops,
    ensure_loop_ids,
    find_default_excel_columns,
    get_action_template,
    resolve_loop_excel_columns,
    validate_loops,
)
from .canvas_painter import hit_test_loop_handle, paint_canvas_flows
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
        # Why: 幅380pxに拡大した詳細設定カードが無理なく収まるよう右余白を460pxへ拡張
        self.main_layout.setContentsMargins(60, 40, 460, 40)
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

            # Why: ループ内外を問わず利用可能なExcel列情報を自動解決して配備
            cols = None
            if is_in_loop:
                for loop in self.loops:
                    if loop.get("start_action_idx", -1) <= i <= loop.get("end_action_idx", -1):
                        cols = resolve_loop_excel_columns(self.commands[loop["loop_start_idx"]], self.workflow_dir)
                        if cols:
                            block.set_available_variables(cols)
                        break

            if not cols:
                default_cols = find_default_excel_columns(self.commands, self.workflow_dir)
                if default_cols:
                    block.set_available_variables(default_cols)

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
            w.settings_changed.connect(lambda idx=loop["loop_start_idx"]: self._on_loop_settings_changed(idx))
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

    def _on_loop_settings_changed(self, loop_start_idx: int):
        # Why: ループ設定変更時にExcel列情報を配下ブロックへ動的再配布
        if 0 <= loop_start_idx < len(self.commands):
            cols = resolve_loop_excel_columns(self.commands[loop_start_idx], self.workflow_dir)
            if cols:
                for loop in self.loops:
                    if loop.get("loop_start_idx") == loop_start_idx:
                        s_idx = loop.get("start_action_idx", -1)
                        e_idx = loop.get("end_action_idx", -1)
                        for block in self.blocks:
                            if s_idx <= block.cmd_index <= e_idx:
                                block.set_available_variables(cols)
                        break
        self.commands_changed.emit()

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

    def _get_drop_target_index(self, drop_y: int) -> int:
        for block in self.blocks:
            if drop_y < block.geometry().center().y():
                return block.cmd_index
        return len(self.commands)

    def _get_template_for_action(self, action_type: str) -> dict:
        return get_action_template(action_type)

    def _validate_loops(self) -> bool:
        return validate_loops(self.commands)

    def _on_delete_requested(self, cmd_index: int):
        if 0 <= cmd_index < len(self.commands):
            backup_commands = copy.deepcopy(self.commands)
            self.commands.pop(cmd_index)
            if not self._validate_loops():
                self.commands = backup_commands
                return
            self.commands_changed.emit()
            self.rebuild()

    def mousePressEvent(self, event):
        hit = hit_test_loop_handle(event.pos(), self.loops, self.get_block_widget)
        if hit:
            handle_type, cmd_idx = hit
            drag = QDrag(self)
            mime_data = QMimeData()
            mime_data.setText(f"loop_handle:{handle_type}:{cmd_idx}")
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
            target_idx = self._get_drop_target_index(event.pos().y())
            parts = mime_text.split(":")
            handle_type = parts[1]
            cmd_idx = int(parts[2])
            if 0 <= cmd_idx < len(self.commands):
                self._drag_loop_info = {
                    "type": handle_type,
                    "loop_id": self.commands[cmd_idx].get("loop_id"),
                    "target_idx": target_idx,
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
        target_idx = self._get_drop_target_index(event.pos().y())
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
                start_cmd = {"method": "loop_start", "loop_id": new_id, "args": {"loop_count": 10, "data_source": "static"}}
                end_cmd = {"method": "loop_end", "loop_id": new_id, "args": {}}
                ins_start = max(0, min(target_idx, len(self.commands)))
                self.commands.insert(ins_start, start_cmd)
                self.commands.append(end_cmd)
            else:
                self.commands.insert(target_idx, copy.deepcopy(self._get_template_for_action(action_type)))
        elif mime_text.startswith("loop_handle:"):
            cmd_idx = int(mime_text.split(":")[2])
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
        ensure_loop_ids(self.commands)

    def _analyze_loops(self):
        self.loops = analyze_loops(self.commands)

    def paintEvent(self, event):
        super().paintEvent(event)
        if not self.blocks:
            return
        painter = QPainter(self)
        painter.setRenderHint(QPainter.RenderHint.Antialiasing)
        paint_canvas_flows(
            painter,
            self.blocks,
            self.loops,
            self.loop_widgets,
            self.warning_widgets,
            getattr(self, "_drag_loop_info", None),
            self.commands,
            self.get_block_widget,
        )
