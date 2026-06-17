# @role: システム全体で共有するデータ構造（一時生データ、コンテキスト統合データ、実行用ワークフロー、アプリケーション設定、および学習用合成データ）の型定義とバリデーションを統括するデータモデル層。
# 
# 【参照元 (呼ばれる側)】
#   - core/recorder/* (フック・スクショ等の生データ生成時)
#   - engines/* (YOLO/OCR等の解析結果返却時)
#   - core/generator/* (生ログから統合データ・ワークフローデータへの変換・生成時)
#   - core/executor/* (ワークフローデータの読み込み・実行時)
#   - ui/viewmodels/* (UI状態の型安全な管理)
# 
# 【参照先 (呼ぶ側)】
#   - なし (アーキテクチャの最下層として、他モジュールへの依存を持たない)

import re
from pydantic import BaseModel, Field, field_validator, SecretStr
from typing import List, Optional, Literal

# ====================================================================
# 共通・基本データ構造
# ====================================================================

class Coordinates(BaseModel):
    """画面上の絶対座標またはウィンドウ内の相対座標"""
    x: int
    y: int

class Size(BaseModel):
    """ウィンドウやオブジェクトの矩形サイズ"""
    width: int
    height: int

class BoundingBox(BaseModel):
    """検出されたUI要素またはテキスト領域の境界ボックス"""
    x: int
    y: int
    width: int
    height: int


# ====================================================================
# 5.2. 生データ構造定義（一時データ）
# ====================================================================

class UiAnalysisData(BaseModel):
    """5.2.1. UI認識データ（YOLO Engine）"""
    timestamp: int = Field(..., description="画像を取得・解析したUnixタイムスタンプ")
    boundingBox: BoundingBox = Field(..., description="認識したUI要素の座標情報")
    type: str = Field(..., description="物体認識されたUIの種類(button, dropdown, inputなど)")
    confidence: float = Field(..., ge=0.0, le=1.0, description="UI認識の信頼度・精度")

class YoloEngineOutput(BaseModel):
    """YOLO Engineがtemp/に出力するJSON全体のラップ構造"""
    uiAnalysis: UiAnalysisData


class TextAnalysisData(BaseModel):
    """5.2.2. テキスト認識データ（OCR Engine）"""
    timestamp: int = Field(..., description="画像を取得・解析したUnixタイムスタンプ")
    boundingBox: BoundingBox = Field(..., description="認識したテキスト領域の座標情報")
    content: str = Field(..., description="OCRエンジンにより読み取られた文字列内容")
    confidence: float = Field(..., ge=0.0, le=1.0, description="OCRによるテキスト認識の信頼度・精度")

class OcrEngineOutput(BaseModel):
    """OCR Engineがtemp/に出力するJSON全体のラップ構造"""
    textAnalysis: TextAnalysisData


class InputLogData(BaseModel):
    """5.2.3. 入力操作データ（Python OS Hook）"""
    timestamp: int = Field(..., description="OSレベルで操作をフック・検出したUnixタイムスタンプ")
    type: str = Field(..., description="入力操作の種類（click_down, key_down 等）")
    content: str = Field(..., description="具体的な入力内容（left_click, Enter, または入力文字など）")
    windowName: str = Field(..., description="操作対象となったアプリケーションのウィンドウタイトル名")
    windowSize: Size = Field(..., description="対象ウィンドウの全体サイズ")
    windowCoordinates: Coordinates = Field(..., description="対象ウィンドウのデスクトップ上における絶対座標")
    cursorCoordinates: Optional[Coordinates] = Field(None, description="操作が実行された瞬間のマウスカーソルの絶対座標（キーボード操作時などは省略可）")

class PythonOsHookOutput(BaseModel):
    """Python OS Hookがtemp/に出力するJSON全体のラップ構造"""
    inputLog: InputLogData


# ====================================================================
# 5.4. 統合データ構造定義 (integrated.json)
# ====================================================================

class ActionDetail(BaseModel):
    """UIに対して行われた入力操作の詳細コンテキスト"""
    inputType: str = Field(..., description="入力タイプ（click_down, key_down 等）")
    inputValue: str = Field(..., description="入力内容（left_click, Enter 等）")
    cursorRelativeCoordinates: Optional[Coordinates] = Field(None, description="対象ウィンドウ内でのカーソル相対座標（キーボード操作時などは省略可）")
    diffRatio: float = Field(..., description="操作による前画面変化率（最速化の待機判定、および低変化率画像の間引き処理に使用）")

class ContextComponent(BaseModel):
    """UI要素を補足する周辺情報（意味理解を助けるためのテキストやアイコン）"""
    type: str = Field(..., description="コンテキストの種類（text, icon 等）")
    content: str = Field(..., description="読み取られたテキストや記号の種類名")
    relativeBoundingBox: BoundingBox = Field(..., description="対象ウィンドウ内での相対座標とサイズ")
    confidence: float = Field(..., ge=0.0, le=1.0, description="認識精度")
    parentRelevance: float = Field(..., ge=0.0, le=1.0, description="親要素（対象UI）との距離や意味合いに基づく関連度")

class InteractedUiElement(BaseModel):
    """対象ウィンドウ内で認識され、実際に操作に関与したUI要素のデータ構造"""
    type: str = Field(..., description="UIの種類（button, input 等）")
    relativeBoundingBox: BoundingBox = Field(..., description="対象ウィンドウの左上を原点(0,0)とした相対座標とサイズ")
    confidence: float = Field(..., ge=0.0, le=1.0, description="AIによるUI認識精度")
    action: ActionDetail = Field(..., description="当該UIに対して行われた入力操作の詳細")
    context: List[ContextComponent] = Field(default_factory=list, description="UI要素を補足する周辺情報の配列")

class WindowContext(BaseModel):
    """操作時におけるアクティブウィンドウの全体コンテキスト"""
    name: str = Field(..., description="操作対象となったアクティブウィンドウの名前（タイトル）")
    size: Size = Field(..., description="ウィンドウのサイズ")
    coordinates: Coordinates = Field(..., description="ウィンドウの画面上の絶対座標")
    UIs: List[InteractedUiElement] = Field(default_factory=list, description="ウィンドウ内で認識・操作されたUI要素の配列")

class IntegratedEvent(BaseModel):
    """一時データをウィンドウ基準の階層型ツリーに統合したデータモデル"""
    id: str = Field(..., description="統合ログの一意なID (例: evt_001)")
    timestamp: int = Field(..., description="アクション実行時のUnixタイムスタンプ")
    window: WindowContext = Field(..., description="ウィンドウおよび配下のUI要素ツリー情報")


# ====================================================================
# 5.6. ワークフローデータ構造定義 (workflow.json)
# ====================================================================

class WorkflowAction(BaseModel):
    """実行エンジンが再現すべき具体的なユーザー操作内容の詳細"""
    type: str = Field(..., description="入力操作の種類（click, key_down, text_input など）")
    button: Optional[str] = Field(None, description="使用されたマウスボタン（left, right など。キー入力時は省略可）")
    modifiers: List[str] = Field(default_factory=list, description="同時に押下された修飾キー（ctrl, shift, alt など）の配列")

class InteractedElementContext(BaseModel):
    """ユーザーが実際に操作を加えたUI要素の静的・意味的情報（自己修復時の特徴マッチングに使用）"""
    element_id: str = Field(..., description="操作対象となったUI要素を特定する内部ID")
    ui_type: str = Field(..., description="要素のオブジェクトタイプ（button, input, checkbox 等）")
    semantic_role: str = Field(..., description="要素の持つ意味的役割（submit, cancel, search_box 等）")
    location_context: str = Field(..., description="配置上の視覚的コンテキスト（bottom_right, top_nav 等）")

class EventContext(BaseModel):
    """操作実行時における対象要素および画面の状態コンテキストラップ"""
    interacted_element: InteractedElementContext

class WorkflowEvent(BaseModel):
    """ワークフローを構成する一連の操作イベントの最小単位。画像ファイルとの紐付け規則を持つ。"""
    event_id: str = Field(..., description="各操作イベントの一意なID。画像ファイル名との紐付けにも使用。")
    timestamp: int = Field(..., description="イベントが検出または生成されたUnixタイムスタンプ")
    action: WorkflowAction = Field(..., description="実行される具体的な操作内容")
    context: EventContext = Field(..., description="操作対象要素のメタデータ情報")

class Workflow(BaseModel):
    """統合ログから抽出・生成され、実行エンジン(Executor)へと引き渡されるマクロシナリオの最上位構造"""
    workflow_ID: str = Field(..., description="ワークフロー（マクロ）を一意に識別するID")
    target_ID: str = Field(..., description="操作対象となる主要なアプリケーションの識別子ID")
    events: List[WorkflowEvent] = Field(default_factory=list, description="ワークフローを構成する一連の操作イベントの配列")


# ====================================================================
# Phase 3: 合成データ・品質ゲート用データ構造
# ====================================================================

class QualityGateJudgment(BaseModel):
    """独立したJudgeモデルによる生成データの品質評価結果"""
    status: Literal['PASS', 'FAIL'] = Field(..., description="評価ステータス")
    reason: Optional[str] = Field(None, description="FAILの場合の具体的な理由や不整合の指摘")
    confidence: float = Field(..., ge=0.0, le=1.0, description="Judgeモデルの評価に対する確信度")

class SyntheticTrainingSample(BaseModel):
    """LoRAファインチューニング用にストックされる、品質ゲートを通過した合成データ"""
    sample_id: str = Field(..., description="サンプルの一意な識別子")
    input_context: IntegratedEvent = Field(..., description="プロンプト入力となる統合ログデータ")
    target_output: WorkflowEvent = Field(..., description="LLMが生成すべき理想的なワークフロー出力")
    judgment: QualityGateJudgment = Field(..., description="品質ゲートを通過した際の評価証跡")


# ====================================================================
# UI表示用・状態管理用データ構造
# ====================================================================

class MacroSummary(BaseModel):
    """UIのメイン画面に表示するためのマクロ概要（メタデータと実行状態）"""
    name: str = Field(..., description="マクロの表示名")
    status: str = Field(..., description="直近の実行結果ステータス（success, warning, danger 等）")
    status_text: str = Field(..., description="UIに表示する結果のテキスト表現")
    heals: str = Field(..., description="自己修復の発動回数などのテキスト表現")
    heal_level: str = Field(..., description="自己修復のレベル（none, low, mid, high 等）")
    last_run: str = Field(..., description="最終実行日時のフォーマット済み文字列")


# ====================================================================
# 6.8. アプリケーション設定用データ構造 (config.json)
# ====================================================================

class AppConfig(BaseModel):
    """UIから設定され、config.jsonとして永続化されるシステム接続情報"""
    ai_mode: str = Field(default='local', description='AIの動作モード（local または cloud）')
    
    # マクロ生成用AI (Phase 1, 2)
    llm_host: str = Field(default='127.0.0.1', description='マクロ生成用AI（LLM）の接続先（IPまたはホスト名）')
    llm_port: str = Field(default='8844', description='LLM APIのポート番号')
    
    # Computer Vision (Phase 1, 2)
    cv_host: str = Field(default='127.0.0.1', description='Computer Vision API（YOLO/OCR）の接続先（IPまたはホスト名）')
    cv_port: str = Field(default='8843', description='Computer Vision APIのポート番号')
    
    # 最速化推論エンジン (Phase 3)
    vllm_host: str = Field(default='127.0.0.1', description='ファインチューニング済みローカルモデルを稼働させるvLLMの接続先')
    vllm_port: str = Field(default='8000', description='vLLM APIのポート番号')
    
    # データ生成パイプライン用APIキー (Phase 3 - 機密情報保護のためSecretStrを使用)
    generator_api_key: Optional[SecretStr] = Field(default=None, description='学習データ生成用大型モデルのAPIキー')
    judge_api_key: Optional[SecretStr] = Field(default=None, description='品質ゲート用独立モデルのAPIキー')

    @field_validator('ai_mode')
    @classmethod
    def validate_ai_mode(cls, v: str) -> str:
        if v not in ('local', 'cloud'):
            raise ValueError(f'Invalid ai_mode: {v}')
        return v

    @field_validator('llm_host', 'cv_host', 'vllm_host')
    @classmethod
    def sanitize_host(cls, v: str) -> str:
        # OSコマンドインジェクションやSSRF攻撃の抑止
        if not re.match(r'^[a-zA-Z0-9.-]+$', v):
            raise ValueError(f'Invalid host format: {v}')
        return v

    @field_validator('llm_port', 'cv_port', 'vllm_port')
    @classmethod
    def validate_port(cls, v: str) -> str:
        if not v.isdigit() or not (1 <= int(v) <= 65535):
            raise ValueError(f'Invalid port number: {v}')
        return v