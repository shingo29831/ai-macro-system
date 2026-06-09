# @role: ホーム画面（メインウィンドウ）のUI表示、UI要素の紐付け、およびシステムステータスの切り替え表現を制御するビュークラス。
import os
from PySide6.QtWidgets import QMainWindow, QWidget, QPushButton, QTableWidget
from PySide6.QtUiTools import QUiLoader
from PySide6.QtCore import QFile
from ui.views.record_dialog import RecordDialog

class MainWindow(QMainWindow):
    """メインウィンドウ（ホーム画面）のインタラクションを管理するクラス"""
    
    def __init__(self):
        super().__init__()
        self.setWindowTitle("Macro Manager")
        self.resize(800, 600)
        
        # UIリソースの読み込みと適用
        self.central_widget = self._load_ui_and_style("main_window.ui")
        self.setCentralWidget(self.central_widget)
        
        # 画面の一体感を持たせるため、レイアウト内の無駄な隙間を排除
        main_layout = self.central_widget.layout()
        if main_layout:
            main_layout.setSpacing(0)
            
        # UI要素のバインド
        self.btn_status = self.central_widget.findChild(QPushButton, "btnStatus")
        self.btn_start_record = self.central_widget.findChild(QPushButton, "btnStartRecord")
        self.table_macros = self.central_widget.findChild(QTableWidget, "tableMacros")
        
        if self.table_macros:
            self.table_macros.setShowGrid(False)
            
        # 状態管理の初期化
        self.states = ["idle", "recording", "running"]
        self.state_labels = {"idle": "待機中", "recording": "記録中", "running": "実行中"}
        self.current_state_index = 0
        self._update_status_ui()
        
        # シグナル接続
        if self.btn_status:
            self.btn_status.clicked.connect(self.toggle_status)
        if self.btn_start_record:
            self.btn_start_record.clicked.connect(self.open_record_dialog)

    def toggle_status(self):
        """手動テスト用にステータスを順次切り替える"""
        self.current_state_index = (self.current_state_index + 1) % len(self.states)
        self._update_status_ui()

    def _update_status_ui(self):
        """現在の状態ラベルのテキストと、CSS擬似クラス（Property）を動的に同期する"""
        if not self.btn_status:
            return
            
        state = self.states[self.current_state_index]
        self.btn_status.setText(self.state_labels[state])
        self.btn_status.setProperty("state", state)
        
        # Qtがプロパティ変更を検知して即座にCSSを再レンダリングするように強制する処理
        self.btn_status.style().unpolish(self.btn_status)
        self.btn_status.style().polish(self.btn_status)

    def open_record_dialog(self):
        """新規マクロ記録用のフローティング・ウィジェットを表示する"""
        self.record_dialog = RecordDialog(self)
        self.record_dialog.show()

    def _load_ui_and_style(self, ui_file_name: str) -> QWidget:
        """指定されたUIファイルをリソース配後から検索・ロードし、対になるCSSをバインドする"""
        base_dir = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
        ui_path = os.path.join(base_dir, "resources", "ui", ui_file_name)
        
        loader = QUiLoader()
        ui_file = QFile(ui_path)
        if not ui_file.open(QFile.ReadOnly):
            raise FileNotFoundError(f"Cannot open UI file: {ui_path}")
            
        widget = loader.load(ui_file, self)
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