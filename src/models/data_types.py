# @role: システム全体で共有するデータ構造（統合データ、ワークフロー、各エンジンの生ログ）の型定義とバリデーションを行う。
# 
# 【参照元 (呼ばれる側)】
#   - core/recorder/* (記録データ生成時)
#   - core/generator/* (統合データ生成時)
#   - core/executor/* (ワークフロー読み込み時)
#   - engines/* (解析結果の返却時)
# 
# 【参照先 (呼ぶ側)】
#   - なし (外部依存を持たない純粋なデータモデル)
# 
# 【処理内容】
#   - pydantic を用いて、data.html に定義されたJSON構造（UI認識データ、入力操作データ、統合データ、ワークフロー）のモデルを定義する。
#   - 不正なデータが各モジュール間を行き来しないよう、厳密な型チェックと初期値の設定を行う。

from pydantic import BaseModel, Field
from typing import List, Optional

class Coordinates(BaseModel):
    x: int
    y: int

class BoundingBox(BaseModel):
    x: int
    y: int
    width: int
    height: int

# ※ 以下、各種モデル（YoloResult, OcrResult, InputLog, IntegratedEvent, Workflow 等）を追記していく