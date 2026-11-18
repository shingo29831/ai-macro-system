# @role: pynputを利用したOSレベルのマウス・キーボード入力の監視と、イベントキューへの非同期プッシュを行う。

import time
import math
import threading
import logging
from core.recorder.state import state
from core.recorder.utils import key_to_string, sorted_combo_keys, make_combo_text, should_record_key_combo, MODIFIER_KEYS
from core.recorder.event_processor import enqueue_key_event, flush_typing_buffer
from core.recorder.ime_detector import is_ime_active
from core.shared.romaji import to_hiragana

logger = logging.getLogger(__name__)

MIN_DISTANCE_FOR_VECTOR = 40
CORNER_ANGLE_THRESHOLD = 20

_hover_timer = None
_hover_timer_lock = threading.Lock()

def _cancel_hover_timer():
    global _hover_timer
    with _hover_timer_lock:
        if _hover_timer is not None:
            _hover_timer.cancel()
            _hover_timer = None

def _on_hover_timeout(hx: int, hy: int):
    if not state.is_recording or state.is_stopping:
        return
    # Why: マウス静止によるドロップダウンメニュー等の展開をホバーとして記録
    state.mouse_event_queue.put({"type": "hover", "x": hx, "y": hy})
    logger.debug("Hover event emitted at (%d, %d)", hx, hy)

def _trigger_field_search(trigger_reason: str):
    buffer = getattr(state, "typing_buffer", "")
    if not buffer:
        return

    ime_on = is_ime_active()
    target_text = to_hiragana(buffer) if ime_on else buffer
    
    # 背景: IME状態をイベントプロセッサに伝達する
    enqueue_key_event("text_field_search", target_text, capture_now=True, ime_active=ime_on)
    logger.debug("テキストフィールド探索イベントを発火しました [%s]: %s (IME: %s)", trigger_reason, target_text, ime_on)


def on_move(x, y):
    if not state.is_recording or state.is_stopping: return
    current_time = time.time()
    
    global _hover_timer
    with _hover_timer_lock:
        if _hover_timer is not None:
            _hover_timer.cancel()
        # Why: ドロップダウン等の素早いホバー通過(120ms)を逃さず確実に捕捉
        _hover_timer = threading.Timer(0.12, _on_hover_timeout, args=(int(x), int(y)))
        _hover_timer.daemon = True
        _hover_timer.start()

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
    _cancel_hover_timer()
    state.cancel_hover()
    if not state.is_recording or state.is_stopping: return

    # Why: フックコールバック内の同期処理をゼロにし、マウスアップ消失による照準固定を根絶
    if pressed:
        flush_typing_buffer("mouse_clicked")

    state.mouse_event_queue.put({"type": "click", "x": x, "y": y, "button": button, "pressed": pressed})

def on_scroll(x, y, dx, dy):
    _cancel_hover_timer()
    state.cancel_hover()
    if state.is_stopping: return
    # Why: 低レベルフックのサイレントアンフックによる欠落を防ぎ常時キュー投入(重複はハンドラ側で除外)
    try: 
        import time
        state.mouse_event_queue.put({
            "type": "scroll",
            "x": int(x),
            "y": int(y),
            "dx": float(dx),
            "dy": float(dy),
            "source": "pynput",
            "timestamp": time.time()
        })
    except Exception: 
        logger.exception("スクロールイベントの記録に失敗しました")

def on_press(key):
    _cancel_hover_timer()
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
                
                stop_cb = state.shortcut_stop_callback or getattr(state, "default_stop_callback", None)
                if stop_cb:
                    threading.Thread(target=stop_cb, daemon=True).start()
            return False 

    ime_on = is_ime_active()
    # Why: 同期キャプチャ遅延でEnter押下後の遷移先ウィンドウへ誤帰属するのを防ぐため押下瞬間のウィンドウを固定
    from core.recorder import window_inspector
    pressed_window_info = window_inspector.get_foreground_window_info()

    try:
        with state.pressed_keys_lock:
            # Why: フォーカス遷移等で消失した古い機能キーの残骸を除去し誤コンボを防止
            if key_text not in MODIFIER_KEYS:
                for stale_k in ["tab", "enter", "space", "esc"]:
                    state.pressed_keys.discard(stale_k)
            state.pressed_keys.add(key_text)
            current_keys = set(state.pressed_keys)

        is_combo = should_record_key_combo(current_keys, key_text)
        if is_combo:
            flush_typing_buffer("shortcut_pressed", window_info=pressed_window_info)
            combo_keys = sorted_combo_keys(current_keys)
            combo_text = make_combo_text(combo_keys)
            if combo_text not in state.logged_combo_keys:
                state.logged_combo_keys.add(combo_text)
                enqueue_key_event(
                    "key_combo",
                    key_text,
                    combo_keys,
                    capture_now=True,
                    ime_active=ime_on,
                    window_info=pressed_window_info,
                )
            return

        if key_text in MODIFIER_KEYS and not key_text.startswith("win"):
            return

        is_text_input = (len(key_text) == 1 and key_text.isprintable()) or key_text in (
            "backspace",
            "enter",
            "tab",
            "space",
        )
        capture_now = is_text_input

        if is_text_input:
            if key_text in ("enter", "tab"):
                flush_typing_buffer(f"{key_text}_pressed", window_info=pressed_window_info)
            else:
                if not getattr(state, "typing_buffer", ""):
                    _trigger_field_search("typing_started")

                if len(key_text) == 1 and key_text.isprintable():
                    state.typing_buffer = getattr(state, "typing_buffer", "") + key_text
                elif key_text == "space":
                    state.typing_buffer = getattr(state, "typing_buffer", "") + " "
                elif key_text == "backspace":
                    state.typing_buffer = getattr(state, "typing_buffer", "")[:-1]

        enqueue_key_event(
            "key_press",
            key_text,
            capture_now=capture_now,
            ime_active=ime_on,
            window_info=pressed_window_info,
        )
    except Exception:
        logger.exception("キーフック処理中にエラーが発生しました")

def on_release(key):
    key_text = key_to_string(key)
    with state.pressed_keys_lock:
        state.pressed_keys.discard(key_text)
        # Why: Ctrl押しっぱなしでの連続Ctrl+V等で2回目以降が抑制されないよう非修飾キー解放時にリセット
        if not state.pressed_keys or key_text not in MODIFIER_KEYS:
            state.logged_combo_keys.clear()
