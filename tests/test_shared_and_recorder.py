"""Tests for shared window/IME utilities and recorder log builder."""

import importlib.util
import sys
import types
from datetime import datetime

from core.shared import ime_controller, window_utils


def test_window_utils_extract_and_system_shell_distinction():
    assert window_utils.extract_app_name("Book1 - Excel") == "Excel"
    assert window_utils.extract_app_name("無題 — メモ帳") == "メモ帳"
    assert window_utils.extract_app_name("", default="App") == "App"

    assert window_utils.is_browser_window("Google 検索 - Mozilla Firefox") is True
    assert window_utils.is_system_shell_window("検索") is True
    assert window_utils.is_system_shell_window("Google 検索 - Google Chrome", check_keywords_for_browser=False) is False
    assert window_utils.is_system_shell_window("AI Macro System - Chrome", check_keywords_for_browser=True) is True
    assert window_utils.is_ignored_system_window("AIマクロ生成中...") is True


def test_ime_controller_url_and_width_classification():
    assert ime_controller.is_link_or_url("https://example.com/search?q=1") is True
    assert ime_controller.is_link_or_url("ｈｔｔｐｓ：／／ｅｘａｍｐｌｅ．ｃｏｍ") is True
    assert ime_controller.is_link_or_url("sub.example.co.jp/path") is True
    assert ime_controller.is_link_or_url("日本語テキスト") is False

    assert ime_controller.normalize_text_width("ｈｔｔｐｓ：／／ｅｘａｍｐｌｅ．ｃｏｍ") == "https://example.com"
    assert ime_controller.should_input_as_halfwidth("abc123") is True
    assert ime_controller.should_input_as_fullwidth("名古屋") is True

    from core.recorder import utils as rec_utils

    class _DummyCtrlKey:
        def __init__(self, char: str, vk: int):
            self.char = char
            self.vk = vk

    assert rec_utils.normalize_key_name(_DummyCtrlKey("\x01", 65)) == "a"
    assert rec_utils.normalize_key_name(_DummyCtrlKey("\x03", 67)) == "c"
    assert rec_utils.normalize_key_name(_DummyCtrlKey("\x16", 86)) == "v"
    assert rec_utils.make_combo_text(["ctrl_l", "v"]) == "ctrl+v"
    assert rec_utils.should_record_key_combo({"ctrl_l"}, "v") is True


def test_log_builder_scroll_and_save(tmp_path, monkeypatch):
    if importlib.util.find_spec("etw") is None and "etw" not in sys.modules:
        monkeypatch.setitem(sys.modules, "etw", types.ModuleType("etw"))

    from core.recorder import log_builder

    monkeypatch.setattr(
        log_builder.process_monitor,
        "get_window_title_at_point",
        lambda x, y: {"title": "Page - Google Chrome", "rect": {"x": 0, "y": 0, "width": 1200, "height": 800}},
    )
    monkeypatch.setattr(log_builder, "get_wheel_scroll_settings", lambda: {"scroll_lines": 5, "is_page_scroll": False})

    scroll_log = log_builder.build_scroll_log(
        event_no="002",
        dt=datetime(2026, 1, 1, 12, 0, 0),
        x=300,
        y=400,
        dx=0.0,
        dy=-3.0,
    )
    assert scroll_log["Type"] == "mouse_scroll"
    assert scroll_log["Content"]["direction"] == "down"
    assert scroll_log["Content"]["scroll_lines"] == 5

    assert log_builder.event_no_sort_key({"EventNo": "001"}) < log_builder.event_no_sort_key({"EventNo": "002"})
    assert log_builder.event_no_sort_key({"EventNo": "002"}) < log_builder.event_no_sort_key({"EventNo": "End"})

    temp_dir = tmp_path / "temp"
    temp_dir.mkdir(parents=True)
    monkeypatch.setattr(log_builder.macro_path_manager, "get_temp_dir", lambda: temp_dir)
    monkeypatch.setattr(log_builder.state, "recording_dirs", {"macro_name": "wf_99"})
    monkeypatch.setattr(
        log_builder.state,
        "input_logs",
        [{"EventNo": "End", "Type": "recording_end"}, scroll_log],
    )
    log_builder.save_input_logs()

    saved = (temp_dir / "input_logs.json").read_text(encoding="utf-8")
    assert '"MacroName": "wf_99"' in saved
    assert saved.index('"EventNo": "002"') < saved.index('"EventNo": "End"')
