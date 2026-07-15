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
    x: int
    y: int

class Size(BaseModel):
    width: int
    height: int

class BoundingBox(BaseModel):
    x: int
    y: int
    width: int
    height: int


# ====================================================================
# 5.2. 生データ構造定義（一時データ）
# ====================================================================

class UiAnalysisData(BaseModel):
    timestamp: int = Field(..., description="画像を取得・解析したUnixタイムスタンプ")
    boundingBox: BoundingBox = Field(..., description="認識したUI要素の座標情報")
    type: str = Field(..., description="物体認識されたUIの種類(button, dropdown, inputなど)")
    confidence: float = Field(..., ge=0.0, le=1.0, description="UI認識の信頼度・精度")

class YoloEngineOutput(BaseModel):
    uiAnalysis: UiAnalysisData


class TextAnalysisData(BaseModel):
    timestamp: int = Field(..., description="画像を取得・解析したUnixタイムスタンプ")
    boundingBox: BoundingBox = Field(..., description="認識したテキスト領域の座標情報")
    content: str = Field(..., description="OCRエンジンにより読み取られた文字列内容")
    confidence: float = Field(..., ge=0.0, le=1.0, description="OCRによるテキスト認識の信頼度・精度")

class OcrEngineOutput(BaseModel):
    textAnalysis: TextAnalysisData


class InputLogData(BaseModel):
    timestamp: int = Field(..., description="OSレベルで操作をフック・検出したUnixタイムスタンプ")
    type: str = Field(..., description="入力操作の種類（click_down, key_down 等）")
    content: str = Field(..., description="具体的な入力内容（left_click, Enter, または入力文字など）")
    windowName: str = Field(..., description="操作対象となったアプリケーションのウィンドウタイトル名")
    windowSize: Size = Field(..., description="対象ウィンドウの全体サイズ")
    windowCoordinates: Coordinates = Field(..., description="対象ウィンドウのデスクトップ上における絶対座標")
    cursorCoordinates: Optional[Coordinates] = Field(None, description="操作が実行された瞬間のマウスカーソルの絶対座標（キーボード操作時などは省略可）")
    appSpecificContext: Optional[dict] = Field(None, description="アプリ固有の詳細コンテキスト情報（Excelのセル内容、ブラウザのURLなど）")

class PythonOsHookOutput(BaseModel):
    inputLog: InputLogData


# ====================================================================
# 5.4. 統合データ構造定義 (integrated.json)
# ====================================================================

class ActionDetail(BaseModel):
    inputType: str = Field(..., description="入力タイプ（click_down, key_down 等）")
    inputValue: str = Field(..., description="入力内容（left_click, Enter 等）")
    cursorRelativeCoordinates: Optional[Coordinates] = Field(None, description="対象ウィンドウ内でのカーソル相対座標（キーボード操作時などは省略可）")
    diffRatio: float = Field(..., description="操作による前画面変化率（最速化の待機判定、および低変化率画像の間引き処理に使用）")

class ContextComponent(BaseModel):
    type: str = Field(..., description="コンテキストの種類（text, icon 等）")
    content: str = Field(..., description="読み取られたテキストや記号の種類名")
    relativeBoundingBox: BoundingBox = Field(..., description="対象ウィンドウ内での相対座標とサイズ")
    confidence: float = Field(..., ge=0.0, le=1.0, description="認識精度")
    parentRelevance: float = Field(..., ge=0.0, le=1.0, description="親要素（対象UI）との距離や意味合いに基づく関連度")

class InteractedUiElement(BaseModel):
    type: str = Field(..., description="UIの種類（button, input 等）")
    relativeBoundingBox: BoundingBox = Field(..., description="対象ウィンドウの左上を原点(0,0)とした相対座標とサイズ")
    confidence: float = Field(..., ge=0.0, le=1.0, description="AIによるUI認識精度")
    action: Optional[ActionDetail] = Field(None, description="当該UIに対して行われた入力操作の詳細")
    context: List[ContextComponent] = Field(default_factory=list, description="UI要素を補足する周辺情報の配列")
    children: List['InteractedUiElement'] = Field(default_factory=list, description="子UI要素の配列（階層構造表現用）")

class WindowContext(BaseModel):
    name: str = Field(..., description="操作対象となったアクティブウィンドウの名前（タイトル）")
    size: Size = Field(..., description="ウィンドウのサイズ")
    coordinates: Coordinates = Field(..., description="ウィンドウの画面上の絶対座標")
    UIs: List[InteractedUiElement] = Field(default_factory=list, description="ウィンドウ内で認識・操作されたUI要素の配列")

class IntegratedEvent(BaseModel):
    id: str = Field(..., description="統合ログの一意なID (例: evt_001)")
    timestamp: int = Field(..., description="アクション実行時のUnixタイムスタンプ")
    window: WindowContext = Field(..., description="ウィンドウおよび配下のUI要素ツリー情報")


# ====================================================================
# 5.6. ワークフローデータ構造定義 (workflow.json)
# ====================================================================

class UniversalSelector(BaseModel):
    semantic_role: Optional[str] = Field(None, description="要素の持つ意味的役割")
    text_contains: Optional[str] = Field(None, description="含むべきテキスト")
    image_template: Optional[str] = Field(None, description="画像テンプレートのパス")
    absolute_coordinates: Optional[Coordinates] = Field(None, description="絶対座標")

class ActionParameters(BaseModel):
    target: Optional[UniversalSelector] = None
    destination: Optional[UniversalSelector] = None
    button: Optional[str] = None
    modifiers: List[str] = Field(default_factory=list)
    key: Optional[str] = None
    text: Optional[str] = None
    condition: Optional[str] = None
    timeout_ms: Optional[int] = None
    loop_count: Optional[int] = Field(None, description='ループの実行回数')
    loop_variables: Optional[dict] = Field(None, description='ループごとの差分変数（例: {"y_offset": 30, "index_step": 1}）')

class WorkflowCommandAction(BaseModel):
    command: str = Field(..., description="システムのルートコマンド (例: MOUSE_CLICK, TYPE_TEXT)")
    parameters: ActionParameters = Field(..., description="コマンドの実行に必要なペイロード")

class WorkflowStepContext(BaseModel):
    active_window_name: Optional[str] = Field(None, description="アクティブなウィンドウ名")

class WorkflowStep(BaseModel):
    step_id: int = Field(..., description="ステップの連番")
    intent: str = Field(..., description="標準化されたアクション分類")
    description: str = Field(..., description="自然言語の文章（英語）")
    context: WorkflowStepContext = Field(..., description="前提条件")
    action: WorkflowCommandAction = Field(..., description="実行内容")
    fallback_raw_events: List[str] = Field(default_factory=list, description="根拠となった生イベントID")

class WorkflowMetadata(BaseModel):
    os: str = Field(default="Windows", description="実行OS")
    resolution: Size = Field(..., description="画面解像度")
    duration_ms: int = Field(default=0, description="所要時間")

class Workflow(BaseModel):
    version: str = Field(default="2.0", description="スキーマバージョン")
    workflow_ID: str = Field(..., description="ワークフローの識別子")
    metadata: WorkflowMetadata = Field(..., description="実行環境メタデータ")
    steps: List[WorkflowStep] = Field(default_factory=list, description="作業単位のステップ配列")


# ====================================================================
# Phase 3: 合成データ・品質ゲート用データ構造
# ====================================================================

class QualityGateJudgment(BaseModel):
    status: Literal['PASS', 'FAIL'] = Field(..., description="評価ステータス")
    reason: Optional[str] = Field(None, description="FAILの場合の具体的な理由や不整合の指摘")
    confidence: float = Field(..., ge=0.0, le=1.0, description="Judgeモデルの評価に対する確信度")

class SyntheticTrainingSample(BaseModel):
    sample_id: str = Field(..., description="サンプルの一意な識別子")
    input_context: IntegratedEvent = Field(..., description="プロンプト入力となる統合ログデータ")
    target_output: WorkflowStep = Field(..., description="LLMが生成すべき理想的なワークフロー出力")
    judgment: QualityGateJudgment = Field(..., description="品質ゲートを通過した際の評価証跡")


# ====================================================================
# UI表示用・状態管理用データ構造
# ====================================================================

class MacroSummary(BaseModel):
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
    ai_mode: str = Field(default='local', description='AIの動作モード（local または cloud）')
    
    llm_host: str = Field(default='127.0.0.1', description='マクロ生成用AI（LLM）の接続先（IPまたはホスト名）')
    llm_port: str = Field(default='8844', description='LLM APIのポート番号')
    
    cv_host: str = Field(default='127.0.0.1', description='Computer Vision API（YOLO/OCR）の接続先（IPまたはホスト名）')
    cv_port: str = Field(default='8843', description='Computer Vision APIのポート番号')
    
    vllm_host: str = Field(default='127.0.0.1', description='ファインチューニング済みローカルモデルを稼働させるvLLMの接続先')
    vllm_port: str = Field(default='8000', description='vLLM APIのポート番号')
    
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
        if not re.match(r'^[a-zA-Z0-9.-]+$', v):
            raise ValueError(f'Invalid host format: {v}')
        return v

    @field_validator('llm_port', 'cv_port', 'vllm_port')
    @classmethod
    def validate_port(cls, v: str) -> str:
        if not v.isdigit() or not (1 <= int(v) <= 65535):
            raise ValueError(f'Invalid port number: {v}')
        return v