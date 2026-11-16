"""Module: @role: マクロエディタのキャンバス上でループ回数設定ブロックと逆転警告アイコンを描画・制御するUIウィジェット群。"""

from pathlib import Path
from PySide6.QtCore import Qt, Signal
from PySide6.QtWidgets import (
    QFrame,
    QHBoxLayout,
    QLabel,
    QPushButton,
    QSpinBox,
    QVBoxLayout,
    QWidget,
)

from .excel_column_scanner import get_excel_columns_map
from .loop_config_panel import LoopConfigPanel
from .loop_setting_dialog import LoopSettingDialog
from .loop_warning_widget import WarningWidget

__all__ = [
    "WarningWidget",
    "LoopCountWidget",
    "LoopSettingDialog",
    "get_excel_columns_map",
]


class LoopCountWidget(QFrame):
    count_changed = Signal(int, int)
    settings_changed = Signal()
    delete_requested = Signal(int)

    def __init__(self, loop_start_idx: int, cmd: dict, workflow_dir: Path, parent=None):
        super().__init__(parent)
        self.loop_start_idx = loop_start_idx
        self.cmd = cmd
        self.workflow_dir = workflow_dir
        self.args = cmd.setdefault("args", {})
        self.is_expanded = False

        self.setObjectName("LoopCountWidget")
        self._build_ui()
        self._update_appearance()

    def _build_ui(self):
        self.main_layout = QVBoxLayout(self)
        self.main_layout.setContentsMargins(10, 8, 10, 8)
        self.main_layout.setSpacing(8)

        # 1. サマリー表示バー
        self.bar_layout = QHBoxLayout()
        self.bar_layout.setContentsMargins(0, 0, 0, 0)
        self.bar_layout.setSpacing(6)

        self.icon_label = QLabel()
        self.bar_layout.addWidget(self.icon_label)

        self.summary_label = QLabel()
        self.summary_label.setStyleSheet("font-weight: bold; font-size: 12px; color: #333333;")
        self.bar_layout.addWidget(self.summary_label)

        # Why: 十分な幅と右パディングを確保し上下矢印が数値テキストに重なるのを完全防止
        self.spin_box = QSpinBox()
        self.spin_box.setRange(1, 99999)
        self.spin_box.setValue(int(self.args.get("loop_count") or 10))
        self.spin_box.setFixedWidth(70)
        self.spin_box.setAlignment(Qt.AlignmentFlag.AlignRight | Qt.AlignmentFlag.AlignVCenter)
        self.spin_box.setStyleSheet("""
            QSpinBox {
                border: 1px solid #c7c7c7;
                border-radius: 4px;
                background-color: #ffffff;
                color: #0078d4;
                font-size: 12px;
                font-weight: bold;
                padding-right: 20px;
                padding-left: 4px;
            }
        """)
        self.spin_box.valueChanged.connect(self._on_spin_changed)
        self.bar_layout.addWidget(self.spin_box)

        self.unit_label = QLabel("回")
        self.unit_label.setStyleSheet("font-weight: bold; font-size: 12px; color: #333333;")
        self.bar_layout.addWidget(self.unit_label)

        self.bar_layout.addStretch()

        self.toggle_btn = QPushButton("詳細 ▼")
        self.toggle_btn.setFixedSize(56, 24)
        self.toggle_btn.setCursor(Qt.CursorShape.PointingHandCursor)
        self.toggle_btn.setStyleSheet("""
            QPushButton {
                background: #f3f2f1;
                border: 1px solid #d0d0d0;
                border-radius: 3px;
                font-size: 11px;
                color: #444444;
            }
            QPushButton:hover {
                background: #e1dfdd;
                color: #0078d4;
            }
        """)
        self.toggle_btn.clicked.connect(self._toggle_expand)
        self.bar_layout.addWidget(self.toggle_btn)

        self.del_btn = QPushButton("✕")
        self.del_btn.setFixedSize(22, 22)
        self.del_btn.setToolTip("このループを解除する")
        self.del_btn.setCursor(Qt.CursorShape.PointingHandCursor)
        self.del_btn.setStyleSheet("""
            QPushButton {
                background: transparent;
                border: none;
                color: #888888;
                font-size: 12px;
                font-weight: bold;
            }
            QPushButton:hover {
                color: #d13438;
            }
        """)
        self.del_btn.clicked.connect(lambda: self.delete_requested.emit(self.loop_start_idx))
        self.bar_layout.addWidget(self.del_btn)

        self.main_layout.addLayout(self.bar_layout)

        # 2. インライン詳細設定パネル（展開式）
        self.detail_panel = QWidget()
        detail_container_layout = QVBoxLayout(self.detail_panel)
        detail_container_layout.setContentsMargins(4, 8, 4, 4)
        self.detail_panel.setStyleSheet("""
            QLabel { font-size: 12px; color: #2b2b2b; }
            QLineEdit {
                font-size: 12px; padding: 4px 6px; border: 1px solid #c7c7c7;
                border-radius: 4px; background-color: #ffffff; color: #1f2937;
            }
            QLineEdit:focus { border: 1px solid #0078d4; }
            QSpinBox {
                font-size: 12px; padding: 3px 22px 3px 6px; border: 1px solid #c7c7c7;
                border-radius: 4px; background-color: #ffffff; color: #1f2937;
            }
            QSpinBox:focus { border: 1px solid #0078d4; }
            QComboBox {
                font-size: 12px; padding: 4px 8px; border: 1px solid #c7c7c7;
                border-radius: 4px; background-color: #ffffff; color: #1f2937;
            }
            QComboBox:hover { border: 1px solid #0078d4; }
            QComboBox QAbstractItemView {
                background-color: #ffffff; color: #1f2937;
                selection-background-color: #0078d4; selection-color: #ffffff;
                border: 1px solid #c7c7c7; outline: none;
            }
            QCheckBox { font-size: 12px; color: #2b2b2b; }
            QPushButton {
                font-size: 12px; color: #2b2b2b; background-color: #f3f2f1;
                border: 1px solid #c7c7c7; border-radius: 4px; padding: 3px 8px;
            }
            QPushButton:hover { background-color: #e1dfdd; color: #0078d4; }
        """)
        self.config_panel = LoopConfigPanel(
            self.args,
            self.workflow_dir,
            commit_on_change=True,
            show_static_count=False,
            show_columns_preview=True,
            parent=self.detail_panel,
        )
        self.config_panel.config_changed.connect(self._on_panel_config_changed)
        detail_container_layout.addWidget(self.config_panel)

        self.detail_panel.hide()
        self.main_layout.addWidget(self.detail_panel)

    def _on_panel_config_changed(self) -> None:
        self._update_appearance()
        self.settings_changed.emit()

    def _update_appearance(self):
        is_excel = self.args.get("data_source") == "excel"

        if is_excel:
            self.icon_label.setText("📊")
            f_name = Path(self.args.get("file_path", "")).name if self.args.get("file_path") else "未選択"
            st_col = self.args.get("status_column") or "-"
            self.summary_label.setText(f"{f_name} [{st_col}列]")
            self.summary_label.setStyleSheet("font-weight: bold; font-size: 12px; color: #107c41;")
            self.spin_box.hide()
            self.unit_label.hide()
            self.setStyleSheet("""
                #LoopCountWidget {
                    background-color: #f6fcf8;
                    border: 2px solid #107c41;
                    border-radius: 8px;
                }
            """)
        else:
            self.icon_label.setText("🔁")
            self.summary_label.setText("固定ループ:")
            self.summary_label.setStyleSheet("font-weight: bold; font-size: 12px; color: #0078d4;")
            self.unit_label.setStyleSheet("font-weight: bold; font-size: 12px; color: #333333;")
            self.spin_box.show()
            self.unit_label.show()
            self.setStyleSheet("""
                #LoopCountWidget {
                    background-color: #f7faff;
                    border: 2px solid #0078d4;
                    border-radius: 8px;
                }
            """)
        self.adjustSize()

    def _toggle_expand(self):
        self.is_expanded = not self.is_expanded
        self.detail_panel.setVisible(self.is_expanded)
        self.toggle_btn.setText("閉じる ▲" if self.is_expanded else "詳細 ▼")
        # Why: 展開時幅を380pxに拡張し十分な余白と操作エリアを確保
        self.setFixedWidth(380 if self.is_expanded else 260)
        self.adjustSize()
        self.settings_changed.emit()

    def _on_mode_changed(self, idx):
        mode = "excel" if idx == 1 else "static"
        self.args["data_source"] = mode
        if mode == "excel":
            self._refresh_columns_preview()
        self._update_appearance()
        self.settings_changed.emit()

    def _on_spin_changed(self, val):
        self.args["loop_count"] = val
        self.count_changed.emit(self.loop_start_idx, val)

    def _update_field(self, key, val):
        self.args[key] = val
        self._update_appearance()
        self.settings_changed.emit()

    def _browse_excel_file(self):
        fp, _ = QFileDialog.getOpenFileName(
            self, "Excelファイルを選択", str(self.workflow_dir), "Excel Files (*.xlsx *.xls *.xlsm);;All Files (*.*)"
        )
        if fp:
            self.file_edit.setText(fp)
            self._on_file_changed(fp)
