from PySide6.QtCore import Qt, Signal, Slot
from PySide6.QtGui import QFont
from PySide6.QtWidgets import (
    QAbstractItemView,
    QHBoxLayout,
    QLabel,
    QMessageBox,
    QTableWidgetItem,
    QVBoxLayout,
    QWidget,
    QButtonGroup,
    QGridLayout
)
from PySide6.QtWidgets import QHeaderView

from qfluentwidgets import (
    FluentIcon,
    FluentWindow,
    PrimaryPushButton,
    PushButton,
    SubtitleLabel,
    TableWidget,
    Theme,
    TitleLabel,
    setTheme,
    BodyLabel,
    RadioButton,
    LineEdit
)

from ui.viewmodels.main_viewmodel import MainViewModel
from ui.views.progress_dialog import ProgressDialog
from ui.views.record_dialog import RecordDialog
from ui.views.running_dialog import RunningDialog
from ui.views.settings_dialog import SettingsDialog


class MainScreen(QWidget):
    """ホーム画面。マクロの記録・実行・削除・一覧表示を担当する。"""

    # 外部（MainWindowなど）へ通知するためのカスタムシグナルを定義
    start_record_requested = Signal()  # 記録開始ボタンが押されたとき
    run_macro_requested = Signal()      # 実行ボタンが押されたとき
    delete_macro_requested = Signal()   # 削除ボタンが押されたとき

    def __init__(self, viewmodel: MainViewModel, parent=None):
        super().__init__(parent)

        # 状態やロジックを保持する ViewModel をインスタンス変数に格納
        self.viewmodel = viewmodel
        # スタイリングや探索のためのオブジェクト名を設定
        self.setObjectName("MacroManager")

        # UI部品を保持する変数をあらかじめ初期化
        self.btn_start_record = None
        self.btn_run_selected = None
        self.btn_delete_selected = None
        self.table_macros = None

        # 画面の構築手順を順番に実行
        self._build_ui()          # 1. 各種ボタンやラベル、レイアウトの組み立て
        self._setup_table()       # 2. テーブル（マクロ一覧）の見栄えや挙動の細かな調整
        self._bind_viewmodel()    # 3. ViewModelのデータやシグナルとの紐付け処理

        # 画面起動時に、既存の保存済みマクロ一覧を読み込む
        self.viewmodel.load_macros()

    def _font(self, size: int, bold: bool = False) -> QFont:
        """日本語表示が安定しやすいフォントを生成する。"""
        # Windows標準の読みやすい角ゴシック体「Yu Gothic UI」を指定してフォントオブジェクトを作成
        font = QFont("Yu Gothic UI", size)
        # 代替フォントが必要になった場合もゴシック体（SansSerif）を優先するよう指示
        font.setStyleHint(QFont.StyleHint.SansSerif)

        # 太字か通常かを引数に基づいて判定し設定
        if bold:
            font.setWeight(QFont.Weight.Bold)
        else:
            font.setWeight(QFont.Weight.Normal)

        return font

    def _build_ui(self):
        # 画面全体の垂直方向（上から下）のメインレイアウトを作成
        main_layout = QVBoxLayout(self)
        main_layout.setContentsMargins(30, 30, 30, 30)  # 周囲の余白を30ピクセルに設定
        main_layout.setSpacing(20)                      # コンポーネント間の隙間を20ピクセルに設定

        # メインタイトル「ホーム」ラベルを作成
        page_title = TitleLabel("ホーム", self)
        page_title.setFont(self._font(22, bold=True))

        # 画面の説明テキストラベルを作成
        page_description = QLabel(
            "マクロの記録、実行、管理を行えます",
            self,
        )
        page_description.setFont(self._font(10))
        page_description.setStyleSheet("color: #64748b;")

        # タイトルと説明をメインレイアウトに追加
        main_layout.addWidget(page_title)
        main_layout.addWidget(page_description)
        main_layout.addSpacing(4)  # 少しだけスペースを空ける

        # --- 新しいマクロを作成するエリア（横並びレイアウト） ---
        record_area = QHBoxLayout()
        record_area.setSpacing(16)

        # テキスト部分を縦に並べるためのレイアウト
        record_text_layout = QVBoxLayout()
        record_text_layout.setSpacing(4)

        record_title = SubtitleLabel("新しいマクロを作成", self)
        record_title.setFont(self._font(15, bold=True))

        record_description = QLabel(
            "クリック、キーボード入力、スクロールなどの操作を記録します。",
            self,
        )
        record_description.setFont(self._font(10))
        record_description.setStyleSheet("color: #64748b;")

        record_text_layout.addWidget(record_title)
        record_text_layout.addWidget(record_description)

        # 目立つアクセントカラーの「記録を開始」ボタンを作成
        self.btn_start_record = PrimaryPushButton(
            "●  記録を開始",
            self,
        )
        self.btn_start_record.setFont(self._font(11, bold=True))
        self.btn_start_record.setFixedSize(210, 48)  # ボタンサイズを固定

        # テキストエリアを配置し、残りの空白を埋めてから右側にボタンを配置
        record_area.addLayout(record_text_layout)
        record_area.addStretch(1)  # 弾力性のあるスペース（左詰めにさせる）
        record_area.addWidget(
            self.btn_start_record,
            alignment=Qt.AlignmentFlag.AlignVCenter,  # 上下中央に配置
        )

        main_layout.addLayout(record_area)
        main_layout.addSpacing(10)

        # --- マクロ一覧エリアのヘッダー（タイトルと操作ボタン） ---
        table_header = QHBoxLayout()
        table_header.setSpacing(10)

        table_title_layout = QVBoxLayout()
        table_title_layout.setSpacing(2)

        macro_list_title = SubtitleLabel("マクロ一覧", self)
        macro_list_title.setFont(self._font(15, bold=True))

        macro_list_description = QLabel(
            "実行したいマクロを選択してください",
            self,
        )
        macro_list_description.setFont(self._font(10))
        macro_list_description.setStyleSheet("color: #64748b;")

        table_title_layout.addWidget(macro_list_title)
        table_title_layout.addWidget(macro_list_description)

        # 選択中のマクロを削除するボタン（初期状態は無効化）
        self.btn_delete_selected = PushButton("削除", self)
        self.btn_delete_selected.setFont(self._font(10))
        self.btn_delete_selected.setFixedSize(100, 36)
        self.btn_delete_selected.setEnabled(False)

        # 選択中のマクロを実行するボタン（初期状態は無効化、目立つスタイル）
        self.btn_run_selected = PrimaryPushButton("▶ 実行", self)
        self.btn_run_selected.setFont(self._font(10, bold=True))
        self.btn_run_selected.setFixedSize(110, 36)
        self.btn_run_selected.setEnabled(False)

        # タイトルを左側、各種操作ボタンを右側に寄せて配置
        table_header.addLayout(table_title_layout)
        table_header.addStretch(1)  # 右寄せにするための空白スペース
        table_header.addWidget(self.btn_delete_selected)
        table_header.addWidget(self.btn_run_selected)

        main_layout.addLayout(table_header)

        # --- マクロを表示するテーブルコンポーネントの構築 ---
        self.table_macros = TableWidget(self)
        self.table_macros.setColumnCount(3)  # 列数を3列に設定
        self.table_macros.setHorizontalHeaderLabels(
            [
                "マクロ名",
                "自己修復",
                "最終実行日時",
            ]
        )
        self.table_macros.setFont(self._font(10))
        self.table_macros.horizontalHeader().setFont(self._font(10, bold=True))

        # テーブルをメインレイアウトに追加（引数の1はウィンドウ拡大時にテーブルが引き伸ばされる伸縮率）
        main_layout.addWidget(self.table_macros, 1)

    def _setup_table(self):
        # テーブルのデザインとユーザーインタラクションの細かなチューニング
        self.table_macros.setShowGrid(False)  # グリッド線を非表示にしてモダンな見た目にする
        self.table_macros.setEditTriggers(
            QAbstractItemView.EditTrigger.NoEditTriggers
        )  # ダブルクリック等による直接編集を禁止
        self.table_macros.setSelectionBehavior(
            QAbstractItemView.SelectionBehavior.SelectRows
        )  # セル単位ではなく、行単位で選択されるように設定
        self.table_macros.setSelectionMode(
            QAbstractItemView.SelectionMode.SingleSelection
        )  # 複数行の同時選択を禁止（1件のみ選択可能）

        # 左端の行番号ヘッダーを非表示にし、1行あたりの高さを58ピクセルに広げてゆとりを持たせる
        self.table_macros.verticalHeader().setVisible(False)
        self.table_macros.verticalHeader().setDefaultSectionSize(58)

        # 各列の幅の自動調整ルールを設定
        header = self.table_macros.horizontalHeader()
        header.setSectionResizeMode(0, QHeaderView.ResizeMode.Stretch)  # 1列目（マクロ名）は残りの幅いっぱいに広がる
        header.setSectionResizeMode(1, QHeaderView.ResizeMode.Fixed)    # 2列目は幅固定
        header.setSectionResizeMode(2, QHeaderView.ResizeMode.Fixed)    # 3列目も幅固定

        # 固定列の具体的なピクセル幅を指定
        self.table_macros.setColumnWidth(1, 115)  # 自己修復列
        self.table_macros.setColumnWidth(2, 200)  # 最終実行日時列

    def _bind_viewmodel(self):
        # 画面上のボタンがクリックされたら、対応するカスタムシグナルを発生させる
        self.btn_start_record.clicked.connect(
            self.start_record_requested.emit
        )
        self.btn_run_selected.clicked.connect(
            self.run_macro_requested.emit
        )
        self.btn_delete_selected.clicked.connect(
            self.delete_macro_requested.emit
        )

        # テーブルの選択行が変更されたら、内部処理メソッドを呼び出す
        self.table_macros.itemSelectionChanged.connect(
            self._on_table_selection_changed
        )

        # ViewModel側の状態変更（マクロリスト更新、実行可能状態の変化）を検知してUIを再描画する
        self.viewmodel.macros_updated.connect(self._render_table)
        self.viewmodel.can_run_changed.connect(
            self._update_control_buttons_state
        )

    @Slot()
    def _on_table_selection_changed(self):
        # 現在テーブルで選択されているアイテムのリストを取得
        selected_items = self.table_macros.selectedItems()

        # 何も選択されていない場合は、ViewModelに空文字を渡して選択をクリア
        if not selected_items:
            self.viewmodel.select_macro("")
            return

        # 選択されたセルの行番号を取得し、その行の0番目（マクロ名）の文字列を取り出してViewModelに通知
        row = selected_items[0].row()
        macro_name_item = self.table_macros.item(row, 0)

        if macro_name_item:
            self.viewmodel.select_macro(macro_name_item.text())

    @Slot(bool)
    def _update_control_buttons_state(self, can_run: bool):
        # マクロが選択されているかどうかに応じて、実行ボタンと削除ボタンの有効/無効を切り替える
        self.btn_run_selected.setEnabled(can_run)
        self.btn_delete_selected.setEnabled(can_run)

    @Slot(list)
    def _render_table(self, macros: list):
        # テーブルを一度クリアし、受け取ったマクロの総数に合わせて行数を再設定
        self.table_macros.clearContents()
        self.table_macros.setRowCount(len(macros))

        # 基本フォントの設定をテーブルに適用
        item_font = self._font(10)
        self.table_macros.setFont(item_font)
        self.table_macros.horizontalHeader().setFont(
            self._font(10, bold=True)
        )

        # 取得したマクロデータを1件ずつループ処理してテーブルのセルに配置
        for row, macro in enumerate(macros):
            # 1列目: マクロ名（左寄せ、上下中央配置）
            macro_name = QTableWidgetItem(macro.name)
            macro_name.setFont(item_font)
            macro_name.setTextAlignment(
                Qt.AlignmentFlag.AlignLeft
                | Qt.AlignmentFlag.AlignVCenter
            )
            self.table_macros.setItem(row, 0, macro_name)

            # 2列目: 自己修復ステータス（中央寄せ、編集不可設定）
            heals = QTableWidgetItem(macro.heals)
            heals.setFont(item_font)
            heals.setTextAlignment(
                Qt.AlignmentFlag.AlignCenter
            )
            heals.setFlags(
                heals.flags()
                & ~Qt.ItemFlag.ItemIsEditable  # ビット反転を用いて編集不可フラグを設定
            )
            self.table_macros.setItem(row, 1, heals)

            # 3列目: 最終実行日時（中央寄せ、編集不可設定）
            last_run = QTableWidgetItem(macro.last_run)
            last_run.setFont(item_font)
            last_run.setTextAlignment(
                Qt.AlignmentFlag.AlignCenter
            )
            last_run.setFlags(
                last_run.flags()
                & ~Qt.ItemFlag.ItemIsEditable
            )
            self.table_macros.setItem(row, 2, last_run)

        # テーブル更新後は一旦選択状態を安全にリセットする
        self.viewmodel.select_macro("")


class SettingScreen(QWidget):
    """設定画面への入口。"""

    # 外部へ設定ダイアログの表示を要求するためのシグナル
    open_settings_requested = Signal()

    def __init__(self, parent=None):
        super().__init__(parent)

        self.setObjectName("SettingScreen")

        # 画面全体のメインレイアウト（縦並び）
        main_layout = QVBoxLayout(self)
        main_layout.setContentsMargins(40, 40, 40, 40)
        main_layout.setSpacing(25)

        # ラベル
        self.title_label = SubtitleLabel("設定画面", self)
        main_layout.addWidget(self.title_label)

        # 接続先モード設定を配置するための垂直レイアウト
        mode_layout = QVBoxLayout()
        mode_layout.setSpacing(10)

        mode_title = BodyLabel("AI 接続先設定", self)
        # テーマ対応
        # 現在のラベルフォントを取得し、太字に加工して再設定
        font_mode = mode_title.font()
        font_mode.setBold(True)
        mode_title.setFont(font_mode)
        mode_layout.addWidget(mode_title)

        # ラジオボタンの作成
        self.local_ai_radio = RadioButton("ローカルAI", self)
        self.cloud_ai_radio = RadioButton("クラウドAI", self)
        
        # デフォではローカルAIにチェック
        self.local_ai_radio.setChecked(True)

        # 2つのラジオボタンを排他選択（片方を選ぶともう片方が消える）にするためグループ化
        self.mode_group = QButtonGroup(self)
        self.mode_group.addButton(self.local_ai_radio)
        self.mode_group.addButton(self.cloud_ai_radio)
        
        mode_layout.addWidget(self.local_ai_radio)
        mode_layout.addWidget(self.cloud_ai_radio)
        
        main_layout.addLayout(mode_layout)
        
        # サーバーのホスト名入力をきれいに並べるための格子状（グリッド）レイアウト
        server_layout = QGridLayout()
        server_layout.setVerticalSpacing(15)
        server_layout.setHorizontalSpacing(15)
        
        # --- LLMサーバ設定項目 ---
        llm_title = BodyLabel("LLMサーバ設定", self)
        font_llm = llm_title.font()
        font_llm.setBold(True)
        llm_title.setFont(font_llm)
        
        self.llm_host_input = LineEdit(self)
        self.llm_host_input.setPlaceholderText("localhost")  # 未入力時のヒント文字列
        
        # 0行目の0列目にラベル、0行目の1列目に入力欄を配置
        server_layout.addWidget(llm_title, 0, 0)
        server_layout.addWidget(self.llm_host_input, 0, 1)
        
        # --- 画面解析サーバ設定項目 ---
        vision_title = BodyLabel("画面解析 サーバ設定", self)
        font_vision = vision_title.font()
        font_vision.setBold(True)
        vision_title.setFont(font_vision)
        
        self.vision_host_input = LineEdit(self)
        self.vision_host_input.setPlaceholderText("localhost")
        
        # 1行目の0列目にラベル、1行目の1列目に入力欄を配置
        server_layout.addWidget(vision_title, 1, 0)
        server_layout.addWidget(self.vision_host_input, 1, 1)
        # 入力欄がある1列目を、ウィンドウ幅に合わせて横いっぱいに引き伸ばす設定
        server_layout.setColumnStretch(1, 1)
        
        main_layout.addLayout(server_layout)
        main_layout.addStretch(1)  # ボタンエリアを最下部に押し下げるための可変スペース
        
        # --- 最下部の操作ボタンエリア（横並び） ---
        bottom_layout = QHBoxLayout()
        
        # 同期アイコン（SYNC）付きの接続テストボタンを作成
        self.test_button = PushButton(FluentIcon.SYNC, "接続テスト", self)
        self.cancel_button = PushButton("キャンセル", self)
        self.save_button = PrimaryPushButton("保存", self)
        
        # 左側にテストボタン、右側にキャンセル・保存ボタンを寄せて配置
        bottom_layout.addWidget(self.test_button)
        bottom_layout.addStretch(1)  # 左右を離すためのスペース
        bottom_layout.addWidget(self.cancel_button)
        bottom_layout.addWidget(self.save_button)
        
        main_layout.addLayout(bottom_layout)
        
        # self.cancel_button.clicked.connect()  # 将来的なキャンセル処理用（現在コメントアウト）
        
        # 接続テストボタンクリック時の処理を接続
        self.test_button.clicked.connect(self.connect_test)
        
    def connect_test(self):
        # ユーザーにモック（仮設定）の通知をポップアップメッセージで表示
        QMessageBox.information(self, "接続テスト", "接続テスト要求を受け付けました（バックエンド未結合）")


class MainWindow(FluentWindow):
    """QFluentWidgets ベースのメインウィンドウ。"""

    def __init__(self, viewmodel: MainViewModel):
        super().__init__()

        # ライトテーマをデフォルトとして適用
        setTheme(Theme.LIGHT)

        # 共通のロジックを持つ ViewModel を保持
        self.viewmodel = viewmodel

        # 各種モーダル・モーダレスダイアログのインスタンス管理用変数
        self.record_dialog = None
        self.running_dialog = None
        self.progress_dialog = None
        self.settings_dialog = None

        # ウィンドウのタイトルと基本・最小サイズを定義
        self.setWindowTitle("Macro Manager")
        self.resize(1080, 720)
        self.setMinimumSize(900, 620)

        # アプリケーション全体の標準フォントスタイルを設定
        app_font = QFont("Yu Gothic UI", 10)
        app_font.setStyleHint(QFont.StyleHint.SansSerif)
        self.setFont(app_font)

        # 内部画面（ホーム画面と設定画面）のインスタンスを生成
        self.home_screen = MainScreen(self.viewmodel, self)
        self.settings_screen = SettingScreen(self)

        # 各子画面から発火した要求シグナルを、MainWindow自身のダイアログ開閉メソッドへルーティング接続
        self.home_screen.start_record_requested.connect(
            self.open_record_dialog
        )
        self.home_screen.run_macro_requested.connect(
            self.open_running_dialog
        )
        self.home_screen.delete_macro_requested.connect(
            self._on_delete_selected_clicked
        )
        self.settings_screen.open_settings_requested.connect(
            self.open_settings_dialog
        )

        # 左側のナビゲーションメニューに「ホーム」画面を追加
        self.addSubInterface(
            self.home_screen,
            FluentIcon.HOME,
            "ホーム",
        )
        # 左側のナビゲーションメニューに「設定」画面を追加
        self.addSubInterface(
            self.settings_screen,
            FluentIcon.SETTING,
            "設定",
        )

        # ViewModelからのマクロ処理イベント（実行完了、生成完了、ショートカットキーによる記録停止）を受けてUIを復帰させる接続
        self.viewmodel.execution_finished.connect(
            self._on_execution_finished
        )
        self.viewmodel.generation_finished.connect(
            self._on_generation_finished
        )
        self.viewmodel.recording_stopped_by_shortcut.connect(
            self._on_recording_stopped_by_shortcut
        )

    @Slot()
    def _on_delete_selected_clicked(self):
        # 現在選択されているマクロ名をViewModelから取得
        selected_macro_name = self.viewmodel._selected_macro

        if not selected_macro_name:
            return

        # 本当に削除して良いかユーザーに確認する質問ダイアログ（はい・いいえ）を表示
        result = QMessageBox.question(
            self,
            "削除の確認",
            f"「{selected_macro_name}」を完全に削除してもよろしいですか？\n"
            "この操作は元に戻せません。",
            QMessageBox.Yes | QMessageBox.No,
            QMessageBox.No,  # デフォルトで「いいえ」を選択状態にする
        )

        # ユーザーが「はい」を選んだ場合のみ、削除処理を実行
        if result == QMessageBox.Yes:
            self.viewmodel.delete_macro(selected_macro_name)

    @Slot()
    def _on_recording_stopped_by_shortcut(self):
        # キーボードショートカット等で外部から記録停止された場合、記録中ダイアログが存在すれば閉じる
        if self.record_dialog:
            self.record_dialog.dialog.close()

        # 通常の停止処理へ移行
        self._on_recording_stopped()

    def open_record_dialog(self):
        try:
            # ViewModelにマクロ記録の開始を命令
            self.viewmodel.start_recording()

            # 記録中であることを示す小さなダイアログを表示（停止時のコールバック関数を渡す）
            self.record_dialog = RecordDialog(
                self,
                on_stop_callback=self._on_recording_stopped,
            )
            self.record_dialog.show()

            # 記録の邪魔にならないよう、メインウィンドウを一時的に非表示（隠す）にする
            self.hide()

        except Exception as error:
            # 万が一の起動エラー時はエラーメッセージを表示
            QMessageBox.critical(
                self,
                "エラー",
                f"記録の開始に失敗しました:\n{error}",
            )

    def _on_recording_stopped(self):
        try:
            # ViewModelにマクロ記録の停止を命令
            self.viewmodel.stop_recording()

            # 記録した操作からマクロコードをAI自動生成する間の「進捗（ぐるぐる）ダイアログ」を表示
            self.progress_dialog = ProgressDialog(
                self.viewmodel,
                self,
            )
            self.progress_dialog.show()

        except Exception as error:
            # エラー発生時はエラーを通知した上でメインウィンドウを再表示して復帰させる
            QMessageBox.critical(
                self,
                "エラー",
                f"記録の停止中にエラーが発生しました:\n{error}",
            )

            self.show()
            self.raise_()
            self.activateWindow()

    def open_running_dialog(self):
        try:
            # ViewModelに選択中マクロの実行開始を命令
            self.viewmodel.run_selected_macro()

            # マクロ実行中であることを示すダイアログを表示（緊急停止ボタン用のコールバック関数を提携）
            self.running_dialog = RunningDialog(
                self,
                on_stop_callback=self._on_emergency_stop_triggered,
            )
            self.running_dialog.show()

            # マクロ実行の邪魔にならないよう、メインウィンドウを一時的に隠す
            self.hide()

        except Exception as error:
            QMessageBox.critical(
                self,
                "エラー",
                f"実行の開始に失敗しました:\n{error}",
            )

    def _on_emergency_stop_triggered(self):
        try:
            # ユーザーが実行中ダイアログの停止を押した場合、ViewModelに緊急停止を命令
            self.viewmodel.trigger_emergency_stop()

        except Exception as error:
            QMessageBox.critical(
                self,
                "エラー",
                f"強制停止中にエラーが発生しました:\n{error}",
            )

    @Slot()
    def _on_execution_finished(self):
        # マクロの実行が正常または停止によって終了したときの処理
        if self.running_dialog:
            self.running_dialog.close_dialog()
            self.running_dialog = None

        # 隠していたメインウィンドウを再び前面に表示してユーザー操作を受け付ける状態に戻す
        self.show()
        self.raise_()          # ウィンドウの階層を最前面に引き上げる
        self.activateWindow()  # ウィンドウをアクティブ化（フォーカスを当てる）

    @Slot(bool, str)
    def _on_generation_finished(self, success: bool, message: str):
        # マクロのAI生成タスクが完了したときの処理
        if self.progress_dialog:
            self.progress_dialog.close()
            self.progress_dialog = None

        # 生成に失敗し、かつユーザーの意図的なキャンセルではない場合は警告を表示
        if not success and message != "キャンセルされました":
            QMessageBox.warning(
                self,
                "マクロ生成エラー",
                f"マクロの生成に失敗しました:\n{message}",
            )

        # 処理が終わったためメインウィンドウを再表示してフォーカスを戻す
        self.show()
        self.raise_()
        self.activateWindow()

    def open_settings_dialog(self):
        # 設定詳細ダイアログを生成し、ブロックモード（.exec()）で開く
        self.settings_dialog = SettingsDialog(self)
        self.settings_dialog.exec()