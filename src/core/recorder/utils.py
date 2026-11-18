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
    vk = getattr(key, "vk", None)
    # Why: Ctrl同時押し時にkey.charが制御文字(\x01~\x1a)へ化けるのを防ぎVKから英字を優先復元
    if vk is not None and 65 <= vk <= 90:
        return chr(vk).lower()

    try:
        if key.char is not None:
            ch = str(key.char)
            if len(ch) == 1 and 1 <= ord(ch) <= 26:
                return chr(ord(ch) + 96)
            return ch.lower()
    except AttributeError:
        pass

    # Why: テンキー(VK_NUMPAD0~9: 96~105)を通常数字キーへ正規化
    if vk is not None and 96 <= vk <= 105:
        return str(vk - 96)

    text = str(key)
    if text.startswith("Key."):
        text = text.replace("Key.", "")
    text = text.replace("cmd", "win")
    if text.startswith("<") and text.endswith(">") and text[1:-1].isdigit():
        num_vk = int(text[1:-1])
        if 96 <= num_vk <= 105:
            return str(num_vk - 96)
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

def _canonicalize_modifier(key_name: str) -> str:
    k = key_name.lower()
    if k in ("ctrl_l", "ctrl_r"):
        return "ctrl"
    if k in ("alt_l", "alt_r"):
        return "alt"
    if k in ("shift_l", "shift_r"):
        return "shift"
    if k in ("win_l", "win_r", "windows", "cmd"):
        return "win"
    return k

def make_combo_text(keys: list[str]) -> str:
    normalized: list[str] = []
    for k in keys:
        canon = _canonicalize_modifier(k)
        if canon not in normalized:
            normalized.append(canon)
    return "+".join(normalized)

def should_record_key_combo(pressed_keys: set[str], current_key: str) -> bool:
    if current_key in MODIFIER_KEYS:
        return False
    has_modifier = any(pressed_key in MODIFIER_KEYS for pressed_key in pressed_keys)
    if not has_modifier:
        return False
    # Why: Shift+英字単体は大文字入力のためコンボではなく通常キーとして記録
    only_shift = all(pk in ["shift", "shift_l", "shift_r"] for pk in pressed_keys if pk in MODIFIER_KEYS)
    if only_shift and len(current_key) == 1 and current_key.isalpha():
        return False
    has_non_shift_mod = any(
        pk in ["ctrl", "ctrl_l", "ctrl_r", "alt", "alt_l", "alt_r", "win", "win_l", "win_r", "windows"]
        for pk in pressed_keys
    )
    return has_non_shift_mod or current_key in COMBO_TRIGGER_KEYS

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
