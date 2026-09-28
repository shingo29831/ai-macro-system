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
from typing import List, Union, Literal, Optional, Any

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
    use_clipboard: Optional[bool] = Field(default=None, description="Inject text via clipboard paste (Ctrl+V) to avoid IME desync.")
    clear_before_typing: Optional[bool] = Field(default=False, description="Clear existing field text (Ctrl+A, Backspace) before typing.")
    ime_mode: Optional[Literal["auto", "on", "off"]] = Field(default="auto", description="Explicit IME mode control.")

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
    window_alias: Optional[str] = None
    mapped_hwnd: Optional[int] = None

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
    data_source: Optional[Literal["static", "excel", "variable"]] = Field(default="static", description="Loop data source type.")
    file_path: Optional[str] = Field(default=None, description="Path to Excel data file.")
    sheet_name: Optional[str] = Field(default=None, description="Sheet name for data loop.")
    start_row: Optional[int] = Field(default=2, description="1-based starting row index.")
    end_row: Optional[int] = Field(default=None, description="1-based ending row index.")
    status_column: Optional[str] = Field(default=None, description="Column to write execution status (e.g., 'E').")
    skip_completed: Optional[bool] = Field(default=True, description="Skip rows where status_column has value.")
    continue_on_error: Optional[bool] = Field(default=False, description="Continue next record if an error occurs in current row.")
    items_variable: Optional[str] = Field(default=None, description="Variable name holding list of items.")
    item_variable: Optional[str] = Field(default="row", description="Variable name to store current row/item dict.")

class LoopStartCommand(BaseModel):
    method: Literal["loop_start"] = "loop_start"
    args: LoopStartArgs

class LoopEndArgs(BaseModel):
    target_id: Optional[str] = None
    raw_event_id: Optional[str] = None

class LoopEndCommand(BaseModel):
    method: Literal["loop_end"] = "loop_end"
    args: Optional[LoopEndArgs] = Field(default_factory=LoopEndArgs)

class ExcelArgs(BaseModel):
    action: Literal[
        "open_workbook", "save_workbook", "close_workbook",
        "read_cell", "write_cell", "read_range", "write_range",
        "select_sheet", "add_sheet", "insert_row", "delete_row",
        "clear_range", "run_macro", "read_records", "update_status"
    ] = Field(..., description="Excel RPA action type.")
    file_path: Optional[str] = Field(default=None, description="Workbook path.")
    sheet_name: Optional[str] = Field(default=None, description="Target worksheet name.")
    cell: Optional[str] = Field(default=None, description="Target cell (e.g. A1).")
    range_address: Optional[str] = Field(default=None, description="Target range (e.g. A1:C10).")
    value: Optional[Any] = Field(default=None, description="Value or data matrix.")
    variable_name: Optional[str] = Field(default=None, description="Variable name to store output.")
    macro_name: Optional[str] = Field(default=None, description="VBA macro name to run.")
    close_save: Optional[bool] = Field(default=True, description="Save changes on workbook close.")
    target_id: Optional[str] = None
    raw_event_id: Optional[str] = None
    seq_vars: Optional[dict] = Field(default_factory=dict, description="Loop sequence variables.")
    engine: Optional[Literal["auto", "direct", "com"]] = Field(default="auto", description="Engine mode ('direct' uses openpyxl headless, 'com' uses Excel UI).")
    row_index: Optional[int] = Field(default=None, description="Target row index for update_status.")
    status_column: Optional[str] = Field(default=None, description="Target status column letter.")

class ExcelCommand(BaseModel):
    method: Literal["excel_action"] = "excel_action"
    args: ExcelArgs

class BrowserArgs(BaseModel):
    action: Literal[
        "open_url", "click_element", "type_text", "read_text",
        "read_attribute", "select_option", "set_checkbox",
        "wait_element", "execute_script", "close_tab", "switch_tab"
    ] = Field(..., description="Browser RPA action type.")
    url: Optional[str] = Field(default=None, description="Target URL for navigation.")
    selector: Optional[str] = Field(default=None, description="CSS selector or XPath for target element.")
    selector_type: Literal["css", "xpath", "text", "auto"] = Field(default="auto", description="Type of selector.")
    text: Optional[str] = Field(default=None, description="Text string to type or match.")
    value: Optional[Any] = Field(default=None, description="Value to set, option value, or checked state.")
    attribute_name: Optional[str] = Field(default=None, description="Attribute name to read (e.g. href, value).")
    variable_name: Optional[str] = Field(default=None, description="Variable name to store output.")
    script: Optional[str] = Field(default=None, description="JavaScript code snippet to execute.")
    timeout_sec: float = Field(default=10.0, description="Timeout in seconds for waiting.")
    clear_before_typing: bool = Field(default=True, description="Clear existing input before typing.")
    target_id: Optional[str] = None
    raw_event_id: Optional[str] = None
    seq_vars: Optional[dict] = Field(default_factory=dict, description="Loop sequence variables.")

class BrowserCommand(BaseModel):
    method: Literal["browser_action"] = "browser_action"
    args: BrowserArgs

MacroCommand = Union[
    WaitCommand, 
    ClickCommand, 
    TypeTextCommand, 
    PressKeyCommand, 
    ActivateWindowCommand, 
    MoveCommand, 
    ScrollCommand,
    LoopStartCommand,
    LoopEndCommand,
    ExcelCommand,
    BrowserCommand
]

class ExecutableMacro(BaseModel):
    macro_id: str = Field(..., description="Unique identifier for the macro.")
    target_application: str = Field(default="auto_generated")
    commands: List[MacroCommand] = Field(default_factory=list, description="Ordered list of executable commands.")