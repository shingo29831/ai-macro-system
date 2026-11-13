"""Module: @role: ワークフロー内の単一アクションの視認用ブロック表示およびインライン編集フォームのUI制御を担当する。"""

from pathlib import Path
from typing import Any, Dict, Optional

from PySide6.QtCore import QMimeData, Qt, Signal
from PySide6.QtGui import QDrag, QMouseEvent, QPixmap
from PySide6.QtWidgets import QFrame, QHBoxLayout, QLabel, QVBoxLayout, QWidget

from .action_block_form import ActionBlockEditForm
from .action_block_formatter import draw_cursor_on_pixmap, get_action_info_text, get_action_title
from .image_preview_dialog import ImagePreviewDialog


class ActionBlockWidget(QFrame):
    """ワークフロー上の1ステップを表示し、展開編集とドラッグ並び替えを提供するブロックウィジェット。"""

    delete_requested = Signal(int)
    content_changed = Signal()

    def __init__(
        self,
        command: Dict[str, Any],
        cmd_index: int,
        workflow_dir: Path,
        is_in_loop: bool = False,
        parent: Optional[QWidget] = None,
    ) -> None:
        super().__init__(parent)
        self.command = command
        self.cmd_index = cmd_index
        self.workflow_dir = workflow_dir
        self.is_in_loop = is_in_loop

        self.method = command.get("method", "")
        self.args = command.get("args", {})

        self.is_expanded = False
        self.drag_start_pos = None
        self.available_vars: Dict[str, Any] = {}

        self.setObjectName("ActionBlock")
        self.setStyleSheet("""
            #ActionBlock {
                background-color: #ffffff;
                border: 1px solid #e0e0e0;
                border-radius: 8px;
            }
            #ActionBlock:hover {
                border: 1px solid #0078d4;
            }
        """)
        self.setFixedWidth(480)
        self._build_ui()

    def _build_ui(self) -> None:
        self.main_layout = QVBoxLayout(self)
        self.main_layout.setContentsMargins(16, 16, 16, 16)
        self.main_layout.setSpacing(12)

        header_layout = QHBoxLayout()
        title_label = QLabel(get_action_title(self.method, self.args))
        font = title_label.font()
        font.setBold(True)
        font.setPointSize(14)
        title_label.setFont(font)
        title_label.setStyleSheet("color: #333333;")
        header_layout.addWidget(title_label)
        header_layout.addStretch()
        self.main_layout.addLayout(header_layout)

        raw_event_id = self.args.get("raw_event_id")
        if raw_event_id:
            img_path = self.workflow_dir / "images" / f"{raw_event_id}_pre.png"
            if img_path.exists():
                self.img_label = QLabel()
                self.img_label.setAlignment(Qt.AlignmentFlag.AlignCenter)
                self.img_label.setCursor(Qt.CursorShape.PointingHandCursor)

                pixmap = QPixmap(str(img_path))
                if self.method in ("click", "move") and "x" in self.args and "y" in self.args:
                    pixmap = draw_cursor_on_pixmap(pixmap, self.args["x"], self.args["y"])

                self._original_pixmap = pixmap
                scaled_pixmap = pixmap.scaled(
                    448,
                    280,
                    Qt.AspectRatioMode.KeepAspectRatio,
                    Qt.TransformationMode.SmoothTransformation,
                )
                self.img_label.setPixmap(scaled_pixmap)
                self.img_label.setStyleSheet("border: 1px solid #e0e0e0; border-radius: 4px;")
                self.main_layout.addWidget(self.img_label)

        self.info_label = QLabel(get_action_info_text(self.method, self.args))
        self.info_label.setWordWrap(True)
        self.info_label.setStyleSheet(
            "color: #555555; background-color: #f3f2f1; padding: 8px; border-radius: 4px; font-size: 13px;"
        )
        if not self.info_label.text():
            self.info_label.hide()
        self.main_layout.addWidget(self.info_label)

        self.edit_container = ActionBlockEditForm(
            method=self.method,
            args=self.args,
            workflow_dir=self.workflow_dir,
            is_in_loop=self.is_in_loop,
            parent=self,
        )
        self.edit_container.arg_updated.connect(self._on_form_updated)
        self.edit_container.delete_clicked.connect(lambda: self.delete_requested.emit(self.cmd_index))
        self.edit_container.variables_loaded.connect(self._on_variables_loaded)
        self.edit_container.hide()
        self.main_layout.addWidget(self.edit_container)

    def _on_form_updated(self) -> None:
        info_text = get_action_info_text(self.method, self.args)
        self.info_label.setText(info_text)
        self.info_label.setVisible(bool(info_text))
        self.content_changed.emit()

    def _on_variables_loaded(self, cols_map: Dict[str, Any]) -> None:
        self.available_vars = cols_map or {}

    def set_available_variables(self, var_map: Dict[str, Any]) -> None:
        """キャンバス等から渡された列変数マップを編集フォームへ反映する。"""
        self.available_vars = var_map or {}
        self.edit_container.set_available_variables(self.available_vars)

    def mousePressEvent(self, event: QMouseEvent) -> None:
        if event.button() == Qt.MouseButton.LeftButton:
            self.drag_start_pos = event.pos()
        super().mousePressEvent(event)

    def mouseMoveEvent(self, event: QMouseEvent) -> None:
        if not (event.buttons() & Qt.MouseButton.LeftButton) or not self.drag_start_pos:
            return
        if (event.pos() - self.drag_start_pos).manhattanLength() > 10:
            drag = QDrag(self)
            mime_data = QMimeData()
            mime_data.setText(f"action_block:{self.cmd_index}")
            drag.setMimeData(mime_data)
            drag.exec_(Qt.DropAction.MoveAction)
        super().mouseMoveEvent(event)

    def mouseReleaseEvent(self, event: QMouseEvent) -> None:
        if event.button() == Qt.MouseButton.LeftButton and self.drag_start_pos:
            if (event.pos() - self.drag_start_pos).manhattanLength() <= 10:
                if hasattr(self, "img_label") and self.img_label.geometry().contains(event.pos()):
                    self._show_image_preview()
                else:
                    self.is_expanded = not self.is_expanded
                    self.edit_container.setVisible(self.is_expanded)
        self.drag_start_pos = None
        super().mouseReleaseEvent(event)

    def _show_image_preview(self) -> None:
        if hasattr(self, "_original_pixmap"):
            dialog = ImagePreviewDialog(self._original_pixmap, self)
            dialog.exec()
