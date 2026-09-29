# Role: OSプロセスのDPIスケーリング（DPI Awareness）およびIME入力モードの状態制御を担当する。

import platform
import ctypes
import time
import logging
import atexit
import struct
import tempfile
import threading
from pathlib import Path
from PIL import Image, ImageDraw

logger = logging.getLogger(__name__)

# =========================
# 設定定数
# =========================

WM_IME_CONTROL = 0x0283
IMC_SETOPENSTATUS = 0x0006
SPI_SETCURSORS = 0x0057
SPIF_UPDATEINIFILE = 0x0001
SPIF_SENDCHANGE = 0x0002

# Why: 待機時(WAIT)・起動時(APPSTARTING)・文字選択(IBEAM)等でのユーザーカーソルチラつきを完全排除
# Why: 通常矢印・リンク手・作業中を最適化置換し、フック遅延ゼロとチラつき解消を両立
RUN_RECORD_CURSOR_IDS = [
    32512,  # OCR_NORMAL
    32649,  # OCR_HAND
    32650,  # OCR_APPSTARTING
]

import queue

_current_cursor_mode: str = "default"
_cur_cache: dict[str, Path] = {}
_cursor_lock = threading.RLock()
_auto_revert_timer: threading.Timer | None = None
_cursor_queue: queue.Queue = queue.Queue()
_cursor_worker_thread: threading.Thread | None = None
_cursor_worker_lock = threading.Lock()


def _generate_cursor_image_and_hotspot(mode: str) -> tuple[Image.Image, tuple[int, int]]:
    # Why: 右下バッジを排除し文字やUI境界を覆い隠さないシャープなイベント別デザインを描画
    img = Image.new("RGBA", (32, 32), (0, 0, 0, 0))
    draw = ImageDraw.Draw(img)

    if mode in ("record_idle", "record"):
        # 通常移動: 赤枠の精密ポインタ矢印 (ホットスポット: 先端 0, 0)
        arrow = [(0, 0), (0, 18), (5, 14), (9, 22), (12, 21), (8, 13), (14, 13)]
        draw.polygon(arrow, fill=(255, 255, 255, 255), outline=(220, 38, 38, 255))
        draw.line([(0, 0), (0, 18)], fill=(185, 28, 28, 255), width=1)
        return img, (0, 0)

    elif mode in ("record_down", "click_down"):
        # クリックDOWN: 押下点を示すターゲットレティクル (ホットスポット: 中心 15, 15)
        cx, cy = 15, 15
        draw.ellipse([(cx - 8, cy - 8), (cx + 8, cy + 8)], outline=(220, 38, 38, 255), width=2)
        draw.line([(cx - 12, cy), (cx - 3, cy)], fill=(220, 38, 38, 255), width=2)
        draw.line([(cx + 3, cy), (cx + 12, cy)], fill=(220, 38, 38, 255), width=2)
        draw.line([(cx, cy - 12), (cx, cy - 3)], fill=(220, 38, 38, 255), width=2)
        draw.line([(cx, cy + 3), (cx, cy + 12)], fill=(220, 38, 38, 255), width=2)
        draw.ellipse([(cx - 1, cy - 1), (cx + 1, cy + 1)], fill=(255, 255, 255, 255))
        return img, (cx, cy)

    elif mode in ("record_drag", "drag"):
        # ドラッグ中: 掴み手（ホールド）アイコン (ホットスポット: 中心 15, 15)
        cx, cy = 15, 15
        draw.rounded_rectangle([(cx - 7, cy - 5), (cx + 7, cy + 8)], radius=3, fill=(245, 158, 11, 240), outline=(20, 20, 20, 255))
        draw.ellipse([(cx - 6, cy - 9), (cx - 2, cy - 4)], fill=(245, 158, 11, 240), outline=(20, 20, 20, 255))
        draw.ellipse([(cx - 2, cy - 10), (cx + 2, cy - 4)], fill=(245, 158, 11, 240), outline=(20, 20, 20, 255))
        draw.ellipse([(cx + 2, cy - 9), (cx + 6, cy - 4)], fill=(245, 158, 11, 240), outline=(20, 20, 20, 255))
        return img, (cx, cy)

    elif mode in ("record_hover", "hover"):
        # 一定時間停止（静止検知）: エメラルドグリーンの精密クロスヘア (ホットスポット: 15, 15)
        cx, cy = 15, 15
        draw.ellipse([(cx - 6, cy - 6), (cx + 6, cy + 6)], outline=(16, 185, 129, 255), width=1)
        draw.line([(cx - 11, cy), (cx - 2, cy)], fill=(16, 185, 129, 255), width=2)
        draw.line([(cx + 2, cy), (cx + 11, cy)], fill=(16, 185, 129, 255), width=2)
        draw.line([(cx, cy - 11), (cx, cy - 2)], fill=(16, 185, 129, 255), width=2)
        draw.line([(cx, cy + 2), (cx, cy + 11)], fill=(16, 185, 129, 255), width=2)
        return img, (cx, cy)

    elif mode in ("run_idle", "run"):
        # 実行中通常: 青枠の自動実行ポインタ矢印 (ホットスポット: 0, 0)
        arrow = [(0, 0), (0, 18), (5, 14), (9, 22), (12, 21), (8, 13), (14, 13)]
        draw.polygon(arrow, fill=(255, 255, 255, 255), outline=(37, 99, 235, 255))
        draw.line([(0, 0), (0, 18)], fill=(29, 78, 216, 255), width=1)
        return img, (0, 0)

    elif mode in ("run_down", "run_click"):
        # 実行クリック時: 青のターゲット照準 (ホットスポット: 15, 15)
        cx, cy = 15, 15
        draw.ellipse([(cx - 8, cy - 8), (cx + 8, cy + 8)], outline=(37, 99, 235, 255), width=2)
        draw.ellipse([(cx - 2, cy - 2), (cx + 2, cy + 2)], fill=(37, 99, 235, 255))
        return img, (cx, cy)

    # デフォルトフォールバック
    arrow = [(0, 0), (0, 17), (4, 13), (8, 20), (11, 19), (7, 12), (13, 12)]
    draw.polygon(arrow, fill=(255, 255, 255, 255), outline=(0, 0, 0, 255))
    return img, (0, 0)


def _get_or_create_cur_file(mode: str) -> Path:
    if mode in _cur_cache and _cur_cache[mode].exists():
        return _cur_cache[mode]

    temp_dir = Path(tempfile.gettempdir()) / "aimacro_cursors"
    temp_dir.mkdir(parents=True, exist_ok=True)
    file_path = temp_dir / f"cursor_{mode}.cur"

    img, (hx, hy) = _generate_cursor_image_and_hotspot(mode)
    width, height = 32, 32

    pixels = img.load()
    xor_data = bytearray()
    and_mask = bytearray()

    for y in reversed(range(height)):
        and_row = 0
        for x in range(width):
            r, g, b, a = pixels[x, y]
            xor_data.extend([b, g, r, a])
            bit = 1 if a < 128 else 0
            and_row = (and_row << 1) | bit
        and_mask.extend(and_row.to_bytes(4, byteorder="big"))

    header = struct.pack("<IIIHHIIIIII", 40, width, height * 2, 1, 32, 0, len(xor_data) + len(and_mask), 0, 0, 0, 0)
    image_data = header + bytes(xor_data) + bytes(and_mask)
    icondir = struct.pack("<HHH", 0, 2, 1)
    direntry = struct.pack("<BBBBHHII", width, height, 0, 0, hx, hy, len(image_data), 22)

    with open(file_path, "wb") as f:
        f.write(icondir + direntry + image_data)

    _cur_cache[mode] = file_path
    return file_path


def set_dpi_awareness():
    """WindowsプロセスのDPI Awarenessを高DPI対応モードに設定する"""
    if platform.system() == "Windows":
        try:
            ctypes.windll.shcore.SetProcessDpiAwareness(2)
        except Exception:
            try:
                ctypes.windll.user32.SetProcessDPIAware()
            except Exception:
                pass


def is_running_as_admin() -> bool:
    """現在のプロセスがWindows管理者権限で実行されているかを判定する"""
    if platform.system() != "Windows":
        return True
    try:
        return ctypes.windll.shell32.IsUserAnAdmin() != 0
    except Exception:
        return False


def set_ime_state(text: str = "", target_state: bool | None = None):
    """アクティブウィンドウのIME状態（全角/半角）を制御する"""
    if platform.system() != "Windows":
        return
    try:
        # Why: 日本語を含む場合はIMEをON、ASCIIのみは強制OFFにして半角英数入力の破壊を防止
        if target_state is None:
            has_japanese = any(ord(c) > 0x7F for c in text)
            open_status = 1 if has_japanese else 0
        else:
            open_status = 1 if target_state else 0

        hwnd = ctypes.windll.user32.GetForegroundWindow()
        if not hwnd:
            return
        default_ime_wnd = ctypes.windll.imm32.ImmGetDefaultIMEWnd(hwnd)
        if default_ime_wnd:
            ctypes.windll.user32.SendMessageW(default_ime_wnd, WM_IME_CONTROL, IMC_SETOPENSTATUS, open_status)
            time.sleep(0.05)
    except Exception as e:
        logger.warning(f"Failed to set IME state: {e}")


def _schedule_auto_revert(target_idle_mode: str, delay: float = 0.25):
    # Why: クリック照準等が後半ずっと残留するバグを防止する安全タイマー
    global _auto_revert_timer
    with _cursor_lock:
        if _auto_revert_timer is not None:
            _auto_revert_timer.cancel()
        def _revert():
            set_system_cursor(target_idle_mode)
        _auto_revert_timer = threading.Timer(delay, _revert)
        _auto_revert_timer.daemon = True
        _auto_revert_timer.start()


def _force_cursor_update():
    # Why: mouse_event発行はフックの再入デッドロックを招くためSetCursorPosのみで安全に再描画
    if platform.system() != "Windows":
        return
    try:
        user32 = ctypes.windll.user32
        class POINT(ctypes.Structure):
            _fields_ = [("x", ctypes.c_long), ("y", ctypes.c_long)]
        pt = POINT()
        if user32.GetCursorPos(ctypes.byref(pt)):
            user32.SetCursorPos(pt.x, pt.y)
    except Exception:
        pass


def restore_system_cursor(force: bool = True):
    """OSシステムカーソルを標準設定へ完全復元する（レジストリ・フォールバック網羅）"""
    global _current_cursor_mode, _auto_revert_timer
    if platform.system() != "Windows":
        return
    # Why: カーソルワーカースレッド滞留時もメインスレッドのデッドロックを防ぐタイムアウト付き排他制御
    acquired = _cursor_lock.acquire(timeout=1.0)
    try:
        while not _cursor_queue.empty():
            try:
                _cursor_queue.get_nowait()
            except Exception:
                break
        if _auto_revert_timer is not None:
            _auto_revert_timer.cancel()
            _auto_revert_timer = None
        if not force and _current_cursor_mode == "default":
            return
        try:
            user32 = ctypes.windll.user32
            # Why: SPIF_SENDCHANGEによる全窓ブロードキャスト同期ブロックをフラグ0で完全排除
            user32.SystemParametersInfoW(SPI_SETCURSORS, 0, None, 0)
            _force_cursor_update()
            _current_cursor_mode = "default"
        except Exception as e:
            logger.warning(f"Failed to restore system cursor: {e}")
    finally:
        if acquired:
            _cursor_lock.release()


def _apply_system_cursor_internal(mode: str):
    global _current_cursor_mode
    with _cursor_lock:
        if mode == _current_cursor_mode:
            return

        if mode == "default":
            restore_system_cursor(force=True)
            return

        try:
            cur_file = _get_or_create_cur_file(mode)
            user32 = ctypes.windll.user32
            for cid in RUN_RECORD_CURSOR_IDS:
                hcur = user32.LoadImageW(None, str(cur_file), 2, 0, 0, 0x0010)
                if hcur:
                    user32.SetSystemCursor(hcur, cid)

            _current_cursor_mode = mode
            _force_cursor_update()

            # Why: 一時的照準デザインが後半ずっと残留する問題を0.2秒タイマーで確実に防止
            if mode in ("run_down", "run_click"):
                _schedule_auto_revert("run_idle", delay=0.2)
            elif mode in ("record_down", "click_down"):
                _schedule_auto_revert("record_idle", delay=0.2)
            elif mode in ("record_hover", "hover"):
                _schedule_auto_revert("record_idle", delay=0.5)
        except Exception as e:
            logger.warning(f"Failed to set system cursor to {mode}: {e}")


def _cursor_update_worker():
    while True:
        try:
            mode = _cursor_queue.get()
            if mode is None:
                break
            # Why: 連続するカーソル更新要求を間引き最新の要求のみを適用
            while not _cursor_queue.empty():
                try:
                    mode = _cursor_queue.get_nowait()
                except queue.Empty:
                    break
            _apply_system_cursor_internal(mode)
        except Exception as e:
            logger.warning(f"Error in cursor update worker: {e}")


def _ensure_cursor_worker():
    global _cursor_worker_thread
    with _cursor_worker_lock:
        if _cursor_worker_thread is None or not _cursor_worker_thread.is_alive():
            _cursor_worker_thread = threading.Thread(target=_cursor_update_worker, name="CursorWorker", daemon=True)
            _cursor_worker_thread.start()


def set_system_cursor(mode: str = "default", async_exec: bool = False):
    """
    Why: 専用ワーカースレッドのキューへ委譲し、フックや実行スレッドのフリーズ・競合を完全防止
    """
    if platform.system() != "Windows":
        return

    _ensure_cursor_worker()
    _cursor_queue.put(mode)


# Why: 起動時に前回の異常終了等で残った青カーソルを即座にOS標準白矢印へ強制リセット
restore_system_cursor(force=True)
atexit.register(lambda: restore_system_cursor(force=True))
