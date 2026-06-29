# @role: 実行エンジンが直接解釈可能な、メソッド名と解決済み引数を持つマクロ用JSONの型定義。
#
# 【参照元 (呼ばれる側)】
#   - core/generator/* (LLMまたは変換ロジックによるマクロ用JSON生成時)
#   - core/executor/* (マクロ用JSONの読み込み・直接実行時)
#
# 【参照先 (呼ぶ側)】
#   - なし

from pydantic import BaseModel, Field
from typing import List, Union, Literal

class WaitArgs(BaseModel):
    duration: float = Field(..., description="Seconds to wait before next command.")

class WaitCommand(BaseModel):
    method: Literal["wait"] = "wait"
    args: WaitArgs

class ClickArgs(BaseModel):
    x: int = Field(..., description="Absolute X coordinate on screen.")
    y: int = Field(..., description="Absolute Y coordinate on screen.")
    button: Literal["left", "right", "middle"] = Field(default="left")

class ClickCommand(BaseModel):
    method: Literal["click"] = "click"
    args: ClickArgs

class TypeTextArgs(BaseModel):
    text: str = Field(..., description="Text string to type.")

class TypeTextCommand(BaseModel):
    method: Literal["type_text"] = "type_text"
    args: TypeTextArgs

class PressKeyArgs(BaseModel):
    key: str = Field(..., description="Special key name (e.g., enter, esc, tab).")

class PressKeyCommand(BaseModel):
    method: Literal["press_key"] = "press_key"
    args: PressKeyArgs

MacroCommand = Union[WaitCommand, ClickCommand, TypeTextCommand, PressKeyCommand]

class ExecutableMacro(BaseModel):
    macro_id: str = Field(..., description="Unique identifier for the macro.")
    commands: List[MacroCommand] = Field(default_factory=list, description="Ordered list of executable commands.")