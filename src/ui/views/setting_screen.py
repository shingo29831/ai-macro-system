"""Module: @role: AIに詳しくない一般ユーザー向け3択UIと、アプリ全体テーマ（#f1f5f9 / #2563eb）に完全統合された設定画面ビュー。"""

from pathlib import Path
from PySide6.QtCore import Qt, Signal
from PySide6.QtWidgets import (
    QWidget,
    QVBoxLayout,
    QHBoxLayout,
    QGridLayout,
    QButtonGroup,
    QMessageBox,
    QScrollArea,
    QFrame,
    QLabel,
)
from qfluentwidgets import (
    TitleLabel,
    BodyLabel,
    CaptionLabel,
    StrongBodyLabel,
    RadioButton,
    LineEdit,
    PushButton,
    PrimaryPushButton,
    CardWidget,
    FluentIcon,
    InfoBar,
    InfoBarPosition,
    setThemeColor,
)

try:
    from ui.viewmodels.settings_viewmodel import SettingsViewModel
except ModuleNotFoundError:
    from src.ui.viewmodels.settings_viewmodel import SettingsViewModel


class SettingScreen(QWidget):
    open_settings_requested = Signal()

    def __init__(self, parent=None):
        super().__init__(parent)
        self.setObjectName("SettingScreen")

        # アプリ共通のプライマリブルーに統一
        setThemeColor("#2563eb")

        self.viewmodel = SettingsViewModel()

        self._init_ui()
        self._apply_theme_style()
        self._bind_viewmodel()
        self.viewmodel.load_current_settings()

    def _init_ui(self):
        root_layout = QVBoxLayout(self)
        root_layout.setContentsMargins(0, 0, 0, 0)

        scroll_area = QScrollArea(self)
        scroll_area.setObjectName("settingScrollArea")
        scroll_area.setWidgetResizable(True)
        scroll_area.setFrameShape(QFrame.NoFrame)

        container = QWidget()
        container.setObjectName("settingContainer")
        layout = QVBoxLayout(container)
        layout.setContentsMargins(30, 30, 30, 30)
        layout.setSpacing(20)

        # 画面ヘッダー（MainScreenと視覚階層を完全統一）
        self.title_label = TitleLabel("設定", container)
        self.desc_label = QLabel("画面の認識やマクロ生成に必要なAIの接続方法を設定します。", container)
        self.desc_label.setObjectName("lblPageDesc")

        layout.addWidget(self.title_label)
        layout.addWidget(self.desc_label)
        layout.addSpacing(4)

        # 3つの接続先選択カード
        mode_card = CardWidget(container)
        mode_card.setObjectName("settingCard")
        mode_card_layout = QVBoxLayout(mode_card)
        mode_card_layout.setContentsMargins(24, 20, 24, 24)
        mode_card_layout.setSpacing(14)

        mode_header = StrongBodyLabel("AIの実行場所を選択してください", mode_card)
        mode_card_layout.addWidget(mode_header)

        self.mode_group = QButtonGroup(self)

        # 選択肢1: このパソコンで動かす
        self.radio_local = RadioButton("このパソコンで動かす（ローカルAI）", mode_card)
        self.desc_local = CaptionLabel(
            "お使いのパソコン内でAIを実行します。外部へデータを送信しないため機密情報も安全です。\n※追加の設定は不要です。",
            mode_card
        )
        self.desc_local.setObjectName("lblOptionHint")
        mode_card_layout.addWidget(self.radio_local)
        mode_card_layout.addWidget(self.desc_local)

        # 選択肢2: 公式クラウドを使う
        self.radio_cloud = RadioButton("当社が提供するサーバーを使う（公式クラウドAI）", mode_card)
        self.desc_cloud = CaptionLabel(
            "高速・高精度な公式AIサーバーを利用します。パソコンへの負荷がなく快適に動作します。\n※追加の設定は不要です。",
            mode_card
        )
        self.desc_cloud.setObjectName("lblOptionHint")
        mode_card_layout.addWidget(self.radio_cloud)
        mode_card_layout.addWidget(self.desc_cloud)

        # 選択肢3: 社内・自前サーバーを使う
        self.radio_custom = RadioButton("指定したサーバーを使う（社内サーバー・自前サーバー）", mode_card)
        self.desc_custom = CaptionLabel(
            "社内ネットワーク等に設置された専用サーバーへ接続します。社内SEやシステム管理者の指示に従って設定してください。",
            mode_card
        )
        self.desc_custom.setObjectName("lblOptionHint")
        mode_card_layout.addWidget(self.radio_custom)
        mode_card_layout.addWidget(self.desc_custom)

        self.mode_group.addButton(self.radio_local)
        self.mode_group.addButton(self.radio_cloud)
        self.mode_group.addButton(self.radio_custom)
        self.radio_local.setChecked(True)

        layout.addWidget(mode_card)

        # 社内SE向けサーバー指定カード (自前サーバー選択時のみ表示)
        self.custom_server_card = CardWidget(container)
        self.custom_server_card.setObjectName("settingCard")
        custom_layout = QVBoxLayout(self.custom_server_card)
        custom_layout.setContentsMargins(24, 20, 24, 24)
        custom_layout.setSpacing(14)

        custom_header = StrongBodyLabel("社内サーバー接続設定（社内SE・システム管理者向け）", self.custom_server_card)
        custom_layout.addWidget(custom_header)

        grid_layout = QGridLayout()
        grid_layout.setHorizontalSpacing(16)
        grid_layout.setVerticalSpacing(12)

        host_label = BodyLabel("サーバーのアドレス:", self.custom_server_card)
        self.host_input = LineEdit(self.custom_server_card)
        self.host_input.setPlaceholderText("例: 192.168.1.100 または ai-server.company.local")
        host_hint = CaptionLabel("社内AIサーバーのIPアドレス、またはホスト名（ドメイン）を入力してください。", self.custom_server_card)
        host_hint.setObjectName("lblFieldHint")

        port_label = BodyLabel("ポート番号:", self.custom_server_card)
        self.port_input = LineEdit(self.custom_server_card)
        self.port_input.setPlaceholderText("8843")
        self.port_input.setText("8843")
        port_hint = CaptionLabel("※社内SEから特別な指定がない場合は「8843」のままで問題ありません。", self.custom_server_card)
        port_hint.setObjectName("lblFieldHint")

        grid_layout.addWidget(host_label, 0, 0)
        grid_layout.addWidget(self.host_input, 0, 1)
        grid_layout.addWidget(host_hint, 1, 1)

        grid_layout.addWidget(port_label, 2, 0)
        grid_layout.addWidget(self.port_input, 2, 1)
        grid_layout.addWidget(port_hint, 3, 1)
        grid_layout.setColumnStretch(1, 1)

        custom_layout.addLayout(grid_layout)
        layout.addWidget(self.custom_server_card)

        # 設定不要の安心メッセージカード (ローカルまたはクラウド選択時に表示)
        self.no_config_card = CardWidget(container)
        self.no_config_card.setObjectName("noConfigCard")
        no_config_layout = QHBoxLayout(self.no_config_card)
        no_config_layout.setContentsMargins(20, 16, 20, 16)
        no_config_text = BodyLabel(
            "✓ IPアドレスやドメインの設定は不要です。このまま「設定を保存」をクリックしてください。",
            self.no_config_card
        )
        no_config_text.setObjectName("lblNoConfigText")
        no_config_layout.addWidget(no_config_text)
        layout.addWidget(self.no_config_card)

        layout.addStretch(1)

        # アクションボタンエリア
        bottom_layout = QHBoxLayout()
        self.test_button = PushButton(FluentIcon.SYNC, "接続テスト", container)
        self.test_button.setObjectName("btnTestConnection")
        self.test_button.setFixedSize(140, 40)

        self.save_button = PrimaryPushButton("設定を保存", container)
        self.save_button.setObjectName("btnSave")
        self.save_button.setFixedSize(140, 40)

        bottom_layout.addWidget(self.test_button)
        bottom_layout.addStretch(1)
        bottom_layout.addWidget(self.save_button)
        layout.addLayout(bottom_layout)

        scroll_area.setWidget(container)
        root_layout.addWidget(scroll_area)

        self._update_visibility()

    def _apply_theme_style(self):
        """アプリ全体の配色トークン（#f1f5f9 / #2563eb / #334155 / #64748b）を適用"""
        self.setStyleSheet("""
            QWidget#SettingScreen, QWidget#settingContainer {
                background-color: #f1f5f9;
                color: #334155;
                font-family: "Segoe UI", "Hiragino Sans", "Meiryo", sans-serif;
            }
            QScrollArea#settingScrollArea {
                background-color: transparent;
                border: none;
            }
            QLabel#lblPageDesc {
                color: #64748b;
                font-size: 13px;
            }
            CardWidget#settingCard {
                background-color: #ffffff;
                border: 1px solid #cbd5e1;
                border-radius: 12px;
            }
            CardWidget#noConfigCard {
                background-color: #eff6ff;
                border: 1px solid #bfdbfe;
                border-radius: 10px;
            }
            QLabel#lblNoConfigText {
                color: #1d4ed8;
                font-weight: 500;
            }
            QLabel#lblOptionHint {
                color: #64748b;
                margin-left: 28px;
            }
            QLabel#lblFieldHint {
                color: #64748b;
                font-size: 11px;
            }
            QLineEdit {
                background-color: #ffffff;
                border: 1px solid #cbd5e1;
                border-radius: 6px;
                padding: 6px 10px;
                color: #334155;
            }
            QLineEdit:focus {
                border: 2px solid #2563eb;
            }
            QPushButton#btnTestConnection {
                background-color: #e2e8f0;
                color: #475569;
                border: 1px solid #cbd5e1;
                border-radius: 8px;
                font-weight: bold;
                font-size: 13px;
            }
            QPushButton#btnTestConnection:hover {
                background-color: #cbd5e1;
            }
            QPushButton#btnSave {
                background-color: #2563eb;
                color: #ffffff;
                border: none;
                border-radius: 8px;
                font-weight: bold;
                font-size: 13px;
            }
            QPushButton#btnSave:hover {
                background-color: #1d4ed8;
            }
            QPushButton#btnSave:pressed {
                background-color: #1e40af;
            }
        """)

    def _bind_viewmodel(self):
        self.radio_local.toggled.connect(self._update_visibility)
        self.radio_cloud.toggled.connect(self._update_visibility)
        self.radio_custom.toggled.connect(self._update_visibility)

        self.save_button.clicked.connect(self._on_save_clicked)
        self.test_button.clicked.connect(self._on_test_clicked)

        self.viewmodel.config_loaded.connect(self._on_config_loaded)
        self.viewmodel.save_successful.connect(self._on_save_success)
        self.viewmodel.save_failed.connect(self._on_save_failed)
        self.viewmodel.connection_test_finished.connect(self._on_test_finished)

    def _update_visibility(self):
        is_custom = self.radio_custom.isChecked()
        self.custom_server_card.setVisible(is_custom)
        self.no_config_card.setVisible(not is_custom)

    def _get_current_mode(self) -> str:
        if self.radio_custom.isChecked():
            return "custom"
        elif self.radio_cloud.isChecked():
            return "cloud"
        return "local"

    def _on_config_loaded(self, config_dict: dict):
        mode = config_dict.get("ai_mode", "local")
        if mode == "custom":
            self.radio_custom.setChecked(True)
        elif mode == "cloud":
            self.radio_cloud.setChecked(True)
        else:
            self.radio_local.setChecked(True)

        custom_host = config_dict.get("custom_server_host") or config_dict.get("cv_host", "")
        if custom_host in ("127.0.0.1", "localhost"):
            custom_host = ""
        self.host_input.setText(custom_host)

        custom_port = config_dict.get("custom_server_port") or config_dict.get("cv_port", "8843")
        self.port_input.setText(str(custom_port))

        self._update_visibility()

    def _on_save_clicked(self):
        mode = self._get_current_mode()
        host = self.host_input.text()
        port = self.port_input.text()
        self.viewmodel.save_settings(mode, host, port)

    def _on_test_clicked(self):
        mode = self._get_current_mode()
        host = self.host_input.text()
        port = self.port_input.text()
        self.test_button.setEnabled(False)
        self.viewmodel.test_connection(mode, host, port)

    def _on_save_success(self):
        InfoBar.success(
            title="保存完了",
            content="AI接続設定を正常に更新しました。",
            position=InfoBarPosition.TOP,
            parent=self
        )

    def _on_save_failed(self, error_message: str):
        QMessageBox.warning(self, "入力エラー", error_message)

    def _on_test_finished(self, success: bool, message: str):
        self.test_button.setEnabled(True)
        if success:
            InfoBar.success(title="接続テスト成功", content=message, position=InfoBarPosition.TOP, parent=self)
        else:
            QMessageBox.warning(self, "接続テスト結果", message)
