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

def _flush_typing_buffer(trigger_reason: str):
    """
    バッファリングされた入力文字列を確定し、OCR特定用のイベントを発火する。
    """
    buffer = getattr(state, "typing_buffer", "")
    if not buffer:
        return

    ime_on = is_ime_active()
    # 全角モード時はローマ字バッファをひらがなに変換して画面上のターゲット文字列とする
    target_text = to_hiragana(buffer) if ime_on else buffer
    
    # 画面のスクリーンショットを伴うテキスト候補イベントとして記録
    enqueue_key_event("text_candidate", target_text, capture_now=True)
    
    # バッファをクリア
    state.typing_buffer = ""
    logger.debug("タイピングバッファをフラッシュしました [%s]: %s (IME: %s)", trigger_reason, target_text, ime_on)

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
        # クリックによりフォーカスが外れる直前にバッファを確定
        _flush_typing_buffer(trigger_reason="mouse_click")
        
    state.mouse_event_queue.put({"type": "click", "x": x, "y": y, "button": button, "pressed": pressed})

def on_scroll(x, y, dx, dy):
    state.cancel_hover()
    # ネイティブフック動作中であっても、タッチパッドの互換イベントを拾うために排他処理を解除
    if state.is_stopping: return
    try: 
        record_scroll_event(int(x), int(y), float(dx), float(dy), source="pynput")
    except Exception: 
        logger.exception("スクロールイベントの記録に失敗しました")

def on_press(key):
    state.cancel_hover()
    if not state.is_recording or state.is_stopping: return

    key_text = key_to_string(key)
    
    if key_text in ("\\", "\x1c"):
        with state.pressed_keys_lock:
            has_ctrl = any(pk in ["ctrl", "ctrl_l", "ctrl_r"] for pk in state.pressed_keys)
        if has_ctrl:
            if not state.is_stopping:
                state.is_stopping = True
                logger.info("input_listener: Ctrl + \\ が押されたため記録を停止します")
                if state.shortcut_stop_callback:
                    logger.info("input_listener: ViewModelのコールバックを呼び出します")
                    state.shortcut_stop_callback()
                else:
                    logger.info("input_listener: UIコールバックが未登録のため単体停止を実行します")
                    import core.recorder.os_hook as hook
                    threading.Thread(target=hook.stop_recording, daemon=True).start()
            return  # Falseは絶対に返さない(スレッド自爆防止)

    # --- タイピングバッファの管理 ---
    current_buffer = getattr(state, "typing_buffer", "")
    if len(key_text) == 1 and key_text.isprintable():
        state.typing_buffer = current_buffer + key_text.lower()
    elif key_text == "backspace" and current_buffer:
        state.typing_buffer = current_buffer[:-1]

    # 変換・確定トリガーの検知
    if key_text in ("space", "tab", "enter"):
        _flush_typing_buffer(trigger_reason=key_text)

    # --- 既存のキー記録処理 ---
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
                enqueue_key_event("key_combo", key_text, combo_keys, capture_now)
            return

        if key_text in MODIFIER_KEYS and not key_text.startswith("win"): return
        enqueue_key_event("key_press", key_text, capture_now=capture_now)
    except Exception:
        logger.exception("キーフック処理中にエラーが発生しました")

def on_release(key):
    key_text = key_to_string(key)
    with state.pressed_keys_lock:
        state.pressed_keys.discard(key_text)
        if not state.pressed_keys:
            state.logged_combo_keys.clear()