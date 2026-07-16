# src/ui/views/settings_dialog.py
# @role: 画面要件に基づき、AIの動作モード切り替え、各サーバーのエンドポイント入力、バリデーション結果のフィードバック、接続テスト要求を制御する設定画面のビュークラス。

from PySide6.QtWidgets import QDialog, QPushButton, QLineEdit, QRadioButton, QMessageBox, QWidget, QVBoxLayout, QHBoxLayout, QFormLayout, QGroupBox
from PySide6.QtCore import Qt, Slot
from ui.viewmodels.settings_viewmodel import SettingsViewModel

class SettingsDialog(QDialog):
    """システム接続設定のユーザーインタラクションおよびダイアログ描画を制御するクラス"""

    def __init__(self, parent: QWidget | None = None):
        super().__init__(parent)
        self.viewmodel = SettingsViewModel()
        
        self.setWindowTitle("設定")
        self.setFixedSize(500, 400)
        
        layout = QVBoxLayout(self)
        layout.setContentsMargins(20, 20, 20, 20)
        layout.setSpacing(15)
        
        # AI Mode
        mode_group = QGroupBox("AI 動作モード", self)
        mode_layout = QHBoxLayout(mode_group)
        self.rad_local = QRadioButton("ローカルAI", mode_group)
        self.rad_cloud = QRadioButton("クラウドAI", mode_group)
        mode_layout.addWidget(self.rad_local)
        mode_layout.addWidget(self.rad_cloud)
        layout.addWidget(mode_group)
        
        # Server Settings
        server_group = QGroupBox("サーバー設定", self)
        form_layout = QFormLayout(server_group)
        form_layout.setSpacing(10)
        
        self.txt_llm_host = QLineEdit(server_group)
        self.txt_llm_port = QLineEdit(server_group)
        self.txt_cv_host = QLineEdit(server_group)
        self.txt_cv_port = QLineEdit(server_group)
        
        form_layout.addRow("LLM Host:", self.txt_llm_host)
        form_layout.addRow("LLM Port:", self.txt_llm_port)
        form_layout.addRow("CV Host:", self.txt_cv_host)
        form_layout.addRow("CV Port:", self.txt_cv_port)
        
        layout.addWidget(server_group)
        
        # Buttons
        btn_layout = QHBoxLayout()
        self.btn_test_connection = QPushButton("接続テスト", self)
        self.btn_save = QPushButton("保存", self)
        self.btn_cancel = QPushButton("キャンセル", self)
        
        btn_layout.addWidget(self.btn_test_connection)
        btn_layout.addStretch()
        btn_layout.addWidget(self.btn_cancel)
        btn_layout.addWidget(self.btn_save)
        
        layout.addWidget(btn_layout)
            
        self._bind_viewmodel()
        self.viewmodel.load_current_settings()

    def _bind_viewmodel(self):
        if self.btn_save:
            self.btn_save.clicked.connect(self._on_save_clicked)
        if self.btn_cancel:
            self.btn_cancel.clicked.connect(self.reject)
        if self.btn_test_connection:
            self.btn_test_connection.clicked.connect(self._on_test_connection_clicked)
        if self.rad_local and self.rad_cloud:
            self.rad_local.toggled.connect(self._on_ai_mode_changed)

        self.viewmodel.config_loaded.connect(self._update_ui_fields)
        self.viewmodel.save_successful.connect(self._on_save_success)
        self.viewmodel.save_failed.connect(self._on_save_failed)

    @Slot(dict)
    def _update_ui_fields(self, config_dict: dict):
        """ロードされた設定値を各コンポーネントに反映する"""
        if config_dict.get("ai_mode") == "cloud":
            if self.rad_cloud:
                self.rad_cloud.setChecked(True)
        else:
            if self.rad_local:
                self.rad_local.setChecked(True)

        if self.txt_llm_host:
            self.txt_llm_host.setText(config_dict.get("llm_host", ""))
        if self.txt_llm_port:
            self.txt_llm_port.setText(config_dict.get("llm_port", ""))
        if self.txt_cv_host:
            self.txt_cv_host.setText(config_dict.get("cv_host", ""))
        if self.txt_cv_port:
            self.txt_cv_port.setText(config_dict.get("cv_port", ""))

    @Slot()
    def _on_ai_mode_changed(self):
        """仕様書要件: ローカルAI選択時のみLLMサーバー設定を有効化する（クラウド時は入力不要のため無効化）"""
        if self.rad_local and self.txt_llm_host and self.txt_llm_port:
            is_local = self.rad_local.isChecked()
            self.txt_llm_host.setEnabled(is_local)
            self.txt_llm_port.setEnabled(is_local)

    @Slot()
    def _on_save_clicked(self):
        """現在の画面入力値を集約し、検証要求をViewModelへ委譲する"""
        ai_mode = "local"
        if self.rad_cloud and self.rad_cloud.isChecked():
            ai_mode = "cloud"

        llm_host = self.txt_llm_host.text() if self.txt_llm_host else ""
        llm_port = self.txt_llm_port.text() if self.txt_llm_port else ""
        cv_host = self.txt_cv_host.text() if self.txt_cv_host else ""
        cv_port = self.txt_cv_port.text() if self.txt_cv_port else ""

        self.viewmodel.save_settings(ai_mode, llm_host, llm_port, cv_host, cv_port)

    @Slot()
    def _on_save_success(self):
        QMessageBox.information(self, "成功", "システム接続設定を保存しました。")
        self.accept()

    @Slot(str)
    def _on_save_failed(self, error_message: str):
        QMessageBox.warning(self, "エラー", error_message)

    @Slot()
    def _on_test_connection_clicked(self):
        QMessageBox.information(self, "接続テスト", "接続テスト要求を受け付けました（バックエンド未結合）。")