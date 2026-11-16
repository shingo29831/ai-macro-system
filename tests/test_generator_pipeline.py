"""Tests for log filtering, typing aggregation, loop detection, quality gate, and macro building."""

from models.data_types import Coordinates, IntegratedEvent, Size, WindowContext
from core.generator.executable_macro_builder import build_executable_commands
from core.generator.log_integrator import filter_meaningful_raw_logs
from core.generator.loop_detector import detect_and_optimize_loops
from core.generator.quality_gate import QualityGateEvaluator
from core.generator.shell_layout_cleaner import normalize_window_layout, trim_boundary_noise
from core.generator.typing_aggregator import TypingSessionAggregator
from core.generator.workflow_builder import build_workflow_model


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
