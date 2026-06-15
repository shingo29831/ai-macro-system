# @role: 記録モード中に常時最前面かつフレームレス（枠なし）で画面上部に表示される、ミニマルなコントロール用ウィジェット画面を制御するビュークラス。
import os
from PySide6.QtWidgets import QWidget, QPushButton, QLabel, QDialog
from PySide6.QtUiTools import QUiLoader
from PySide6.QtCore import QFile, Qt
from PySide6.QtGui import QGuiApplication

class RecordDialog:
    """超小型記録ウィジェットのUI表示、タイマーバインド、およびクローズ動作を制御するクラス"""
    
    def __init__(self, parent=None, on_stop_callback=None):
        self.parent = parent
        self.on_stop_callback = on_stop_callback
        
        self.dialog = self._load_ui_and_style("record_dialog.ui")
        self.dialog.setWindowTitle("記録中")
        self.dialog.setFixedSize(300, 150)
        
        # 【変更点】Qt.Tool を追加し、タスクバーに独立したアイコンを作らず、かつ最前面を強力に維持する
        self.dialog.setWindowFlags(
            Qt.Window | Qt.FramelessWindowHint | Qt.WindowStaysOnTopHint | Qt.Tool
        )
        
        # UI要素の取得
        self.btn_stop = self.dialog.findChild(QPushButton, "btnStopRecord")
        self.lbl_timer = self.dialog.findChild(QLabel, "lblTimer")
        
        # 停止ボタン押下でコールバック発火後にウィジェットを閉じる
        if self.btn_stop:
            self.btn_stop.clicked.connect(self._on_stop_clicked)

    def _on_stop_clicked(self):
        if self.on_stop_callback:
            self.on_stop_callback()
        self.dialog.close()

    def show(self):
        """ウィジェット画面を表示し、強制的に画面上部へ配置する"""
        self.dialog.show()
        
        # PySide6(Qt)の「自動センタリング」を上書きするため、show()の直後に絶対座標へ強制移動する
        screen = QGuiApplication.primaryScreen()
        if screen:
            screen_geo = screen.availableGeometry()
            # 画面幅からウィジェット幅を引き、2で割って中央のX座標を算出
            x = screen_geo.x() + (screen_geo.width() - self.dialog.width()) // 2
            y = screen_geo.y() + 30  # 画面上端から30px下方に配置
            self.dialog.move(x, y)

    def _load_ui_and_style(self, ui_file_name: str) -> QWidget:
        """リソース配下からダイアログ用のUIファイルとCSSを読み込む"""
        base_dir = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
        ui_path = os.path.join(base_dir, "resources", "ui", ui_file_name)
        
        loader = QUiLoader()
        ui_file = QFile(ui_path)
        if not ui_file.open(QFile.ReadOnly):
            raise FileNotFoundError(f"Cannot open UI file: {ui_path}")
            
        # 【重要な変更点】第二引数を self.parent から None に変更し、メイン画面の非表示に巻き込まれないように独立させる
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