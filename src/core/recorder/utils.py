# @role: キー入力の正規化やコンボキーの判定、距離計算など、記録処理で用いられる純粋なユーティリティ関数群。

from datetime import datetime
import math

MODIFIER_KEYS = {
    "ctrl", "ctrl_l", "ctrl_r", "alt", "alt_l", "alt_r", 
    "shift", "shift_l", "shift_r", "win", "win_l", "win_r", "windows"
}

COMBO_TRIGGER_KEYS = {
    "tab", "enter", "space", "esc", "f1", "f2", "f3", "f4", "f5", 
    "f6", "f7", "f8", "f9", "f10", "f11", "f12", 
    "a", "c", "v", "x", "z", "y", "s", "n", "o", "p", "r", "t", "w", "l", "\\"
}

def now_datetime() -> datetime:
    return datetime.now().astimezone()

def mouse_button_to_string(button) -> str:
    text = str(button)
    if "." in text:
        return text.split(".")[-1]
    return text

def normalize_key_name(key) -> str:
    try:
        if key.char is not None:
            return str(key.char).lower()
    except AttributeError:
        pass

    text = str(key)
    if text.startswith("Key."):
        text = text.replace("Key.", "")
    text = text.replace("cmd", "win")
    return text.lower()

def key_to_string(key) -> str:
    return normalize_key_name(key)

def sorted_combo_keys(keys: set[str]) -> list[str]:
    order = ["ctrl", "ctrl_l", "ctrl_r", "alt", "alt_l", "alt_r", "shift", "shift_l", "shift_r", "win", "win_l", "win_r"]
    def sort_key(value: str):
        if value in order:
            return 0, order.index(value)
        return 1, value
    return sorted(list(keys), key=sort_key)

def make_combo_text(keys: list[str]) -> str:
    return "+".join(keys)

def should_record_key_combo(pressed_keys: set[str], current_key: str) -> bool:
    has_modifier = any(pressed_key in MODIFIER_KEYS for pressed_key in pressed_keys)
    return has_modifier and current_key in COMBO_TRIGGER_KEYS

def calculate_distance(x1: int, y1: int, x2: int, y2: int) -> float:
    return math.hypot(x2 - x1, y2 - y1)

def serialize_ui_rect(ui_rect: dict | None) -> dict | None:
    if ui_rect is None:
        return None

    return {
        "left": int(ui_rect.get("left", 0)),
        "top": int(ui_rect.get("top", 0)),
        "right": int(ui_rect.get("right", 0)),
        "bottom": int(ui_rect.get("bottom", 0)),
        "width": int(ui_rect.get("width", 0)),
        "height": int(ui_rect.get("height", 0)),
        "name": str(ui_rect.get("name", "")),
        "control_type": str(ui_rect.get("control_type", "")),
        "source": str(ui_rect.get("source", "")),
    }