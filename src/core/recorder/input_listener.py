# @role: pynputを利用したOSレベルのマウス・キーボード入力の監視と、イベントキューへの非同期プッシュを行う。

import time
import math
import threading
import logging
from core.recorder.state import state
from core.recorder.utils import key_to_string, sorted_combo_keys, make_combo_text, should_record_key_combo, MODIFIER_KEYS
from core.recorder.event_processor import enqueue_key_event, record_scroll_event
from core.recorder.ime_detector import is_ime_active
from core.recorder.romaji_converter import to_hiragana

logger = logging.getLogger(__name__)

MIN_DISTANCE_FOR_VECTOR = 40
CORNER_ANGLE_THRESHOLD = 20

def _trigger_field_search(trigger_reason: str):
    buffer = getattr(state, "typing_buffer", "")
    if not buffer:
        return

    ime_on = is_ime_active()
    target_text = to_hiragana(buffer) if ime_on else buffer
    
    # 背景: IME状態をイベントプロセッサに伝達する
    enqueue_key_event("text_field_search", target_text, capture_now=True, ime_active=ime_on)
    logger.debug("テキストフィールド探索イベントを発火しました [%s]: %s (IME: %s)", trigger_reason, target_text, ime_on)

def _flush_typing_buffer(trigger_reason: str):
    if not getattr(state, "typing_buffer", ""):
        return

    ime_on = is_ime_active()
    # 背景: IME状態をイベントプロセッサに伝達する
    enqueue_key_event("text_candidate_confirm", "", capture_now=True, ime_active=ime_on)
    state.typing_buffer = ""
    logger.debug("タイピングバッファを確定しました [%s]", trigger_reason)

def on_move(x, y):
    if not state.is_recording or state.is_stopping: return
    current_time = time.time()
    
    if not state.mouse_path:
        state.mouse_path.append((x, y, current_time))
    else:
        last_x, last_y, _ = state.mouse_path[-1]
        if math.hypot(x - last_x, y - last_y) >= MIN_DISTANCE_FOR_VECTOR:
            state.mouse_path.append((x, y, current_time))
            if len(state.mouse_path) >= 3:
                p1, p2, p3 = state.mouse_path[-3], state.mouse_path[-2], state.mouse_path[-1]
                v1, v2 = (p2[0] - p1[0], p2[1] - p1[1]), (p3[0] - p2[0], p3[1] - p2[1])
                dot = v1[0]*v2[0] + v1[1]*v2[1]
                mag1, mag2 = math.hypot(*v1), math.hypot(*v2)
                if mag1 > 0 and mag2 > 0:
                    angle = math.degrees(math.acos(max(-1.0, min(1.0, dot / (mag1 * mag2)))))
                    if angle >= CORNER_ANGLE_THRESHOLD:
                        state.mouse_event_queue.put({"type": "hover", "x": p2[0], "y": p2[1]})
                state.mouse_path.pop(0)

def on_click(x, y, button, pressed):
    state.cancel_hover()
    if not state.is_recording or state.is_stopping: return
    
    if pressed:
        _flush_typing_buffer(trigger_reason="mouse_click")
        
    state.mouse_event_queue.put({"type": "click", "x": x, "y": y, "button": button, "pressed": pressed})

def on_scroll(x, y, dx, dy):
    state.cancel_hover()
    if state.is_stopping: return
    try: 
        record_scroll_event(int(x), int(y), float(dx), float(dy), source="pynput")
    except Exception: 
        logger.exception("スクロールイベントの記録に失敗しました")

def on_press(key):
    state.cancel_hover()
    if not state.is_recording or state.is_stopping: 
        return False

    key_text = key_to_string(key)
    
    if key_text in ("\\", "\x1c"):
        with state.pressed_keys_lock:
            has_ctrl = any(pk in ["ctrl", "ctrl_l", "ctrl_r"] for pk in state.pressed_keys)
        if has_ctrl:
            if not state.is_stopping:
                state.is_stopping = True
                logger.info("input_listener: Ctrl + \\ detected. Triggering safe stop.")
                
                if state.shortcut_stop_callback:
                    threading.Thread(target=state.shortcut_stop_callback, daemon=True).start()
                else:
                    import core.recorder.os_hook as hook
                    threading.Thread(target=hook.stop_recording, daemon=True).start()
            return False 

    ime_on = is_ime_active()
    current_buffer = getattr(state, "typing_buffer", "")

    is_text_input = False
    if key_text == "space" and not ime_on:
        state.typing_buffer = current_buffer + " "
        is_text_input = True
    elif len(key_text) == 1 and key_text.isprintable():
        state.typing_buffer = current_buffer + key_text.lower()
        is_text_input = True
    elif key_text == "backspace" and current_buffer:
        state.typing_buffer = current_buffer[:-1]
        is_text_input = True

    if is_text_input:
        _trigger_field_search(trigger_reason=f"typing_{key_text}")

    flush_triggers = ["enter", "tab"]
    if ime_on:
        flush_triggers.append("space")

    if key_text in flush_triggers:
        _flush_typing_buffer(trigger_reason=key_text)

    try:
        with state.pressed_keys_lock:
            state.pressed_keys.add(key_text)
            current_keys = set(state.pressed_keys)

        capture_now = (key_text == "enter")
        if should_record_key_combo(current_keys, key_text):
            combo_keys = sorted_combo_keys(current_keys)
            combo_text = make_combo_text(combo_keys)
            if combo_text not in state.logged_combo_keys:
                state.logged_combo_keys.add(combo_text)
                # 背景: IME状態をイベントプロセッサに伝達する
                enqueue_key_event("key_combo", key_text, combo_keys, capture_now, ime_active=ime_on)
            return

        if key_text in MODIFIER_KEYS and not key_text.startswith("win"): return
        # 背景: IME状態をイベントプロセッサに伝達する
        enqueue_key_event("key_press", key_text, capture_now=capture_now, ime_active=ime_on)
    except Exception:
        logger.exception("キーフック処理中にエラーが発生しました")

def on_release(key):
    key_text = key_to_string(key)
    with state.pressed_keys_lock:
        state.pressed_keys.discard(key_text)
        if not state.pressed_keys:
            state.logged_combo_keys.clear()