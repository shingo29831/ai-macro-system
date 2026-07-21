# src/ui/views/record_dialog.py
# @role: 記録モード中に常時最前面かつフレームレス（枠なし）で画面上部に表示される、ミニマルなコントロール用ウィジェット画面を制御するビュークラス。

from PySide6.QtWidgets import QWidget, QPushButton, QLabel, QVBoxLayout, QHBoxLayout, QFrame
from PySide6.QtCore import Qt
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
        
        self.dialog = QWidget()
        self.dialog.setWindowTitle("記録中")
        
        self.dialog.setFixedSize(560, 60)
        
        self.dialog.setWindowFlags(
            Qt.Window | Qt.FramelessWindowHint | Qt.WindowStaysOnTopHint | Qt.Tool
        )
        self.dialog.setAttribute(Qt.WA_TranslucentBackground)
        
        layout = QVBoxLayout(self.dialog)
        layout.setContentsMargins(10, 10, 10, 10)
        
        self.frame = QFrame(self.dialog)
        self.frame.setStyleSheet("QFrame { background-color: rgba(30, 30, 30, 200); border-radius: 8px; }")
        frame_layout = QHBoxLayout(self.frame)
        frame_layout.setContentsMargins(10, 5, 10, 5)
        frame_layout.setSpacing(10)
        
        self.btn_loop = QPushButton("🔁 繰り返し作業", self.frame)
        self.btn_loop.setStyleSheet("""
            QPushButton {
                background-color: #4b5563;
                color: white;
                font-weight: bold;
                font-size: 13px;
                border-radius: 4px;
                padding: 5px 10px;
            }
            QPushButton:hover {
                background-color: #6b7280;
            }
        """)
        
        self.lbl_timer = QLabel("00:00", self.frame)
        self.lbl_timer.setStyleSheet("color: white; font-size: 16px; font-weight: bold;")
        self.lbl_timer.setAlignment(Qt.AlignCenter)
        
        self.btn_stop = QPushButton("⏹ 記録を終了 (Ctrl + \\)", self.frame)
        self.btn_stop.setStyleSheet("""
            QPushButton {
                background-color: #ef4444;
                color: white;
                font-weight: bold;
                font-size: 13px;
                border-radius: 4px;
                padding: 5px 10px;
            }
            QPushButton:hover {
                background-color: #dc2626;
            }
        """)
        
        frame_layout.addWidget(self.btn_loop)
        frame_layout.addWidget(self.lbl_timer, 1)
        frame_layout.addWidget(self.btn_stop)
        
        layout.addWidget(self.frame)
        
        if self.btn_stop:
            self.btn_stop.clicked.connect(self._on_stop_clicked)
            
        if self.btn_loop:
            self.btn_loop.clicked.connect(self._on_loop_toggle_clicked)

    def _on_loop_toggle_clicked(self):
        """繰り返し作業の開始・終了を切り替え、メタログを記録する"""
        state.is_loop_recording = not state.is_loop_recording
        
        event_no = state.get_next_event_no()
        dt = now_datetime()
        
        window_info = {
            "title": "System_Meta",
            "rect": {"x": 0, "y": 0, "width": 0, "height": 0}
        }
        
        if state.is_loop_recording:
            self.btn_loop.setText("⏹ 繰り返し終了")
            self.btn_loop.setStyleSheet("""
                QPushButton {
                    background-color: #d97706;
                    color: white;
                    font-weight: bold;
                    font-size: 13px;
                    border-radius: 4px;
                    padding: 5px 10px;
                }
                QPushButton:hover {
                    background-color: #b45309;
                }
            """)
            input_type = "meta_loop_start"
        else:
            self.btn_loop.setText("🔁 繰り返し作業")
            self.btn_loop.setStyleSheet("""
                QPushButton {
                    background-color: #4b5563;
                    color: white;
                    font-weight: bold;
                    font-size: 13px;
                    border-radius: 4px;
                    padding: 5px 10px;
                }
                QPushButton:hover {
                    background-color: #6b7280;
                }
            """) 
            input_type = "meta_loop_end"
            
        log = build_base_log(
            event_no=event_no, 
            dt=dt, 
            input_type=input_type, 
            content={"status": "active" if state.is_loop_recording else "inactive"}, 
            window_info=window_info
        )
        state.append_log(log)

    def _on_stop_clicked(self):
        if self.on_stop_callback:
            self.on_stop_callback()
        self.dialog.close()

    def show(self):
        """ウィジェット画面を表示し、強制的に画面の最上端へ配置する"""
        self.dialog.show()
        
        screen = QGuiApplication.primaryScreen()
        if screen:
            screen_geo = screen.availableGeometry()
            x = screen_geo.x() + (screen_geo.width() - self.dialog.width()) // 2
            y = screen_geo.y()
            self.dialog.move(x, y)