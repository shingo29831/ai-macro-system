# @role: 仕様書の画面要件に基づき、ヘッダーのステータス管理、緊急停止、マクロ一覧テーブルの初期化・描画を制御するメイン画面のビュークラス。
import os
from PySide6.QtWidgets import (
    QMainWindow, QWidget, QPushButton, QTableWidget, 
    QTableWidgetItem, QAbstractItemView, QLabel, QHeaderView
)
from PySide6.QtUiTools import QUiLoader
from PySide6.QtCore import QFile, Qt
from ui.views.record_dialog import RecordDialog

class MainWindow(QMainWindow):
    """メインウィンドウ（ホーム画面）のインタラクションとUI表示を管理するクラス"""
    
    def __init__(self):
        super().__init__()
        self.setWindowTitle("Macro Manager")
        self.resize(900, 600) # カラムが増えたため少し幅を拡大
        
        self.central_widget = self._load_ui_and_style("main_window.ui")
        self.setCentralWidget(self.central_widget)
        
        self.btn_emergency_stop = self.central_widget.findChild(QPushButton, "btnEmergencyStop")
        self.btn_status = self.central_widget.findChild(QPushButton, "btnStatus")
        self.btn_start_record = self.central_widget.findChild(QPushButton, "btnStartRecord")
        self.table_macros = self.central_widget.findChild(QTableWidget, "tableMacros")
        
        if self.table_macros:
            self.table_macros.setShowGrid(False)
        
        self.states = ["idle", "recording", "running"]
        self.state_labels = {"idle": "待機中", "recording": "記録中", "running": "実行中"}
        self.current_state_index = 0
        self._update_status_ui()
        
        if self.btn_emergency_stop:
            self.btn_emergency_stop.clicked.connect(self.handle_emergency_stop)
        if self.btn_status:
            self.btn_status.clicked.connect(self.toggle_status)
        if self.btn_start_record:
            self.btn_start_record.clicked.connect(self.open_record_dialog)
            
        self._init_table()

    def toggle_status(self):
        self.current_state_index = (self.current_state_index + 1) % len(self.states)
        self._update_status_ui()

    def handle_emergency_stop(self):
        print("Emergency Stop Action Triggered.")

    def _update_status_ui(self):
        if not self.btn_status:
            return
            
        state = self.states[self.current_state_index]
        self.btn_status.setText(self.state_labels[state])
        self.btn_status.setProperty("state", state)
        
        self.btn_status.style().unpolish(self.btn_status)
        self.btn_status.style().polish(self.btn_status)

    def _create_badge(self, text: str, badge_type: str) -> QWidget:
        """ステータス表示用の角丸バッジを生成する"""
        container = QWidget()
        layout = QWidget().layout() # Dummy for alignment
        lbl = QLabel(text)
        lbl.setProperty("badge", badge_type)
        lbl.setAlignment(Qt.AlignCenter)
        
        # セル内で中央に配置するためのラッパー
        from PySide6.QtWidgets import QHBoxLayout
        h_layout = QHBoxLayout(container)
        h_layout.setContentsMargins(4, 4, 4, 4)
        h_layout.addWidget(lbl)
        
        return container

    def _init_table(self):
        if not self.table_macros:
            return
            
        self.table_macros.setEditTriggers(QAbstractItemView.NoEditTriggers)
        self.table_macros.setSelectionBehavior(QAbstractItemView.SelectRows)
        
        # ====== 今回追加する設定 ======
        # ユーザーによる行の高さ（縦幅）変更を無効化し、デフォルトの高さを60pxに固定して数字を見やすくする
        self.table_macros.verticalHeader().setSectionResizeMode(QHeaderView.Fixed)
        self.table_macros.verticalHeader().setDefaultSectionSize(60)
        # ==============================
        
        # 実際の運用を想定したモックデータ（成否と修復回数を含む）
        macros = [
            {"name": "Meld Task 定期バックアップ", "status": "success", "status_text": "成功", "heals": "0回", "heal_level": "none", "last_run": "2026-06-10 09:00:00"},
            {"name": "ValorantParty データ同期", "status": "warning", "status_text": "修復完了", "heals": "2回", "heal_level": "mid", "last_run": "2026-06-09 23:30:00"},
            {"name": "D1 Grand Prix ログ収集", "status": "success", "status_text": "成功", "heals": "1回", "heal_level": "low", "last_run": "2026-06-08 14:15:00"},
            {"name": "就活ポータル 新着チェック", "status": "danger", "status_text": "失敗 (Stage 4)", "heals": "4回", "heal_level": "high", "last_run": "2026-06-07 18:00:00"}
        ]
        
        self.table_macros.setRowCount(len(macros))
        
        for row, macro in enumerate(macros):
            # 1. マクロ名
            self.table_macros.setItem(row, 0, QTableWidgetItem(macro["name"]))
            
            # 2. 直近の結果バッジ
            status_badge = self._create_badge(macro["status_text"], macro["status"])
            self.table_macros.setCellWidget(row, 1, status_badge)
            
            # 3. 自己修復バッジ
            heal_badge = self._create_badge(macro["heals"], f"heal_{macro['heal_level']}")
            self.table_macros.setCellWidget(row, 2, heal_badge)
            
            # 4. 最終実行日時
            self.table_macros.setItem(row, 3, QTableWidgetItem(macro["last_run"]))
            
            # 5. 実行ボタン
            btn_run = QPushButton("▶ 実行")
            btn_run.setObjectName("btnRun")
            btn_run.setCursor(Qt.PointingHandCursor)
            self.table_macros.setCellWidget(row, 4, btn_run)
            
            # 6. 削除ボタン
            btn_delete = QPushButton("🗑 削除")
            btn_delete.setObjectName("btnDelete")
            btn_delete.setCursor(Qt.PointingHandCursor)
            self.table_macros.setCellWidget(row, 5, btn_delete)

        # カラム幅の最適化
        self.table_macros.resizeColumnsToContents()
        self.table_macros.setColumnWidth(0, 220) # 名前列は広めに
        self.table_macros.setColumnWidth(1, 110)
        self.table_macros.setColumnWidth(2, 90)
        self.table_macros.horizontalHeader().setStretchLastSection(True)

    def open_record_dialog(self):
        self.record_dialog = RecordDialog(self)
        self.record_dialog.show()

    def _load_ui_and_style(self, ui_file_name: str) -> QWidget:
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