# src/ui/views/running_dialog.py
# @role: 実行モード中に常時最前面かつフレームレス（枠なし）で画面上部に表示される、キルスイッチコントロール用ウィジェット画面を制御するビュークラス。
import os
from PySide6.QtWidgets import QWidget, QPushButton, QLabel, QDialog
from PySide6.QtUiTools import QUiLoader
from PySide6.QtCore import QFile, Qt
from PySide6.QtGui import QGuiApplication

class RunningDialog:
    """超小型実行ウィジェットのUI表示および強制クローズ動作を制御するクラス"""
    
    _instance = None # UI状態動的更新のためのシングルトン参照
    
    def __init__(self, parent=None, on_stop_callback=None):
        RunningDialog._instance = self
        self.parent = parent
        self.on_stop_callback = on_stop_callback
        
        self.dialog = self._load_ui_and_style("running_dialog.ui")
        self.dialog.setWindowTitle("実行中")
        
        self.dialog.setFixedSize(460, 40)
        
        self.dialog.setWindowFlags(
            Qt.Window | Qt.FramelessWindowHint | Qt.WindowStaysOnTopHint | Qt.Tool
        )
        self.dialog.setAttribute(Qt.WA_TranslucentBackground)
        
        self.btn_stop = self.dialog.findChild(QPushButton, "btnEmergencyStop")
        
        if self.btn_stop:
            self.btn_stop.clicked.connect(self._on_stop_clicked)

        self._replace_shortcut_text()

    @classmethod
    def set_status(cls, text: str, is_healing: bool):
        """外部スレッドからの通知を受け取り、インスタンスのUIを更新する"""
        if cls._instance and cls._instance.dialog.isVisible():
            cls._instance.update_status(text, is_healing)

    def update_status(self, text: str, is_healing: bool = False):
        """実行中のステータス（自己修復中など）をUIの停止ボタンに反映する"""
        if self.btn_stop:
            base_text = "緊急停止 (Ctrl + \\)"
            if is_healing:
                self.btn_stop.setText(f"【{text}】 {base_text}")
                self.btn_stop.setStyleSheet("background-color: #f59e0b; color: white; border: 2px solid #b45309;")
            else:
                self.btn_stop.setText(base_text)
                self.btn_stop.setStyleSheet("") # CSS設定に戻す

    def _replace_shortcut_text(self):
        widgets = self.dialog.findChildren(QLabel) + self.dialog.findChildren(QPushButton)
        for widget in widgets:
            text = widget.text()
            if text:
                new_text = text.replace("￥", "Ctrl + \\").replace("¥", "Ctrl + \\").replace("Esc", "Ctrl + \\")
                if new_text != text:
                    widget.setText(new_text)

    def _on_stop_clicked(self):
        # 背景: 一度押されたらボタンを消し、「停止中です。」のラベルを表示してUIレベルでの連打を物理的に防ぐ
        if self.btn_stop:
            self.btn_stop.hide()
            
            if not hasattr(self, 'stopping_label'):
                self.stopping_label = QLabel("停止中です...", self.dialog)
                self.stopping_label.setStyleSheet("color: white; font-weight: bold; font-size: 14px; background-color: #ef4444; border-radius: 4px;")
                self.stopping_label.setAlignment(Qt.AlignCenter)
                self.stopping_label.setGeometry(self.btn_stop.geometry())
                self.stopping_label.show()

        if self.on_stop_callback:
            self.on_stop_callback()
            
        # 背景: 非同期で停止処理を行うため、即座には閉じず「停止中です」の表示を残す。
        # (処理が完全に完了した際にメインウィンドウ側から閉じられる想定です)

    def show(self):
        self.dialog.show()
        
        screen = QGuiApplication.primaryScreen()
        if screen:
            screen_geo = screen.availableGeometry()
            x = screen_geo.x() + (screen_geo.width() - self.dialog.width()) // 2
            y = screen_geo.y()
            self.dialog.move(x, y)

    def close_dialog(self):
        self.dialog.close()

    def _load_ui_and_style(self, ui_file_name: str) -> QWidget:
        base_dir = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
        ui_path = os.path.join(base_dir, "resources", "ui", ui_file_name)
        
        loader = QUiLoader()
        ui_file = QFile(ui_path)
        if not ui_file.open(QFile.ReadOnly):
            raise FileNotFoundError(f"Cannot open UI file: {ui_path}")
            
        widget = loader.load(ui_file, None)
        ui_file.close()
        
        if widget is None:
            raise RuntimeError(f"Failed to load UI file: {ui_path}")
        
        css_name = os.path.splitext(ui_file_name)[0] + ".css"
        css_path = os.path.join(base_dir, "resources", "css", css_name)
        
        if os.path.exists(css_path):
            with open(css_path, "r", encoding="utf-8") as f:
                stylesheet = f.read()
                widget.setStyleSheet(stylesheet)
                
        return widget