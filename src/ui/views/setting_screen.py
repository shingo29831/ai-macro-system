"""Module: @role: AI動作モードおよび接続先サーバー設定の入力・接続テスト要求を担う設定タブビュー。"""

from PySide6.QtCore import Signal
from PySide6.QtWidgets import (
    QButtonGroup,
    QGridLayout,
    QHBoxLayout,
    QMessageBox,
    QVBoxLayout,
    QWidget,
)
from qfluentwidgets import (
    BodyLabel,
    FluentIcon,
    LineEdit,
    PrimaryPushButton,
    PushButton,
    RadioButton,
    SubtitleLabel,
)


class SettingScreen(QWidget):
    """設定画面への入口。"""

    open_settings_requested = Signal()

    def __init__(self, parent=None):
        super().__init__(parent)

        self.setObjectName("SettingScreen")

        main_layout = QVBoxLayout(self)
        main_layout.setContentsMargins(40, 40, 40, 40)
        main_layout.setSpacing(25)

        self.title_label = SubtitleLabel("設定画面", self)
        main_layout.addWidget(self.title_label)

        mode_layout = QVBoxLayout()
        mode_layout.setSpacing(10)

        mode_title = BodyLabel("AI 接続先設定", self)
        font_mode = mode_title.font()
        font_mode.setBold(True)
        mode_title.setFont(font_mode)
        mode_layout.addWidget(mode_title)

        self.local_ai_radio = RadioButton("ローカルAI", self)
        self.cloud_ai_radio = RadioButton("クラウドAI", self)

        self.local_ai_radio.setChecked(True)

        self.mode_group = QButtonGroup(self)
        self.mode_group.addButton(self.local_ai_radio)
        self.mode_group.addButton(self.cloud_ai_radio)

        mode_layout.addWidget(self.local_ai_radio)
        mode_layout.addWidget(self.cloud_ai_radio)

        main_layout.addLayout(mode_layout)

        server_layout = QGridLayout()
        server_layout.setVerticalSpacing(15)
        server_layout.setHorizontalSpacing(15)

        llm_title = BodyLabel("LLMサーバ設定", self)
        font_llm = llm_title.font()
        font_llm.setBold(True)
        llm_title.setFont(font_llm)

        self.llm_host_input = LineEdit(self)
        self.llm_host_input.setPlaceholderText("localhost")

        server_layout.addWidget(llm_title, 0, 0)
        server_layout.addWidget(self.llm_host_input, 0, 1)

        vision_title = BodyLabel("画面解析 サーバ設定", self)
        font_vision = vision_title.font()
        font_vision.setBold(True)
        vision_title.setFont(font_vision)

        self.vision_host_input = LineEdit(self)
        self.vision_host_input.setPlaceholderText("localhost")

        server_layout.addWidget(vision_title, 1, 0)
        server_layout.addWidget(self.vision_host_input, 1, 1)
        server_layout.setColumnStretch(1, 1)

        main_layout.addLayout(server_layout)
        main_layout.addStretch(1)

        bottom_layout = QHBoxLayout()

        self.test_button = PushButton(FluentIcon.SYNC, "接続テスト", self)
        self.cancel_button = PushButton("キャンセル", self)
        self.save_button = PrimaryPushButton("保存", self)

        bottom_layout.addWidget(self.test_button)
        bottom_layout.addStretch(1)
        bottom_layout.addWidget(self.cancel_button)
        bottom_layout.addWidget(self.save_button)

        main_layout.addLayout(bottom_layout)

        self.test_button.clicked.connect(self.connect_test)

    def connect_test(self):
        QMessageBox.information(self, "接続テスト", "接続テスト要求を受け付けました（バックエンド未結合）")
