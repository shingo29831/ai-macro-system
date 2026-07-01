# @role: マクロ記録プロセス全体で共有される状態（フラグ、キュー、スレッドロック）を一元管理する。

import threading
import queue

class RecorderState:
    def __init__(self):
        self.is_recording = False
        self.is_stopping = False
        self.is_click_processing = False
        
        self.input_logs = []
        self.input_logs_lock = threading.Lock()
        
        self.recording_dirs = None
        
        self.event_index = 0
        self.event_index_lock = threading.Lock()
        
        self.previous_screenshot_img = None
        self.previous_screenshot_lock = threading.Lock()
        
        self.pressed_keys = set()
        self.pressed_keys_lock = threading.Lock()
        self.logged_combo_keys = set()
        
        self.latest_mouse_down_event = None
        self.latest_mouse_down_lock = threading.Lock()
        
        self.pending_click_event = None
        self.pending_click_timer = None
        self.pending_click_lock = threading.Lock()
        
        self.key_event_queue = queue.Queue()
        self.key_worker_thread = None
        self.key_worker_stop_event = threading.Event()
        
        self.mouse_event_queue = queue.Queue()
        self.mouse_worker_thread = None
        self.mouse_worker_stop_event = threading.Event()
        
        self.native_scroll_hook_thread = None
        self.native_scroll_hook_thread_id = None
        self.native_scroll_hook_handle = None
        self.native_scroll_hook_callback = None
        self.native_scroll_hook_ready = threading.Event()
        self.native_scroll_hook_active = False
        
        self.mouse_path = []
        self.shortcut_stop_callback = None

    def get_next_event_no(self) -> str:
        with self.event_index_lock:
            self.event_index += 1
            return f"{self.event_index:03d}"

    def append_log(self, log: dict):
        # 背景: 変数化処理(log_integrator)を阻害しないため、OCR用の内部イベントは実ログには残さない
        if log.get("Type") in ("text_field_search", "text_candidate_confirm"):
            return
            
        with self.input_logs_lock:
            self.input_logs.append(log)

    def cancel_hover(self):
        self.mouse_path.clear()

# アプリケーション全体で共有するシングルトンインスタンス
state = RecorderState()