"""Module: @role: マクロエディタのループ種別（固定回数・Excelデータ連携）を設定するモーダルダイアログ。"""

from pathlib import Path
from typing import Any, Dict, Optional

from PySide6.QtWidgets import QDialog, QHBoxLayout, QPushButton, QVBoxLayout, QWidget

from .loop_config_panel import LoopConfigPanel


class LoopSettingDialog(QDialog):
    def __init__(self, cmd: Dict[str, Any], workflow_dir: Path, parent: Optional[QWidget] = None) -> None:
        super().__init__(parent)
        self.cmd = cmd
        self.workflow_dir = workflow_dir
        self.args: Dict[str, Any] = cmd.setdefault("args", {})
        self.setWindowTitle("ループ設定")
        self.setMinimumWidth(440)
        self.setStyleSheet("""
            QDialog { background-color: #ffffff; }
            QLabel { color: #333333; font-size: 13px; }
            QLineEdit, QSpinBox, QComboBox { padding: 4px; border: 1px solid #d0d0d0; border-radius: 4px; font-size: 13px; }
        """)

        layout = QVBoxLayout(self)
        layout.setContentsMargins(20, 20, 20, 20)
        layout.setSpacing(14)

        self.config_panel = LoopConfigPanel(
            self.args,
            self.workflow_dir,
            commit_on_change=False,
            show_static_count=True,
            show_columns_preview=True,
            parent=self,
        )
        layout.addWidget(self.config_panel)

        btn_box = QHBoxLayout()
        btn_box.addStretch()
        cancel_btn = QPushButton("キャンセル")
        cancel_btn.setStyleSheet("padding: 6px 14px; border: 1px solid #d0d0d0; border-radius: 4px;")
        cancel_btn.clicked.connect(self.reject)
        save_btn = QPushButton("決定")
        save_btn.setStyleSheet(
            "background-color: #0078d4; color: white; font-weight: bold; padding: 6px 18px; border-radius: 4px;"
        )
        save_btn.clicked.connect(self._save)
        btn_box.addWidget(cancel_btn)
        btn_box.addWidget(save_btn)
        layout.addLayout(btn_box)

    def _save(self) -> None:
        self.config_panel.apply_to_args()
        self.accept()
