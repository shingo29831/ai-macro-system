# @role: 記録モード中に常時最前面かつフレームレス（枠なし）で画面上部に表示される、ミニマルなコントロール用ウィジェット画面を制御するビュークラス。
import os
from PySide6.QtWidgets import QWidget, QPushButton, QLabel, QDialog
from PySide6.QtUiTools import QUiLoader
from PySide6.QtCore import QFile, Qt
from PySide6.QtGui import QGuiApplication
from core.recorder.state import state
from core.recorder.utils import now_datetime
from core.recorder.log_builder import build_base_log
from core.recorder import process_monitor

class RecordDialog:
    """超小型記録ウィジェットのUI表示、タイマーバインド、およびクローズ動作を制御するクラス"""
    
    def __init__(self, parent=None, on_stop_callback=None):
        self.parent = parent
        self.on_stop_callback = on_stop_callback
        
        self.dialog = self._load_ui_and_style("record_dialog.ui")
        self.dialog.setWindowTitle("記録中")
        
        # 横長薄型バーの形状へサイズを固定 (ボタン追加のため幅を460から560へ拡大)
        self.dialog.setFixedSize(560, 40)
        
        # Qt.Toolフラグによりタスクバーへの露出を防ぎ、StaysOnTopHintで常にデスクトップの最前面へ張り付ける
        self.dialog.setWindowFlags(
            Qt.Window | Qt.FramelessWindowHint | Qt.WindowStaysOnTopHint | Qt.Tool
        )
        
        # CSS側のrgba定義と連動させて、ウィンドウ自体のアルファ透過レイヤーを有効化
        self.dialog.setAttribute(Qt.WA_TranslucentBackground)
        
        # UI要素の取得
        self.btn_stop = self.dialog.findChild(QPushButton, "btnStopRecord")
        self.btn_loop = self.dialog.findChild(QPushButton, "btnLoopToggle")  # 追加
        self.lbl_timer = self.dialog.findChild(QLabel, "lblTimer")
        
        # 停止ボタン押下でコールバック発火後にウィジェットを閉じる
        if self.btn_stop:
            self.btn_stop.clicked.connect(self._on_stop_clicked)
            
        # 繰り返しボタンのコールバック登録
        if self.btn_loop:
            self.btn_loop.clicked.connect(self._on_loop_toggle_clicked)
            
        self._replace_shortcut_text()

    def _on_loop_toggle_clicked(self):
        """繰り返し作業の開始・終了を切り替え、メタログを記録する"""
        state.is_loop_recording = not state.is_loop_recording
        
        event_no = state.get_next_event_no()
        dt = now_datetime()
        window_info = process_monitor.get_foreground_window_info()
        
        if state.is_loop_recording:
            # 繰り返し記録中の強調表示
            self.btn_loop.setText("⏹ 繰り返し終了")
            self.btn_loop.setStyleSheet("background-color: #d97706; color: #ffffff;")
            input_type = "meta_loop_start"
        else:
            # 元の待機状態へ戻す
            self.btn_loop.setText("🔁 繰り返し作業")
            self.btn_loop.setStyleSheet("") 
            input_type = "meta_loop_end"
            
        log = build_base_log(
            event_no=event_no, 
            dt=dt, 
            input_type=input_type, 
            content={"status": "active" if state.is_loop_recording else "inactive"}, 
            window_info=window_info
        )
        state.append_log(log)

    def _replace_shortcut_text(self):
        """UIに表示されているキーボードショートカットの文字を動的に上書きする"""
        # PySide6の仕様に合わせ、QLabelとQPushButtonを個別に取得して結合する
        widgets = self.dialog.findChildren(QLabel) + self.dialog.findChildren(QPushButton)
        for widget in widgets:
            text = widget.text()
            if text:
                new_text = text.replace("￥", "Ctrl + \\").replace("¥", "Ctrl + \\").replace("Esc", "Ctrl + \\")
                if new_text != text:
                    widget.setText(new_text)

    def _on_stop_clicked(self):
        if self.on_stop_callback:
            self.on_stop_callback()
        self.dialog.close()

    def show(self):
        """ウィジェット画面を表示し、強制的に画面の最上端へ配置する"""
        self.dialog.show()
        
        # 画面中央の最上部にぴったりと吸着させるための座標計算
        screen = QGuiApplication.primaryScreen()
        if screen:
            screen_geo = screen.availableGeometry()
            x = screen_geo.x() + (screen_geo.width() - self.dialog.width()) // 2
            y = screen_geo.y()  # ディスプレイの一番上の位置（y=0）へジャスト配置
            self.dialog.move(x, y)

    def _load_ui_and_style(self, ui_file_name: str) -> QWidget:
        """リソース配下からダイアログ用のUIファイルとCSSを読み込む"""
        base_dir = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
        ui_path = os.path.join(base_dir, "resources", "ui", ui_file_name)
        
        loader = QUiLoader()
        ui_file = QFile(ui_path)
        if not ui_file.open(QFile.ReadOnly):
            raise FileNotFoundError(f"Cannot open UI file: {ui_path}")
            
        # メインウィンドウの非表示処理に連動して消滅しないよう、親参照を断ち切り独立ウィンドウ化
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