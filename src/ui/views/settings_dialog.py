# @role: 画面要件に基づき、AIの動作モード切り替え、各サーバーのエンドポイント入力、バリデーション結果のフィードバック、接続テスト要求を制御する設定画面のビュークラス。

import os
from PySide6.QtWidgets import QDialog, QPushButton, QLineEdit, QRadioButton, QMessageBox, QWidget
from PySide6.QtUiTools import QUiLoader
from PySide6.QtCore import QFile, Qt, Slot
from ui.viewmodels.settings_viewmodel import SettingsViewModel

class SettingsDialog(QDialog):
    """システム接続設定のユーザーインタラクションおよびダイアログ描画を制御するクラス"""

    def __init__(self, parent: QWidget | None = None):
        super().__init__(parent)
        self.viewmodel = SettingsViewModel()
        
        # 共通ローダーユーティリティを介してUIとCSSを適用
        self.ui_widget = self._load_ui_and_style("settings_dialog.ui")
        
        # 動的ロードされたウィジェットから子要素を参照
        self.rad_local = self.ui_widget.findChild(QRadioButton, "radLocal")
        self.rad_cloud = self.ui_widget.findChild(QRadioButton, "radCloud")
        self.txt_llm_host = self.ui_widget.findChild(QLineEdit, "txtLlmHost")
        self.txt_llm_port = self.ui_widget.findChild(QLineEdit, "txtLlmPort")
        self.txt_cv_host = self.ui_widget.findChild(QLineEdit, "txtCvHost")
        self.txt_cv_port = self.ui_widget.findChild(QLineEdit, "txtCvPort")
        
        self.btn_test_connection = self.ui_widget.findChild(QPushButton, "btnTestConnection")
        self.btn_save = self.ui_widget.findChild(QPushButton, "btnSave")
        self.btn_cancel = self.ui_widget.findChild(QPushButton, "btnCancel")
        
        # 1画面コンポーネントとしての挙動をQDialogへ同期
        if self.ui_widget.layout():
            self.setLayout(self.ui_widget.layout())
            
        self._bind_viewmodel()
        self.viewmodel.load_current_settings()

    def _bind_viewmodel(self):
        # UI操作からViewModelへのバインディング
        if self.btn_save:
            self.btn_save.clicked.connect(self._on_save_clicked)
        if self.btn_cancel:
            self.btn_cancel.clicked.connect(self.reject)
        if self.btn_test_connection:
            self.btn_test_connection.clicked.connect(self._on_test_connection_clicked)
        if self.rad_local and self.rad_cloud:
            self.rad_local.toggled.connect(self._on_ai_mode_changed)

        # ViewModelからの通知（シグナル）をUIの振る舞いへバインディング
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
        # データの例外や不整合を隠蔽せず、ダイアログで根本原因を明示
        QMessageBox.warning(self, "エラー", error_message)

    @Slot()
    def _on_test_connection_clicked(self):
        # TODO: 担当3(Engines)の疎通確認モジュールが実装され次第、非同期通信リクエストを中継する
        QMessageBox.information(self, "接続テスト", "接続テスト要求を受け付けました（バックエンド未結合）。")

    def _load_ui_and_style(self, ui_file_name: str) -> QWidget:
        """リソース配下から設定画面用のUIファイルとCSSを読み込む"""
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