# @role: 実行モード中に常時最前面かつフレームレス（枠なし）で画面上部に表示される、キルスイッチコントロール用ウィジェット画面を制御するビュークラス。
import os
from PySide6.QtWidgets import QWidget, QPushButton, QLabel, QDialog
from PySide6.QtUiTools import QUiLoader
from PySide6.QtCore import QFile, Qt
from PySide6.QtGui import QGuiApplication

class RunningDialog:
    """超小型実行ウィジェットのUI表示および強制クローズ動作を制御するクラス"""
    
    def __init__(self, parent=None, on_stop_callback=None):
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

    def _on_stop_clicked(self):
        if self.on_stop_callback:
            self.on_stop_callback()
        self.close_dialog()

    def show(self):
        self.dialog.show()
        
        screen = QGuiApplication.primaryScreen()
        if screen:
            screen_geo = screen.availableGeometry()
            x = screen_geo.x() + (screen_geo.width() - self.dialog.width()) // 2
            y = screen_geo.y()
            self.dialog.move(x, y)

    def close_dialog(self):
        """外部（ViewModel）から正常終了時にダイアログを閉じるためのメソッド"""
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