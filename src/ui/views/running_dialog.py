# src/ui/views/running_dialog.py
# @role: 実行モード中に常時最前面かつフレームレス（枠なし）で画面上部に表示される、キルスイッチコントロール用ウィジェット画面を制御するビュークラス。

from PySide6.QtWidgets import QWidget, QPushButton, QLabel, QVBoxLayout, QFrame
from PySide6.QtCore import Qt
from PySide6.QtGui import QGuiApplication

class RunningDialog:
    """超小型実行ウィジェットのUI表示および強制クローズ動作を制御するクラス"""
    
    _instance = None # UI状態動的更新のためのシングルトン参照
    
    def __init__(self, parent=None, on_stop_callback=None):
        RunningDialog._instance = self
        self.parent = parent
        self.on_stop_callback = on_stop_callback
        
        self.dialog = QWidget()
        self.dialog.setWindowTitle("実行中")
        
        # メッセージ表示用に縦幅をさらに拡張（複数行対応）
        self.dialog.setFixedSize(460, 110)
        
        self.dialog.setWindowFlags(
            Qt.Window | Qt.FramelessWindowHint | Qt.WindowStaysOnTopHint | Qt.Tool
        )
        self.dialog.setAttribute(Qt.WA_TranslucentBackground)
        
        layout = QVBoxLayout(self.dialog)
        layout.setContentsMargins(10, 10, 10, 10)
        layout.setSpacing(5)
        
        # 背景用のフレーム（角丸半透明黒）
        self.frame = QFrame(self.dialog)
        self.frame.setStyleSheet("QFrame { background-color: rgba(30, 30, 30, 200); border-radius: 8px; }")
        frame_layout = QVBoxLayout(self.frame)
        frame_layout.setContentsMargins(10, 10, 10, 10)
        frame_layout.setSpacing(5)
        
        # 停止ボタン外のメッセージ表示用ラベル
        self.lbl_status = QLabel("実行中...", self.frame)
        self.lbl_status.setStyleSheet("color: white; font-size: 12px;")
        self.lbl_status.setAlignment(Qt.AlignCenter)
        self.lbl_status.setWordWrap(True)
        
        self.btn_stop = QPushButton("緊急停止 (Ctrl + \\)", self.frame)
        self.btn_stop.setStyleSheet("""
            QPushButton {
                background-color: #ef4444;
                color: white;
                font-weight: bold;
                font-size: 14px;
                border-radius: 4px;
                padding: 5px;
            }
            QPushButton:hover {
                background-color: #dc2626;
            }
        """)
        
        frame_layout.addWidget(self.lbl_status)
        frame_layout.addWidget(self.btn_stop)
        
        layout.addWidget(self.frame)
        
        if self.btn_stop:
            self.btn_stop.clicked.connect(self._on_stop_clicked)

    @classmethod
    def set_status(cls, text: str, is_warning: bool):
        """外部スレッドからの通知を受け取り、インスタンスのUIを更新する"""
        if cls._instance and cls._instance.dialog.isVisible():
            cls._instance.update_status(text, is_warning)

    def update_status(self, text: str, is_warning: bool = False):
        """実行中のステータス（検証スコアや自己修復中など）をUIのラベルに反映する"""
        if hasattr(self, 'lbl_status') and self.lbl_status:
            self.lbl_status.setText(text)
            if is_warning:
                self.lbl_status.setStyleSheet("color: #fca5a5; font-size: 12px; font-weight: bold;")
            else:
                self.lbl_status.setStyleSheet("color: white; font-size: 12px;")

    def _on_stop_clicked(self):
        # 背景: 一度押されたらボタンを消し、「停止中です。」のラベルを表示してUIレベルでの連打を物理的に防ぐ
        if self.btn_stop:
            self.btn_stop.hide()
            
            if not hasattr(self, 'stopping_label'):
                self.stopping_label = QLabel("停止中です...", self.frame)
                self.stopping_label.setStyleSheet("color: white; font-weight: bold; font-size: 14px; background-color: #ef4444; border-radius: 4px; padding: 5px;")
                self.stopping_label.setAlignment(Qt.AlignCenter)
                self.frame.layout().addWidget(self.stopping_label)

        if self.on_stop_callback:
            self.on_stop_callback()

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