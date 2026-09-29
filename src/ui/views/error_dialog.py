"""Module: @role: エラー内容の詳細表示およびクリップボードコピーを提供するダイアログ。"""

from PySide6.QtCore import Qt
from PySide6.QtGui import QFont, QGuiApplication
from PySide6.QtWidgets import (
    QDialog,
    QHBoxLayout,
    QLabel,
    QPlainTextEdit,
    QPushButton,
    QVBoxLayout,
)


class ErrorDialog(QDialog):
    """エラー詳細の表示とクリップボードコピーを支援するダイアログ"""

    def __init__(self, title: str, summary: str, detail: str, parent=None):
        super().__init__(parent)
        self.setWindowTitle(title)
        self.setMinimumSize(560, 380)
        self.resize(600, 420)

        self.detail_text = detail

        layout = QVBoxLayout(self)
        layout.setContentsMargins(20, 20, 20, 20)
        layout.setSpacing(12)

        lbl_summary = QLabel(summary, self)
        lbl_summary.setWordWrap(True)
        summary_font = QFont("Yu Gothic UI", 11)
        summary_font.setBold(True)
        lbl_summary.setFont(summary_font)
        lbl_summary.setStyleSheet("color: #dc2626;")
        layout.addWidget(lbl_summary)

        lbl_detail_guide = QLabel("エラー詳細 / スタックトレース:", self)
        lbl_detail_guide.setStyleSheet("color: #4b5563; font-size: 11px;")
        layout.addWidget(lbl_detail_guide)

        self.txt_detail = QPlainTextEdit(self)
        self.txt_detail.setReadOnly(True)
        self.txt_detail.setPlainText(detail)
        code_font = QFont("Consolas", 10)
        code_font.setStyleHint(QFont.StyleHint.Monospace)
        self.txt_detail.setFont(code_font)
        self.txt_detail.setStyleSheet("""
            QPlainTextEdit {
                background-color: #f8fafc;
                border: 1px solid #cbd5e1;
                border-radius: 6px;
                padding: 8px;
                color: #1e293b;
            }
        """)
        layout.addWidget(self.txt_detail, 1)

        btn_layout = QHBoxLayout()
        btn_layout.setSpacing(10)

        self.btn_copy = QPushButton("📋 エラー内容をコピー", self)
        self.btn_copy.setStyleSheet("""
            QPushButton {
                background-color: #2563eb;
                color: white;
                font-weight: bold;
                border-radius: 4px;
                padding: 6px 14px;
            }
            QPushButton:hover {
                background-color: #1d4ed8;
            }
        """)
        self.btn_copy.clicked.connect(self._copy_to_clipboard)

        self.btn_close = QPushButton("閉じる", self)
        self.btn_close.setStyleSheet("""
            QPushButton {
                background-color: #e2e8f0;
                color: #334155;
                font-weight: bold;
                border-radius: 4px;
                padding: 6px 14px;
            }
            QPushButton:hover {
                background-color: #cbd5e1;
            }
        """)
        self.btn_close.clicked.connect(self.accept)

        btn_layout.addStretch()
        btn_layout.addWidget(self.btn_copy)
        btn_layout.addWidget(self.btn_close)

        layout.addLayout(btn_layout)

    def _copy_to_clipboard(self):
        clipboard = QGuiApplication.clipboard()
        clipboard.setText(self.detail_text)
        self.btn_copy.setText("✓ コピーしました！")
        self.btn_copy.setStyleSheet("""
            QPushButton {
                background-color: #16a34a;
                color: white;
                font-weight: bold;
                border-radius: 4px;
                padding: 6px 14px;
            }
        """)
