# src/models/executable_macro.py
# @role: 実行エンジンが直接解釈可能な、メソッド名と解決済み引数を持つマクロ用JSONの型定義。
#
# 【参照元 (呼ばれる側)】
#   - core/generator/* (LLMまたは変換ロジックによるマクロ用JSON生成時)
#   - core/executor/* (マクロ用JSONの読み込み・直接実行時)
#
# 【参照先 (呼ぶ側)】
#   - なし

from pydantic import BaseModel, Field
from typing import List, Union, Literal, Optional

class WaitArgs(BaseModel):
    duration: float = Field(..., description="Seconds to wait before next command.")
    seq_vars: Optional[dict] = Field(default_factory=dict, description="Loop sequence variables.")

class WaitCommand(BaseModel):
    method: Literal["wait"] = "wait"
    args: WaitArgs

class ClickArgs(BaseModel):
    x: int = Field(..., description="Absolute X coordinate on screen.")
    y: int = Field(..., description="Absolute Y coordinate on screen.")
    button: Literal["left", "right", "middle"] = Field(default="left")
    clicks: Optional[int] = 1
    target_id: Optional[str] = None
    raw_event_id: Optional[str] = None
    seq_vars: Optional[dict] = Field(default_factory=dict, description="Loop sequence variables.")

class ClickCommand(BaseModel):
    method: Literal["click"] = "click"
    args: ClickArgs

class TypeTextArgs(BaseModel):
    text: str = Field(..., description="Text string to type.")
    target_id: Optional[str] = None
    raw_event_id: Optional[str] = None
    seq_vars: Optional[dict] = Field(default_factory=dict, description="Loop sequence variables.")

class TypeTextCommand(BaseModel):
    method: Literal["type_text"] = "type_text"
    args: TypeTextArgs

class PressKeyArgs(BaseModel):
    key: str = Field(..., description="Special key name (e.g., enter, esc, tab).")
    target_id: Optional[str] = None
    raw_event_id: Optional[str] = None
    seq_vars: Optional[dict] = Field(default_factory=dict, description="Loop sequence variables.")

class PressKeyCommand(BaseModel):
    method: Literal["press_key"] = "press_key"
    args: PressKeyArgs

class ActivateWindowArgs(BaseModel):
    window_title: str
    x: int
    y: int
    width: int
    height: int
    launch_cmd: str = ""
    target_id: Optional[str] = None
    raw_event_id: Optional[str] = None
    seq_vars: Optional[dict] = Field(default_factory=dict, description="Loop sequence variables.")

class ActivateWindowCommand(BaseModel):
    method: Literal["activate_window"] = "activate_window"
    args: ActivateWindowArgs

class MoveArgs(BaseModel):
    x: int
    y: int
    target_id: Optional[str] = None
    raw_event_id: Optional[str] = None
    seq_vars: Optional[dict] = Field(default_factory=dict, description="Loop sequence variables.")

class MoveCommand(BaseModel):
    method: Literal["move"] = "move"
    args: MoveArgs

class ScrollArgs(BaseModel):
    dx: float
    dy: float
    x: int
    y: int
    seq_vars: Optional[dict] = Field(default_factory=dict, description="Loop sequence variables.")

class ScrollCommand(BaseModel):
    method: Literal["scroll"] = "scroll"
    args: ScrollArgs

class LoopStartArgs(BaseModel):
    loop_count: int = Field(default=1, description="Number of times to repeat the loop.")
    target_id: Optional[str] = None
    raw_event_id: Optional[str] = None

class LoopStartCommand(BaseModel):
    method: Literal["loop_start"] = "loop_start"
    args: LoopStartArgs

class LoopEndArgs(BaseModel):
    target_id: Optional[str] = None
    raw_event_id: Optional[str] = None

class LoopEndCommand(BaseModel):
    method: Literal["loop_end"] = "loop_end"
    args: Optional[LoopEndArgs] = Field(default_factory=LoopEndArgs)

MacroCommand = Union[
    WaitCommand, 
    ClickCommand, 
    TypeTextCommand, 
    PressKeyCommand, 
    ActivateWindowCommand, 
    MoveCommand, 
    ScrollCommand,
    LoopStartCommand,
    LoopEndCommand
]

class ExecutableMacro(BaseModel):
    macro_id: str = Field(..., description="Unique identifier for the macro.")
    target_application: str = Field(default="auto_generated")
    commands: List[MacroCommand] = Field(default_factory=list, description="Ordered list of executable commands.")