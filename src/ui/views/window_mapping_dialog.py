"""Module: @role: マクロ実行前に対象ウィンドウを現在開いているウィンドウ（または新規起動）へ紐付けるマッピングダイアログおよびサムネイルウィジェット。"""

from PySide6.QtCore import Qt, Signal
from PySide6.QtGui import QImage, QPixmap
from PySide6.QtWidgets import (
    QDialog,
    QFrame,
    QGridLayout,
    QHBoxLayout,
    QLabel,
    QScrollArea,
    QVBoxLayout,
    QWidget,
)
from qfluentwidgets import BodyLabel, PrimaryPushButton, PushButton, SubtitleLabel

from core.executor.window_manager import get_open_windows_info


class WindowThumbnailWidget(QFrame):
    clicked = Signal(int)

    def __init__(self, hwnd, title, pixmap, parent=None):
        super().__init__(parent)
        self.hwnd = hwnd
        self.title = title
        self.is_selected = False
        self.is_confirmed = False

        self.setFixedSize(220, 220)

        layout = QVBoxLayout(self)
        layout.setContentsMargins(10, 10, 10, 10)

        self.title_label = QLabel(title)
        self.title_label.setAlignment(Qt.AlignCenter)
        self.title_label.setWordWrap(True)
        self.title_label.setFixedHeight(40)

        self.thumb_label = QLabel()
        self.thumb_label.setAlignment(Qt.AlignCenter)
        if pixmap:
            self.thumb_label.setPixmap(pixmap.scaled(200, 150, Qt.KeepAspectRatio, Qt.SmoothTransformation))
        else:
            self.thumb_label.setText("No Image")
            self.thumb_label.setStyleSheet("background-color: #f0f0f0; color: #888;")
        self.thumb_label.setFixedSize(200, 150)

        layout.addWidget(self.title_label)
        layout.addWidget(self.thumb_label)

        self.update_style()

    def set_selected(self, selected):
        if self.is_confirmed:
            return
        self.is_selected = selected
        self.update_style()

    def set_confirmed(self, confirmed):
        self.is_confirmed = confirmed
        self.update_style()

    def update_style(self):
        if self.is_confirmed:
            self.setStyleSheet("""
                WindowThumbnailWidget {
                    background-color: #f0f0f0;
                    border: 2px solid #888888;
                    border-radius: 8px;
                }
            """)
            self.title_label.setStyleSheet("color: #888888;")
        elif self.is_selected:
            self.setStyleSheet("""
                WindowThumbnailWidget {
                    background-color: #e6f2ff;
                    border: 4px solid #0066cc;
                    border-radius: 8px;
                }
            """)
            self.title_label.setStyleSheet("color: black; font-weight: bold;")
        else:
            self.setStyleSheet("""
                WindowThumbnailWidget {
                    background-color: white;
                    border: 1px solid #cccccc;
                    border-radius: 8px;
                }
                WindowThumbnailWidget:hover {
                    background-color: #f5f5f5;
                    border: 2px solid #999999;
                }
            """)
            self.title_label.setStyleSheet("color: black;")

    def mousePressEvent(self, event):
        if event.button() == Qt.LeftButton and not self.is_confirmed:
            self.clicked.emit(self.hwnd)


class WindowMappingDialog(QDialog):
    def __init__(self, target_titles, parent=None):
        super().__init__(parent)
        self.setWindowTitle("ウィンドウの紐付け")
        self.resize(800, 600)
        self.setStyleSheet("""
            QDialog { background-color: white; }
            QLabel { color: black; }
        """)
        self.target_titles = target_titles
        self.current_title_index = 0
        self.mapping = {}
        self.confirmed_hwnds = set()
        self.current_selected_hwnd = None

        ignored_system_kw = [
            "マクロ生成中", "aiマクロ生成中", "ai macro system", "記録中", "停止中",
            "実行中", "設定", "ウィンドウの紐付け", "program manager", "taskbar"
        ]
        filtered_open_windows = [
            w for w in get_open_windows_info()
            if not any(kw in w.get("title", "").lower() for kw in ignored_system_kw)
        ]
        self.all_windows = [{"hwnd": -1, "title": "新規起動", "thumbnail": None}] + filtered_open_windows

        layout = QVBoxLayout(self)

        self.progress_label = SubtitleLabel("")
        self.instruction_label = BodyLabel("")
        layout.addWidget(self.progress_label)
        layout.addWidget(self.instruction_label)

        self.scroll = QScrollArea()
        self.scroll.setWidgetResizable(True)
        self.scroll.setStyleSheet("QScrollArea { border: none; background-color: transparent; }")

        self.scroll_widget = QWidget()
        self.scroll_widget.setStyleSheet("QWidget { background-color: transparent; }")

        self.grid_layout = QGridLayout(self.scroll_widget)
        self.grid_layout.setSpacing(15)

        self.scroll.setWidget(self.scroll_widget)
        layout.addWidget(self.scroll)

        btn_layout = QHBoxLayout()
        self.cancel_btn = PushButton("キャンセル")
        self.cancel_btn.clicked.connect(self.reject)

        self.ok_btn = PrimaryPushButton("確定")
        self.ok_btn.clicked.connect(self.on_confirm_clicked)
        self.ok_btn.setEnabled(False)

        btn_layout.addStretch()
        btn_layout.addWidget(self.cancel_btn)
        btn_layout.addWidget(self.ok_btn)
        layout.addLayout(btn_layout)

        self.update_ui_state()

    def sort_windows(self, target_title):
        app_name = target_title.split("—")[-1].split("-")[-1].strip().lower()
        target_lower = target_title.lower()

        def score(win):
            if win["hwnd"] == -1:
                return -100

            s = 0
            if win["hwnd"] in self.confirmed_hwnds:
                s += 1000

            win_title_lower = win["title"].lower()
            if target_lower == win_title_lower:
                s -= 50
            elif target_lower in win_title_lower or win_title_lower in target_lower:
                s -= 40
            elif app_name and app_name in win_title_lower:
                s -= 30

            return s

        return sorted(self.all_windows, key=score)

    def update_ui_state(self):
        target_title = self.target_titles[self.current_title_index]
        self.progress_label.setText(f"ウィンドウの紐付け ({self.current_title_index + 1}/{len(self.target_titles)})")
        self.instruction_label.setText(f"「{target_title}」に紐付けるウィンドウを選択してください。")

        if self.current_title_index == len(self.target_titles) - 1:
            self.ok_btn.setText("完了")
        else:
            self.ok_btn.setText("確定")

        for i in reversed(range(self.grid_layout.count())):
            widgetToRemove = self.grid_layout.itemAt(i).widget()
            self.grid_layout.removeWidget(widgetToRemove)
            widgetToRemove.setParent(None)

        self.thumbnail_widgets = []

        sorted_windows = self.sort_windows(target_title)

        cols = 3
        for i, win in enumerate(sorted_windows):
            pixmap = None
            if win.get("thumbnail"):
                img = win["thumbnail"]
                data = img.tobytes("raw", "RGB")
                qimg = QImage(data, img.width, img.height, QImage.Format_RGB888)
                pixmap = QPixmap.fromImage(qimg)

            widget = WindowThumbnailWidget(win["hwnd"], win["title"], pixmap)
            if win["hwnd"] in self.confirmed_hwnds:
                widget.set_confirmed(True)

            self.thumbnail_widgets.append(widget)
            row = i // cols
            col = i % cols
            self.grid_layout.addWidget(widget, row, col)
            widget.clicked.connect(self.on_thumbnail_clicked)

        self.auto_select_best_match()

    def auto_select_best_match(self):
        target_title = self.target_titles[self.current_title_index]
        app_name = target_title.split("—")[-1].split("-")[-1].strip().lower()
        target_lower = target_title.lower()

        best_widget = None
        best_score = 999

        for widget in self.thumbnail_widgets:
            if widget.is_confirmed:
                continue

            if widget.hwnd == -1:
                score = 0
            else:
                win_title_lower = widget.title.lower()
                score = 10
                if target_lower == win_title_lower:
                    score = -50
                elif target_lower in win_title_lower or win_title_lower in target_lower:
                    score = -40
                elif app_name and app_name in win_title_lower:
                    score = -30

            if score < best_score:
                best_score = score
                best_widget = widget

        if best_widget:
            self.on_thumbnail_clicked(best_widget.hwnd)

    def on_thumbnail_clicked(self, hwnd):
        self.current_selected_hwnd = hwnd
        for widget in self.thumbnail_widgets:
            if not widget.is_confirmed:
                widget.set_selected(widget.hwnd == hwnd)
        self.ok_btn.setEnabled(True)

    def on_confirm_clicked(self):
        if self.current_selected_hwnd is None:
            return

        current_title = self.target_titles[self.current_title_index]
        self.mapping[current_title] = self.current_selected_hwnd

        if self.current_selected_hwnd != -1:
            self.confirmed_hwnds.add(self.current_selected_hwnd)

        self.current_title_index += 1

        if self.current_title_index >= len(self.target_titles):
            self.accept()
        else:
            self.current_selected_hwnd = None
            self.ok_btn.setEnabled(False)
            self.update_ui_state()

    def get_mapping(self):
        return self.mapping
