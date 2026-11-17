"""Tests for log filtering, typing aggregation, loop detection, quality gate, and macro building."""

from models.data_types import Coordinates, IntegratedEvent, Size, WindowContext
from datetime import datetime
from decimal import Decimal

from core.generator.executable_macro_builder import build_executable_commands
from core.generator.form_optimizer import consolidate_web_form_interactions
from core.generator.log_integrator import filter_meaningful_raw_logs
from core.generator.loop_detector import detect_and_optimize_loops
from core.generator.office_optimizer import clean_office_events, filter_web_excel_conflicts, propagate_excel_cells
from core.generator.quality_gate import QualityGateEvaluator
from core.generator.shell_layout_cleaner import normalize_window_layout, trim_boundary_noise
from core.generator.typing_aggregator import TypingSessionAggregator
from core.generator.workflow_builder import build_workflow_model
from core.recorder.office_monitor import _normalize_com_cell_value, _normalize_com_structured_value
from core.shared.app_log_profile import (
    AppProfile,
    LogChannel,
    detect_app_profile,
    detect_office_sub_app,
    is_channel_allowed_for_profile,
    resolve_log_channels,
)


def test_filter_meaningful_raw_logs_handles_null_content_and_filters_noise():
    raw_logs = [
        {"EventNo": "001", "Type": "recording_start", "WindowName": "App", "Content": None, "Images": None},
        {"EventNo": "002", "Type": "key_down", "WindowName": "App", "Content": {"key": "win"}},
        {"EventNo": "003", "Type": "mouse_move", "WindowName": "App", "Content": {"screen_coordinates": {"x": 100, "y": 100}}},
        {"EventNo": "004", "Type": "mouse_click", "WindowName": "App", "Content": {"screen_coordinates": {"x": 110, "y": 105}}},
        {"EventNo": "005", "Type": "mouse_scroll", "WindowName": "", "Content": {"dx": 0, "dy": -3}},
    ]
    filtered = filter_meaningful_raw_logs(raw_logs)
    event_nos = [e["EventNo"] for e in filtered]
    assert event_nos == ["004", "005"]
    assert filtered[1]["WindowName"] == "App"


def test_typing_aggregator_merge_and_trailing_enter_without_index_or_key_error():
    agg = TypingSessionAggregator()
    events = [
        # Session 1 flushes as a single type_text event without fallback_events key
        {
            "event_id": "evt_001",
            "timestamp": 1000,
            "raw_action": "type_text",
            "semantic_role": "hello",
            "window_name": "Browser - Chrome",
            "diff_val": 1.0,
            "app_context": {"element_name": "Search", "css_selector": "#q"},
        },
        # Time gap > 3000ms forces Session 1 flush before Session 2 starts on the same element
        {
            "event_id": "evt_002",
            "timestamp": 4500,
            "raw_action": "key_down",
            "semantic_role": "w",
            "window_name": "Browser - Chrome",
            "diff_val": 1.0,
            "app_context": {"element_name": "Search", "css_selector": "#q"},
        },
        {
            "event_id": "evt_003",
            "timestamp": 4600,
            "raw_action": "key_down",
            "semantic_role": "orld",
            "window_name": "Browser - Chrome",
            "diff_val": 1.0,
            "app_context": {"element_name": "Search", "css_selector": "#q"},
        },
        {
            "event_id": "evt_004",
            "timestamp": 4700,
            "raw_action": "key_down",
            "semantic_role": "enter",
            "window_name": "Browser - Chrome",
            "ime_active": True,
        },
        {
            "event_id": "evt_005",
            "timestamp": 5200,
            "raw_action": "click",
            "semantic_role": "Result",
            "window_name": "Results - Chrome",
        },
    ]
    out = agg.aggregate_events(events)
    assert out[0]["raw_action"] == "type_text"
    assert out[0]["semantic_role"] == "helloworld"
    assert "evt_002" in out[0]["fallback_events"]
    assert any(e.get("semantic_role") == "enter" for e in out)


def test_loop_detector_extracts_offset_and_arithmetic_sequence():
    events = [
        {"raw_type": "meta_loop_start", "raw_action": "meta", "event_id": "evt_001", "window_name": "App"},
        {"raw_type": "mouse_click", "raw_action": "click", "event_id": "evt_002", "cursor_x": 100, "cursor_y": 200},
        {"raw_type": "key_down", "raw_action": "type_text", "event_id": "evt_003", "semantic_role": "10"},
        {"raw_type": "mouse_click", "raw_action": "click", "event_id": "evt_004", "cursor_x": 100, "cursor_y": 240},
        {"raw_type": "key_down", "raw_action": "type_text", "event_id": "evt_005", "semantic_role": "15"},
        {"raw_type": "meta_loop_end", "raw_action": "meta", "event_id": "evt_006", "window_name": "App"},
    ]
    optimized = detect_and_optimize_loops(events, "wf_1")
    loop_start = optimized[0]
    assert loop_start["loop_variables"] == {"y_offset": 40, "x_offset": 0}
    assert optimized[2]["sequence_value"] == {"start": 10, "step": 5}
    assert len(optimized) == 4


def test_quality_gate_static_rules_handles_none_coords_and_penalizes_issues():
    evaluator = QualityGateEvaluator()
    score, issues = evaluator._evaluate_static_rules([
        {"raw_action": "click", "cursor_x": None, "cursor_y": None},
        {"raw_action": "type_text", "text": ""},
        {"raw_action": "browser_action", "action": "click_element", "selector": "#submit"},
        {"raw_action": "browser_action", "action": "type_text", "selector": ""},
    ])
    assert score < 60.0
    assert any("Empty typing" in iss for iss in issues)
    assert any("Missing selector" in iss for iss in issues)
    assert any("after submit" in iss for iss in issues)


def test_shell_cleaner_workflow_builder_and_executable_builder():
    cleaned = normalize_window_layout(
        [
            {"event_id": "evt_000", "raw_action": "click", "cursor_x": None, "cursor_y": None, "win_x": 0, "win_y": 0, "win_w": None, "win_h": None, "window_name": "App"},
            {"event_id": "evt_001", "raw_action": "move", "diff_val": 0.0, "app_context": None, "cursor_x": None, "cursor_y": None, "window_name": "App"},
            {"event_id": "evt_002", "raw_action": "click", "cursor_x": 120, "cursor_y": 150, "win_x": 0, "win_y": 0, "win_w": 800, "win_h": 600, "window_name": "App"},
        ],
        "wf_1",
    )
    trimmed = trim_boundary_noise(cleaned)
    assert len(trimmed) == 3

    integ_events = [
        IntegratedEvent(
            id="evt_001",
            timestamp=1000,
            window=WindowContext(name="App", size=Size(width=800, height=600), coordinates=Coordinates(x=0, y=0), UIs=[]),
        ),
        IntegratedEvent(
            id="evt_002",
            timestamp=1500,
            window=WindowContext(name="App", size=Size(width=800, height=600), coordinates=Coordinates(x=0, y=0), UIs=[]),
        ),
        IntegratedEvent(
            id="evt_003",
            timestamp=2000,
            window=WindowContext(name="App", size=Size(width=800, height=600), coordinates=Coordinates(x=0, y=0), UIs=[]),
        ),
    ]
    temp_info = [
        {"event_id": "evt_001", "raw_action": "drag", "raw_type": "mouse_drag", "drag_start_x": 10, "drag_start_y": 10, "drag_end_x": 10, "drag_end_y": 10, "window_name": "App"},
        {"event_id": "evt_002", "raw_action": "click", "raw_type": "mouse_click", "button": None, "fallback_events": None, "cursor_x": 120, "cursor_y": 150, "semantic_role": "Save", "window_name": "App"},
        {"event_id": "evt_003", "raw_action": "scroll", "raw_type": "mouse_scroll", "dx": None, "dy": -3.0, "cursor_x": None, "cursor_y": None, "window_name": "App"},
    ]
    wf, targets = build_workflow_model(temp_info, integ_events, "wf_1")
    # step 1 is ACTIVATE_WINDOW, step 2 is MOUSE_CLICK, step 3 is MOUSE_SCROLL (zero-distance drag skipped cleanly)
    assert [s.action.command for s in wf.steps] == ["ACTIVATE_WINDOW", "MOUSE_CLICK", "MOUSE_SCROLL"]
    assert "tgt_2" in targets and "tgt_1" not in targets

    cmds = build_executable_commands(wf.steps, temp_info, integ_events)
    assert [c["method"] for c in cmds] == ["activate_window", "wait", "click", "wait", "scroll"]
    assert cmds[1]["args"]["duration"] == 0.5
    assert cmds[3]["args"]["duration"] == 0.5


def test_app_profile_log_switching_between_office_and_browser():
    # Browser viewing an Excel-related page must still be classified as BROWSER
    assert detect_app_profile({"WindowName": "Excel Tutorial - Google Chrome", "ProcessName": "chrome.exe"}) == AppProfile.BROWSER
    assert detect_app_profile({"WindowName": "売上管理.xlsx - Excel", "ProcessName": "EXCEL.EXE"}) == AppProfile.OFFICE

    # Non-browser / Non-Office apps with "edge", "opera", "chrome", "excel", "word" in filename must be DESKTOP
    assert detect_app_profile({"WindowName": "excel_handler.py - Visual Studio Code", "ProcessName": "Code.exe"}) == AppProfile.DESKTOP
    assert detect_app_profile({"WindowName": "chrome_driver.py - Visual Studio Code", "ProcessName": "Code.exe"}) == AppProfile.DESKTOP
    assert detect_app_profile({"WindowName": "knowledge_base.txt - メモ帳"}) == AppProfile.DESKTOP
    assert detect_app_profile({"WindowName": "operation_manual.txt - メモ帳"}) == AppProfile.DESKTOP
    assert detect_app_profile({
        "WindowName": "Book1 - Excel",
        "WindowCommandLine": '"C:\\Program Files\\Microsoft Office\\root\\Office16\\EXCEL.EXE" "C:\\temp\\chrome.exe.xlsx"',
    }) == AppProfile.OFFICE

    # Word (non-Excel Office) must allow UIA_SCAN while Excel blocks UIA_SCAN
    word_src = {"WindowName": "仕様書.docx - Word", "ProcessName": "WINWORD.EXE"}
    excel_src = {"WindowName": "Book1 - Excel", "ProcessName": "EXCEL.EXE"}
    assert detect_office_sub_app(word_src) == "Word"
    assert is_channel_allowed_for_profile(LogChannel.UIA_SCAN, AppProfile.OFFICE, source=word_src) is True
    assert is_channel_allowed_for_profile(LogChannel.UIA_SCAN, AppProfile.OFFICE, source=excel_src) is False

    # COM value normalization for datetime, Decimal, and 2D range matrix
    assert _normalize_com_cell_value(datetime(2026, 4, 1, 9, 30)) == "2026-04-01T09:30:00"
    assert _normalize_com_cell_value(Decimal("1250.50")) == "1250.50"
    assert _normalize_com_cell_value(((100.0, None), (200.0, None))) == "100, 200"
    assert _normalize_com_structured_value(((100.0, None), (200.0, None)), is_range=True) == [["100", ""], ["200", ""]]
    assert _normalize_com_structured_value(((None, None), (None, None)), is_range=True) is None

    # Multi-channel resolution per active app profile
    primary_br, channels_br = resolve_log_channels(
        "mouse_click",
        AppProfile.BROWSER,
        {"css_selector": "#submit", "element_name": "送信"},
        has_crop_image=True,
    )
    assert primary_br == LogChannel.OS_MOUSE
    assert LogChannel.BROWSER_DOM.value in channels_br
    assert LogChannel.OS_MOUSE.value in channels_br

    raw_logs = [
        {
            "EventNo": "001",
            "Type": "uia_scan",
            "WindowName": "Book1 - Excel",
            "ProcessName": "EXCEL.EXE",
            "Content": {"uia_info": {"name": "A1"}},
        },
        {
            "EventNo": "002",
            "Type": "office_event",
            "WindowName": "Book1 - Excel",
            "ProcessName": "EXCEL.EXE",
            "Content": {
                "office_info": {
                    "type": "excel",
                    "event_type": "cell_change",
                    "sheet": "Sheet1",
                    "cell": "A1",
                    "value": "テスト値",
                    "message": "✏️ [入力確定] シート: 'Sheet1' | セル: $A$1 | 値: テスト値",
                }
            },
            "AppSpecificContext": {"css_selector": "#leaked_browser_sel", "element_name": "A1"},
        },
        {
            "EventNo": "003",
            "Type": "mouse_click",
            "WindowName": "ポータル - Google Chrome",
            "ProcessName": "chrome.exe",
            "Content": {"screen_coordinates": {"x": 300, "y": 250}},
            "AppSpecificContext": {"css_selector": "#search", "element_name": "検索"},
        },
        {
            "EventNo": "004",
            "Type": "office_event",
            "WindowName": "",
            "ProcessName": "",
            "Content": {
                "office_info": {
                    "type": "excel",
                    "event_type": "selection_change",
                    "sheet": "Sheet1",
                    "cell": "B2",
                    "message": "🖱️ [選択移動] シート: 'Sheet1' | セル: $B$2",
                }
            },
        },
    ]

    filtered = filter_meaningful_raw_logs(raw_logs)
    assert [e["EventNo"] for e in filtered] == ["002", "003"]
    assert filtered[0]["AppProfile"] == "office"
    assert filtered[0]["LogChannel"] == "office_com"
    assert "css_selector" not in (filtered[0].get("AppSpecificContext") or {})
    assert filtered[1]["AppProfile"] == "browser"
    assert "browser_dom" in filtered[1]["ActiveChannels"]

    # Interleaved Browser -> Excel -> Browser form must not duplicate inputs or auto-submit mid-way
    interleaved_form = [
        {
            "event_id": "evt_1",
            "raw_action": "type_text",
            "semantic_role": "山田",
            "window_name": "登録フォーム - Google Chrome",
            "app_profile": "browser",
            "cursor_x": 200,
            "cursor_y": 150,
            "app_context": {
                "css_selector": "#name",
                "element_name": "氏名",
                "control_type": "Edit",
                "form_snapshot": {
                    "#name": "山田",
                    "__elements__": [
                        {"selector": "#name", "element_name": "氏名", "control_type": "Edit", "x": 200, "y": 150, "value": "山田"},
                        {"selector": "#submit-btn", "element_name": "送信", "control_type": "Button", "x": 200, "y": 400, "value": ""},
                    ],
                },
            },
        },
        {
            "event_id": "evt_2",
            "raw_action": "excel_action",
            "action": "read_cell",
            "cell": "A1",
            "window_name": "Book1 - Excel",
            "app_profile": "office",
        },
        {
            "event_id": "evt_3",
            "raw_action": "type_text",
            "semantic_role": "yamada@example.com",
            "window_name": "登録フォーム - Google Chrome",
            "app_profile": "browser",
            "cursor_x": 200,
            "cursor_y": 220,
            "app_context": {
                "css_selector": "#email",
                "element_name": "メール",
                "control_type": "Edit",
                "form_snapshot": {"#name": "山田", "#email": "yamada@example.com"},
            },
        },
    ]
    consolidated = consolidate_web_form_interactions(interleaved_form)
    assert [c.get("selector") or c.get("action") for c in consolidated] == ["#name", "read_cell", "#email", "#submit-btn"]

    # Excel cell propagation & range support
    mixed_events = [
        {
            "event_id": "evt_002",
            "raw_action": "office_event",
            "window_name": "",
            "app_profile": "office",
            "office_info": {"event_type": "cell_change", "sheet": "Sheet1", "cell": "A1", "value": "100"},
        },
        {
            "event_id": "evt_003",
            "raw_action": "type_text",
            "semantic_role": "別ブック入力",
            "window_name": "Book2 - Excel",
            "app_profile": "office",
        },
        {
            "event_id": "evt_004",
            "raw_action": "office_event",
            "window_name": "Book2 - Excel",
            "app_profile": "office",
            "office_info": {"event_type": "cell_change", "sheet": "Sheet1", "cell": "A1:B2", "is_range": True, "value": None},
        },
        {
            "event_id": "evt_005",
            "raw_action": "type_text",
            "semantic_role": "ブラウザ検索語",
            "window_name": "ポータル - Google Chrome",
            "app_profile": "browser",
            "excel_cell": "LEAKED",
        },
    ]
    cleaned_office = filter_web_excel_conflicts(propagate_excel_cells(clean_office_events(mixed_events)))
    assert cleaned_office[0]["action"] == "write_cell"
    assert cleaned_office[0]["cell"] == "A1"
    assert "excel_cell" not in cleaned_office[1]
    assert cleaned_office[2]["action"] == "clear_range"
    assert cleaned_office[2]["range_address"] == "A1:B2"
    assert "excel_cell" not in cleaned_office[3]
