# @role: 仕様書の画面要件に基づき、ヘッダーのステータス管理、緊急停止、マクロ一覧テーブルの初期化・描画を制御するメイン画面のビュークラス。
import os
from PySide6.QtWidgets import QMainWindow, QWidget, QPushButton, QTableWidget, QTableWidgetItem, QAbstractItemView
from PySide6.QtUiTools import QUiLoader
from PySide6.QtCore import QFile, Qt
from ui.views.record_dialog import RecordDialog

class MainWindow(QMainWindow):
    """メインウィンドウ（ホーム画面）のインタラクションとUI表示を管理するクラス"""
    
    def __init__(self):
        super().__init__()
        self.setWindowTitle("Macro Manager")
        self.resize(800, 600)
        
        # 新しいリソースディレクトリ階層からUIとスタイルを読み込み
        self.central_widget = self._load_ui_and_style("main_window.ui")
        self.setCentralWidget(self.central_widget)
        
        # UI要素の取得とバインド
        self.btn_emergency_stop = self.central_widget.findChild(QPushButton, "btnEmergencyStop")
        self.btn_status = self.central_widget.findChild(QPushButton, "btnStatus")
        self.btn_start_record = self.central_widget.findChild(QPushButton, "btnStartRecord")
        self.table_macros = self.central_widget.findChild(QTableWidget, "tableMacros")
        
        # デザイン要件に基づきテーブルのグリッド線を非表示化
        if self.table_macros:
            self.table_macros.setShowGrid(False)
        
        # システム状態管理の初期化
        self.states = ["idle", "recording", "running"]
        self.state_labels = {"idle": "待機中", "recording": "記録中", "running": "実行中"}
        self.current_state_index = 0
        self._update_status_ui()
        
        # シグナル・スロットの接続
        if self.btn_emergency_stop:
            self.btn_emergency_stop.clicked.connect(self.handle_emergency_stop)
        if self.btn_status:
            self.btn_status.clicked.connect(self.toggle_status)
        if self.btn_start_record:
            self.btn_start_record.clicked.connect(self.open_record_dialog)
            
        # 作成済みマクロ一覧テーブルの初期化と描画
        self._init_table()

    def toggle_status(self):
        """手動テスト用にクリックでステータスをサイクル切り替えする"""
        self.current_state_index = (self.current_state_index + 1) % len(self.states)
        self._update_status_ui()

    def handle_emergency_stop(self):
        """緊急停止プロセスを呼び出す（現在はプロトタイプ用のログ出力）"""
        print("Emergency Stop Action Triggered.")

    def _update_status_ui(self):
        """現在のステータスに応じてバッジテキストを変更し、CSSの疑似状態プロパティを更新する"""
        if not self.btn_status:
            return
            
        state = self.states[self.current_state_index]
        self.btn_status.setText(self.state_labels[state])
        self.btn_status.setProperty("state", state)
        
        # Qtにプロパティの変更を強制認識させてCSSレイアウトを即座に再適用するための処理
        self.btn_status.style().unpolish(self.btn_status)
        self.btn_status.style().polish(self.btn_status)

    def _init_table(self):
        """マクロ一覧テーブルの表示規則・列幅を設定し、モックデータを挿入する"""
        if not self.table_macros:
            return
            
        self.table_macros.setEditTriggers(QAbstractItemView.NoEditTriggers)
        self.table_macros.setSelectionBehavior(QAbstractItemView.SelectRows)
        
        # 仕様書のバッジおよび履歴要件を満たすためのモックデータ
        macros = [
            {"name": "マクロ1", "last_run": "2026-06-01 12:00:00", "created": "2026-05-01 10:00:00"},
            {"name": "マクロ2", "last_run": "2026-06-05 15:30:00", "created": "2026-05-15 08:00:00"},
            {"name": "マクロ3", "last_run": "2026-06-09 09:15:00", "created": "2026-05-20 14:30:00"}
        ]
        
        self.table_macros.setRowCount(len(macros))
        
        for row, macro in enumerate(macros):
            self.table_macros.setItem(row, 0, QTableWidgetItem(macro["name"]))
            
            # 再生ボタンの配置とハンドカーソル設定
            btn_run = QPushButton("▶")
            btn_run.setObjectName("btnRun")
            btn_run.setCursor(Qt.PointingHandCursor)
            self.table_macros.setCellWidget(row, 1, btn_run)
            
            # 削除ボタンの配置とハンドカーソル設定
            btn_delete = QPushButton("🗑")
            btn_delete.setObjectName("btnDelete")
            btn_delete.setCursor(Qt.PointingHandCursor)
            self.table_macros.setCellWidget(row, 2, btn_delete)
            
            self.table_macros.setItem(row, 3, QTableWidgetItem(macro["last_run"]))
            self.table_macros.setItem(row, 4, QTableWidgetItem(macro["created"]))

        # デザイン崩れを防ぐため、列幅をコンテンツに追従させた後にボタン列を固定幅に固定
        self.table_macros.resizeColumnsToContents()
        self.table_macros.setColumnWidth(1, 80)
        self.table_macros.setColumnWidth(2, 80)
        self.table_macros.horizontalHeader().setStretchLastSection(True)

    def open_record_dialog(self):
        """新規マクロ記録用のフローティング・ウィジェット画面を生成して表示する"""
        self.record_dialog = RecordDialog(self)
        self.record_dialog.show()

    def _load_ui_and_style(self, ui_file_name: str) -> QWidget:
        """指定されたUIファイルを読み込み、同名のCSSファイルをリソース配下から自動適用する"""
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