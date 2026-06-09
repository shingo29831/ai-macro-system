# @role: 記録モード中に常時最前面に表示される、ミニマルなコントロール用ウィジェット画面を制御するビュークラス。
import os
from PySide6.QtWidgets import QWidget, QPushButton, QLabel
from PySide6.QtUiTools import QUiLoader
from PySide6.QtCore import QFile

class RecordDialog:
    """超小型記録ウィジェットのUI表示、タイマーバインド、およびクローズ動作を制御するクラス"""
    
    def __init__(self, parent=None):
        self.parent = parent
        self.dialog = self._load_ui_and_style("record_dialog.ui")
        self.dialog.setWindowTitle("記録中")
        self.dialog.setFixedSize(300, 150)
        
        # UI要素の取得
        self.btn_stop = self.dialog.findChild(QPushButton, "btnStopRecord")
        self.lbl_timer = self.dialog.findChild(QLabel, "lblTimer")
        
        # 停止ボタン押下でウィジェットを閉じるシグナルをバインド
        if self.btn_stop:
            self.btn_stop.clicked.connect(self.dialog.close)

    def show(self):
        """ウィジェット画面を表示する"""
        self.dialog.show()

    def _load_ui_and_style(self, ui_file_name: str) -> QWidget:
        """リソース配下からダイアログ用のUIファイルとCSSを読み込む"""
        base_dir = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
        ui_path = os.path.join(base_dir, "resources", "ui", ui_file_name)
        
        loader = QUiLoader()
        ui_file = QFile(ui_path)
        if not ui_file.open(QFile.ReadOnly):
            raise FileNotFoundError(f"Cannot open UI file: {ui_path}")
            
        widget = loader.load(ui_file, self.parent)
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