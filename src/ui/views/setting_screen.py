"""Module: @role: AIに詳しくない一般ユーザーにも分かりやすい3択と、社内SE向けサーバー指定を切り替える設定画面ビュー。"""

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
)
from qfluentwidgets import (
    SubtitleLabel,
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
)

from src.ui.viewmodels.settings_viewmodel import SettingsViewModel


class SettingScreen(QWidget):
    open_settings_requested = Signal()

    def __init__(self, parent=None):
        super().__init__(parent)
        self.setObjectName("SettingScreen")
        self.viewmodel = SettingsViewModel()

        self._init_ui()
        self._bind_viewmodel()
        self.viewmodel.load_current_settings()

    def _init_ui(self):
        root_layout = QVBoxLayout(self)
        root_layout.setContentsMargins(0, 0, 0, 0)

        scroll_area = QScrollArea(self)
        scroll_area.setWidgetResizable(True)
        scroll_area.setFrameShape(QFrame.NoFrame)

        container = QWidget()
        layout = QVBoxLayout(container)
        layout.setContentsMargins(36, 32, 36, 32)
        layout.setSpacing(20)

        # 画面ヘッダー
        self.title_label = SubtitleLabel("AI 接続設定", container)
        self.desc_label = CaptionLabel("画面の認識や操作手順の解析を行うAIの接続先を選択します。", container)
        layout.addWidget(self.title_label)
        layout.addWidget(self.desc_label)

        # 3つの接続先選択カード
        mode_card = CardWidget(container)
        mode_card_layout = QVBoxLayout(mode_card)
        mode_card_layout.setContentsMargins(20, 20, 20, 20)
        mode_card_layout.setSpacing(16)

        mode_header = StrongBodyLabel("AIの実行場所を選択してください", mode_card)
        mode_card_layout.addWidget(mode_header)

        self.mode_group = QButtonGroup(self)

        # 選択肢1: このパソコンで動かす
        self.radio_local = RadioButton("このパソコンで動かす（ローカルAI）", mode_card)
        self.desc_local = CaptionLabel(
            "お使いのパソコン内でAIを実行します。社外にデータを送信しないため機密情報も安全です。\n※追加の設定は不要です。",
            mode_card
        )
        self.desc_local.setStyleSheet("color: #666666; margin-left: 28px;")
        mode_card_layout.addWidget(self.radio_local)
        mode_card_layout.addWidget(self.desc_local)

        # 選択肢2: 公式クラウドを使う
        self.radio_cloud = RadioButton("当社が提供するサーバーを使う（公式クラウドAI）", mode_card)
        self.desc_cloud = CaptionLabel(
            "高速・高精度な公式AIサーバーを利用します。パソコンへの負荷がなく快適に動作します。\n※追加の設定は不要です。",
            mode_card
        )
        self.desc_cloud.setStyleSheet("color: #666666; margin-left: 28px;")
        mode_card_layout.addWidget(self.radio_cloud)
        mode_card_layout.addWidget(self.desc_cloud)

        # 選択肢3: 社内・自前サーバーを使う
        self.radio_custom = RadioButton("指定したサーバーを使う（社内サーバー・自前サーバー）", mode_card)
        self.desc_custom = CaptionLabel(
            "社内ネットワーク等に設置された専用サーバーへ接続します。社内SEやシステム管理者の指示に従って設定してください。",
            mode_card
        )
        self.desc_custom.setStyleSheet("color: #666666; margin-left: 28px;")
        mode_card_layout.addWidget(self.radio_custom)
        mode_card_layout.addWidget(self.desc_custom)

        self.mode_group.addButton(self.radio_local)
        self.mode_group.addButton(self.radio_cloud)
        self.mode_group.addButton(self.radio_custom)
        self.radio_local.setChecked(True)

        layout.addWidget(mode_card)

        # 社内SE向けサーバー指定カード (自前サーバー選択時のみ表示)
        self.custom_server_card = CardWidget(container)
        custom_layout = QVBoxLayout(self.custom_server_card)
        custom_layout.setContentsMargins(20, 20, 20, 20)
        custom_layout.setSpacing(14)

        custom_header = StrongBodyLabel("社内サーバー接続設定（社内SE・管理者向け）", self.custom_server_card)
        custom_layout.addWidget(custom_header)

        grid_layout = QGridLayout()
        grid_layout.setHorizontalSpacing(16)
        grid_layout.setVerticalSpacing(12)

        host_label = BodyLabel("サーバーのアドレス:", self.custom_server_card)
        self.host_input = LineEdit(self.custom_server_card)
        self.host_input.setPlaceholderText("例: 192.168.1.100 または ai-server.company.local")
        host_hint = CaptionLabel("社内AIサーバーのIPアドレス、またはホスト名（ドメイン）を入力してください。", self.custom_server_card)
        host_hint.setStyleSheet("color: #888888;")

        port_label = BodyLabel("ポート番号:", self.custom_server_card)
        self.port_input = LineEdit(self.custom_server_card)
        self.port_input.setPlaceholderText("8843")
        self.port_input.setText("8843")
        port_hint = CaptionLabel("※社内SEから特別な指定がない場合は「8843」のままで問題ありません。", self.custom_server_card)
        port_hint.setStyleSheet("color: #888888;")

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
        no_config_layout = QHBoxLayout(self.no_config_card)
        no_config_layout.setContentsMargins(20, 16, 20, 16)
        no_config_text = BodyLabel(
            "✓ IPアドレスやドメインの設定は不要です。このまま「設定を保存」をクリックしてください。",
            self.no_config_card
        )
        no_config_text.setStyleSheet("color: #0078D4; font-weight: 500;")
        no_config_layout.addWidget(no_config_text)
        layout.addWidget(self.no_config_card)

        layout.addStretch(1)

        # アクションボタンエリア
        bottom_layout = QHBoxLayout()
        self.test_button = PushButton(FluentIcon.SYNC, "接続テスト", container)
        self.save_button = PrimaryPushButton("設定を保存", container)

        bottom_layout.addWidget(self.test_button)
        bottom_layout.addStretch(1)
        bottom_layout.addWidget(self.save_button)
        layout.addLayout(bottom_layout)

        scroll_area.setWidget(container)
        root_layout.addWidget(scroll_area)

        # 初期表示トグル
        self._update_visibility()

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
