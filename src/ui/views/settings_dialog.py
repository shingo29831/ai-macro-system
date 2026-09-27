"""Module: @role: モーダル表示用のシステム接続設定ダイアログ。3択のAIモードと条件付き入力欄を提供する。"""

from PySide6.QtCore import Qt, Slot
from PySide6.QtWidgets import (
    QDialog,
    QWidget,
    QVBoxLayout,
    QHBoxLayout,
    QGridLayout,
    QGroupBox,
    QRadioButton,
    QLineEdit,
    QPushButton,
    QLabel,
    QMessageBox,
)

try:
    from ui.viewmodels.settings_viewmodel import SettingsViewModel
except ModuleNotFoundError:
    from src.ui.viewmodels.settings_viewmodel import SettingsViewModel


class SettingsDialog(QDialog):
    """システム接続設定のユーザーインタラクションおよびダイアログ描画を制御するクラス"""

    def __init__(self, parent: QWidget | None = None):
        super().__init__(parent)
        self.viewmodel = SettingsViewModel()

        self.setWindowTitle("AI 接続設定")
        self.setFixedSize(540, 460)

        layout = QVBoxLayout(self)
        layout.setContentsMargins(24, 24, 24, 24)
        layout.setSpacing(16)

        # 動作モード選択
        mode_group = QGroupBox("AIの実行場所を選択してください", self)
        mode_layout = QVBoxLayout(mode_group)
        mode_layout.setSpacing(10)

        self.rad_local = QRadioButton("このパソコンで動かす（ローカルAI）", mode_group)
        self.lbl_local_desc = QLabel("※追加の設定は不要です（社外にデータを送信せず安全に利用可能）", mode_group)
        self.lbl_local_desc.setStyleSheet("color: #666666; margin-left: 20px;")

        self.rad_cloud = QRadioButton("当社が提供するサーバーを使う（公式クラウドAI）", mode_group)
        self.lbl_cloud_desc = QLabel("※追加の設定は不要です（パソコンに負荷をかけず高速に処理可能）", mode_group)
        self.lbl_cloud_desc.setStyleSheet("color: #666666; margin-left: 20px;")

        self.rad_custom = QRadioButton("指定したサーバーを使う（社内サーバー・自前サーバー）", mode_group)
        self.lbl_custom_desc = QLabel("※社内SEやシステム管理者の指示に従って設定してください", mode_group)
        self.lbl_custom_desc.setStyleSheet("color: #666666; margin-left: 20px;")

        mode_layout.addWidget(self.rad_local)
        mode_layout.addWidget(self.lbl_local_desc)
        mode_layout.addWidget(self.rad_cloud)
        mode_layout.addWidget(self.lbl_cloud_desc)
        mode_layout.addWidget(self.rad_custom)
        mode_layout.addWidget(self.lbl_custom_desc)
        layout.addWidget(mode_group)

        # 社内SE向けサーバー設定グループ (自前サーバー時のみ表示)
        self.server_group = QGroupBox("社内サーバー設定（社内SE・管理者向け）", self)
        form_layout = QGridLayout(self.server_group)
        form_layout.setSpacing(10)

        self.txt_host = QLineEdit(self.server_group)
        self.txt_host.setPlaceholderText("例: 192.168.1.100 または ai-server.company.local")
        self.txt_port = QLineEdit(self.server_group)
        self.txt_port.setPlaceholderText("8843")
        self.txt_port.setText("8843")

        form_layout.addWidget(QLabel("サーバーアドレス:", self.server_group), 0, 0)
        form_layout.addWidget(self.txt_host, 0, 1)
        form_layout.addWidget(QLabel("ポート番号:", self.server_group), 1, 0)
        form_layout.addWidget(self.txt_port, 1, 1)
        layout.addWidget(self.server_group)

        # 設定不要案内ラベル (ローカルまたはクラウド時)
        self.lbl_no_setting = QLabel("✓ アドレスやポートの指定は不要です。このまま保存できます。", self)
        self.lbl_no_setting.setStyleSheet("color: #0078D4; font-weight: bold; margin: 8px 0;")
        layout.addWidget(self.lbl_no_setting)

        layout.addStretch()

        # アクションボタン
        btn_layout = QHBoxLayout()
        self.btn_test = QPushButton("接続テスト", self)
        self.btn_cancel = QPushButton("キャンセル", self)
        self.btn_save = QPushButton("保存", self)
        self.btn_save.setDefault(True)

        btn_layout.addWidget(self.btn_test)
        btn_layout.addStretch()
        btn_layout.addWidget(self.btn_cancel)
        btn_layout.addWidget(self.btn_save)
        layout.addWidget(btn_layout)

        self._bind_viewmodel()
        self.viewmodel.load_current_settings()

    def _bind_viewmodel(self):
        self.rad_local.toggled.connect(self._update_visibility)
        self.rad_cloud.toggled.connect(self._update_visibility)
        self.rad_custom.toggled.connect(self._update_visibility)

        self.btn_save.clicked.connect(self._on_save_clicked)
        self.btn_cancel.clicked.connect(self.reject)
        self.btn_test.clicked.connect(self._on_test_clicked)

        self.viewmodel.config_loaded.connect(self._on_config_loaded)
        self.viewmodel.save_successful.connect(self._on_save_success)
        self.viewmodel.save_failed.connect(self._on_save_failed)
        self.viewmodel.connection_test_finished.connect(self._on_test_finished)

    def _update_visibility(self):
        is_custom = self.rad_custom.isChecked()
        self.server_group.setVisible(is_custom)
        self.lbl_no_setting.setVisible(not is_custom)

    def _get_current_mode(self) -> str:
        if self.rad_custom.isChecked():
            return "custom"
        elif self.rad_cloud.isChecked():
            return "cloud"
        return "local"

    @Slot(dict)
    def _on_config_loaded(self, config_dict: dict):
        mode = config_dict.get("ai_mode", "local")
        if mode == "custom":
            self.rad_custom.setChecked(True)
        elif mode == "cloud":
            self.rad_cloud.setChecked(True)
        else:
            self.rad_local.setChecked(True)

        custom_host = config_dict.get("custom_server_host") or config_dict.get("cv_host", "")
        if custom_host in ("127.0.0.1", "localhost"):
            custom_host = ""
        self.txt_host.setText(custom_host)

        custom_port = config_dict.get("custom_server_port") or config_dict.get("cv_port", "8843")
        self.txt_port.setText(str(custom_port))

        self._update_visibility()

    @Slot()
    def _on_save_clicked(self):
        mode = self._get_current_mode()
        host = self.txt_host.text()
        port = self.txt_port.text()
        self.viewmodel.save_settings(mode, host, port)

    @Slot()
    def _on_test_clicked(self):
        mode = self._get_current_mode()
        host = self.txt_host.text()
        port = self.txt_port.text()
        self.btn_test.setEnabled(False)
        self.viewmodel.test_connection(mode, host, port)

    @Slot()
    def _on_save_success(self):
        QMessageBox.information(self, "成功", "システム接続設定を保存しました。")
        self.accept()

    @Slot(str)
    def _on_save_failed(self, error_message: str):
        QMessageBox.warning(self, "エラー", error_message)

    @Slot(bool, str)
    def _on_test_finished(self, success: bool, message: str):
        self.btn_test.setEnabled(True)
        if success:
            QMessageBox.information(self, "接続成功", message)
        else:
            QMessageBox.warning(self, "接続失敗", message)
