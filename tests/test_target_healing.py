"""Module: @role: Regression tests for UI target verification and multi-stage self-recovery."""

import json
from types import SimpleNamespace

from PIL import Image, ImageDraw

from core.executor import step_verifier, target_healer
from core.healer import recovery_manager


def test_edge_click_inside_wide_template_does_not_trigger_false_drift(tmp_path, monkeypatch):
    images_dir = tmp_path / "images"
    images_dir.mkdir(parents=True)

    screen = Image.new("RGB", (400, 300), (240, 240, 240))
    draw = ImageDraw.Draw(screen)
    draw.rectangle((100, 100, 300, 140), fill=(30, 90, 200), outline=(10, 40, 120))
    draw.text((120, 112), "Search Input", fill=(255, 255, 255))

    crop = screen.crop((100, 100, 300, 140))
    crop.save(images_dir / "evt_0001_crop.png")

    monkeypatch.setattr(
        "core.shared.screen_capture.take_screenshot",
        lambda: (screen, {"left": 0, "top": 0, "width": 400, "height": 300}),
    )

    called = {"recovered": False}
    monkeypatch.setattr(
        "core.healer.recovery_manager.attempt_recovery",
        lambda *a, **kw: called.__setitem__("recovered", True) or {"success": False},
    )

    # Click near left edge of the 200px wide element (>25px away from center 200, but inside matched box)
    args = {"x": 115, "y": 120}
    ok, info = target_healer.verify_and_heal_target(
        workflow_id="wf_test",
        target_id="tgt_1",
        raw_event_id="evt_0001_nav_click",
        args=args,
        target_dir=tmp_path,
        last_win_args=None,
        status_callback=None,
        check_stop_callback=lambda: None,
    )

    assert ok is True
    assert info is None
    assert called["recovered"] is False


def test_none_coordinates_with_matched_template_uses_fallback_without_type_error(tmp_path, monkeypatch):
    images_dir = tmp_path / "images"
    images_dir.mkdir(parents=True)

    screen = Image.new("RGB", (300, 240), (240, 240, 240))
    draw = ImageDraw.Draw(screen)
    draw.rectangle((120, 80, 200, 120), fill=(40, 120, 210), outline=(255, 255, 255))
    screen.crop((120, 80, 200, 120)).save(images_dir / "evt_0010_crop.png")

    monkeypatch.setattr(
        "core.shared.screen_capture.take_screenshot",
        lambda: (screen, {"left": 0, "top": 0, "width": 300, "height": 240}),
    )
    monkeypatch.setattr(
        "core.healer.recovery_manager.attempt_recovery",
        lambda *a, **kw: {"success": False, "reason": "element_not_found"},
    )

    args = {"x": None, "y": None}
    ok, info = target_healer.verify_and_heal_target(
        workflow_id="wf_test",
        target_id="tgt_10",
        raw_event_id="evt_0010",
        args=args,
        target_dir=tmp_path,
        last_win_args=None,
        status_callback=None,
        check_stop_callback=lambda: None,
    )

    assert ok is True
    assert info is not None and info["method"] == "template_drift_fallback"
    assert 150 <= args["x"] <= 170 and 90 <= args["y"] <= 110


def test_missing_crop_without_semantic_locator_proceeds_safely(tmp_path, monkeypatch):
    (tmp_path / "images").mkdir(parents=True)
    (tmp_path / "ui_targets.json").write_text(
        json.dumps({"tgt_1": {"semantic_role": "left_click", "ui_type": "unknown"}}),
        encoding="utf-8",
    )

    monkeypatch.setattr(
        "core.healer.recovery_manager.attempt_recovery",
        lambda *a, **kw: (_ for _ in ()).throw(AssertionError("should not invoke recovery without locator")),
    )

    args = {"x": 320, "y": 240}
    ok, info = target_healer.verify_and_heal_target(
        workflow_id="wf_test",
        target_id="tgt_1",
        raw_event_id="evt_0002",
        args=args,
        target_dir=tmp_path,
        last_win_args=None,
        status_callback=None,
        check_stop_callback=lambda: None,
    )

    assert ok is True
    assert info is None


def test_recovery_manager_recovers_tinted_template_and_skips_browser_uia_for_desktop_app(tmp_path, monkeypatch):
    wf_dir = tmp_path / "wf_heal"
    (wf_dir / "images").mkdir(parents=True)
    (wf_dir / "ui_targets.json").write_text(
        json.dumps({"tgt_5": {"semantic_role": "left_click", "ui_type": "unknown"}}),
        encoding="utf-8",
    )

    tpl = Image.new("RGB", (80, 32), (20, 100, 220))
    draw_tpl = ImageDraw.Draw(tpl)
    draw_tpl.rectangle((4, 4, 75, 27), outline=(255, 255, 255), width=2)
    tpl.save(wf_dir / "images" / "evt_0005_crop.png")

    screen = Image.new("RGB", (400, 300), (245, 245, 245))
    draw_scr = ImageDraw.Draw(screen)
    draw_scr.rectangle((220, 150, 300, 182), fill=(25, 105, 225))
    draw_scr.rectangle((224, 154, 295, 177), outline=(255, 255, 255), width=2)

    monkeypatch.setattr(recovery_manager, "get_macros_root", lambda: tmp_path)
    monkeypatch.setattr(
        recovery_manager,
        "take_screenshot",
        lambda: (screen, {"left": 0, "top": 0, "width": 400, "height": 300}),
    )
    monkeypatch.setattr(
        "core.executor.browser_uia_searcher.find_uia_element",
        lambda *a, **kw: (_ for _ in ()).throw(AssertionError("must not run browser UIA on desktop app")),
    )

    res = recovery_manager.attempt_recovery(
        workflow_id="wf_heal",
        target_id="tgt_5",
        window_hwnd=0,
        raw_event_id="evt_0005_nav_click",
        target_context={"element_name": "保存"},
        last_win_args={"window_title": "Book1 - Excel"},
    )

    assert res["success"] is True
    assert res["stage"] == 2
    assert 245 <= res["new_coordinates"]["x"] <= 275
    assert 155 <= res["new_coordinates"]["y"] <= 177


def test_recovery_manager_stage3_uses_target_context_element_name(tmp_path, monkeypatch):
    wf_dir = tmp_path / "wf_ocr"
    (wf_dir / "images").mkdir(parents=True)
    (wf_dir / "ui_targets.json").write_text(
        json.dumps({"tgt_9": {"semantic_role": "left_click", "ui_type": "unknown"}}),
        encoding="utf-8",
    )

    screen = Image.new("RGB", (300, 200), (255, 255, 255))
    monkeypatch.setattr(recovery_manager, "get_macros_root", lambda: tmp_path)
    monkeypatch.setattr(
        recovery_manager,
        "take_screenshot",
        lambda: (screen, {"left": 100, "top": 50, "width": 300, "height": 200}),
    )
    monkeypatch.setattr(
        recovery_manager,
        "read_text_from_image",
        lambda _p: [
            SimpleNamespace(
                content="送信する",
                confidence=0.95,
                boundingBox=SimpleNamespace(x=40, y=60, width=80, height=30),
            )
        ],
    )

    res = recovery_manager.attempt_recovery(
        workflow_id="wf_ocr",
        target_id="tgt_9",
        window_hwnd=0,
        raw_event_id="evt_0009",
        target_context={"element_name": "送信する"},
    )

    assert res["success"] is True
    assert res["stage"] == 3
    assert res["new_coordinates"] == {"x": 180, "y": 125}


def test_heal_target_or_skip_bypasses_without_persisting_scroll_coords(tmp_path, monkeypatch):
    monkeypatch.setattr(
        step_verifier,
        "verify_and_heal_target",
        lambda **_kw: (_ for _ in ()).throw(AssertionError("should not run image healer")),
    )

    next_i, aborted, healed = step_verifier.heal_target_or_skip(
        step_index=2,
        commands=[],
        args={"x": 10, "y": 20},
        target_id="tgt_3",
        raw_event_id="evt_0003",
        step_log={},
        loop_stack=[],
        current_win_rect=(0, 0, 800, 600),
        last_win_args=None,
        target_dir=tmp_path,
        workflow_id="wf_test",
        status_callback=None,
        check_stop_callback=lambda: None,
        update_ui_callback=lambda _m, _w=False: None,
        browser_element_located=True,
    )
    assert (next_i, aborted, healed) == (3, False, False)

    next_i2, aborted2, healed2 = step_verifier.heal_target_or_skip(
        step_index=4,
        commands=[],
        args={"x": 10, "y": 20, "excel_dest_cell": "B2"},
        target_id="tgt_5",
        raw_event_id="evt_0005",
        step_log={},
        loop_stack=[],
        current_win_rect=(0, 0, 800, 600),
        last_win_args=None,
        target_dir=tmp_path,
        workflow_id="wf_test",
        status_callback=None,
        check_stop_callback=lambda: None,
        update_ui_callback=lambda _m, _w=False: None,
        browser_element_located=False,
    )
    assert (next_i2, aborted2, healed2) == (5, False, False)


def test_heal_target_or_skip_allows_fallback_for_non_critical_move(tmp_path, monkeypatch):
    captured = {}

    def _fake_verify(**kw):
        captured["fast_fail"] = kw.get("fast_fail")
        return False, {"success": False, "reason": "element_not_found"}

    monkeypatch.setattr(step_verifier, "verify_and_heal_target", _fake_verify)

    next_i, aborted, healed = step_verifier.heal_target_or_skip(
        step_index=1,
        commands=[],
        args={"x": 100, "y": 200},
        target_id="tgt_2",
        raw_event_id="evt_0002",
        step_log={},
        loop_stack=[],
        current_win_rect=(0, 0, 800, 600),
        last_win_args=None,
        target_dir=tmp_path,
        workflow_id="wf_test",
        status_callback=None,
        check_stop_callback=lambda: None,
        update_ui_callback=lambda _m, _w=False: None,
        allow_fallback_on_failure=True,
    )
    assert captured["fast_fail"] is True
    assert (next_i, aborted, healed) == (2, False, False)


def test_stage3_ocr_outranks_canny_edge_when_interfering_same_size_buttons_exist(tmp_path, monkeypatch):
    wf_dir = tmp_path / "wf_ocr_over_edge"
    (wf_dir / "images").mkdir(parents=True)
    (wf_dir / "ui_targets.json").write_text(
        json.dumps({"tgt_8": {"semantic_role": "left_click", "ui_type": "unknown"}}),
        encoding="utf-8",
    )

    tpl = Image.new("RGB", (90, 36), (255, 255, 255))
    draw_tpl = ImageDraw.Draw(tpl)
    draw_tpl.rectangle((4, 4, 85, 31), outline=(20, 20, 20), width=3)
    tpl.save(wf_dir / "images" / "evt_0008_crop.png")

    # Identical border size box placed at wrong position (50, 50) while target OCR label is at (260, 180)
    screen = Image.new("RGB", (400, 300), (30, 30, 30))
    draw_scr = ImageDraw.Draw(screen)
    draw_scr.rectangle((54, 54, 135, 81), outline=(240, 240, 240), width=3)

    monkeypatch.setattr(recovery_manager, "get_macros_root", lambda: tmp_path)
    monkeypatch.setattr(
        recovery_manager,
        "take_screenshot",
        lambda: (screen, {"left": 0, "top": 0, "width": 400, "height": 300}),
    )
    monkeypatch.setattr(
        recovery_manager,
        "read_text_from_image",
        lambda _p: [
            SimpleNamespace(
                content="キャンセル",
                confidence=0.95,
                boundingBox=SimpleNamespace(x=54, y=54, width=80, height=28),
            ),
            SimpleNamespace(
                content="保存する",
                confidence=0.96,
                boundingBox=SimpleNamespace(x=220, y=165, width=80, height=30),
            ),
        ],
    )

    res = recovery_manager.attempt_recovery(
        workflow_id="wf_ocr_over_edge",
        target_id="tgt_8",
        window_hwnd=0,
        raw_event_id="evt_0008",
        target_context={"element_name": "保存する"},
        last_win_args=None,
    )

    assert res["success"] is True
    assert res["stage"] == 3
    assert res["new_coordinates"] == {"x": 260, "y": 180}


def test_recovery_manager_recovers_inverted_color_button_via_edge_match(tmp_path, monkeypatch):
    wf_dir = tmp_path / "wf_edge"
    (wf_dir / "images").mkdir(parents=True)
    (wf_dir / "ui_targets.json").write_text(
        json.dumps({"tgt_7": {"semantic_role": "left_click", "ui_type": "unknown"}}),
        encoding="utf-8",
    )

    tpl = Image.new("RGB", (90, 36), (255, 255, 255))
    draw_tpl = ImageDraw.Draw(tpl)
    draw_tpl.rectangle((4, 4, 85, 31), outline=(20, 20, 20), width=3)
    draw_tpl.line((20, 18, 70, 18), fill=(20, 20, 20), width=3)
    tpl.save(wf_dir / "images" / "evt_0007_crop.png")

    screen = Image.new("RGB", (400, 300), (30, 30, 30))
    draw_scr = ImageDraw.Draw(screen)
    draw_scr.rectangle((214, 154, 295, 181), outline=(240, 240, 240), width=3)
    draw_scr.line((230, 168, 280, 168), fill=(240, 240, 240), width=3)

    monkeypatch.setattr(recovery_manager, "get_macros_root", lambda: tmp_path)
    monkeypatch.setattr(
        recovery_manager,
        "take_screenshot",
        lambda: (screen, {"left": 0, "top": 0, "width": 400, "height": 300}),
    )

    res = recovery_manager.attempt_recovery(
        workflow_id="wf_edge",
        target_id="tgt_7",
        window_hwnd=0,
        raw_event_id="evt_0007",
        target_context={},
        last_win_args=None,
    )

    assert res["success"] is True
    assert res["stage"] == 2
    assert 240 <= res["new_coordinates"]["x"] <= 270
    assert 155 <= res["new_coordinates"]["y"] <= 180


def test_browser_uia_searcher_prefers_button_and_strictly_matches_browser_family(monkeypatch):
    import sys
    import types
    from core.executor import browser_uia_searcher
    from core.shared.window_utils import get_browser_family

    assert get_browser_family("Knowledge Base - メモ帳") == ""
    assert get_browser_family("Dashboard - Google Chrome") == "chrome"
    assert get_browser_family("Home - Microsoft Edge") == "edge"

    class _Rect:
        def __init__(self, left, top, right, bottom):
            self.left, self.top, self.right, self.bottom = left, top, right, bottom

    class _FakeCtrl:
        def __init__(self, name, ct_name, rect, aid=""):
            self.Name = name
            self.ControlTypeName = ct_name
            self.BoundingRectangle = rect
            self.AutomationId = aid
            self.IsOffscreen = False
            self.Element = f"elem:{ct_name}:{name}"

        def Exists(self, *_a):
            return True

    text_ctrl = _FakeCtrl("Submit", "TextControl", _Rect(100, 100, 180, 130))
    group_ctrl = _FakeCtrl("Submit", "GroupControl", _Rect(80, 80, 380, 280))
    btn_ctrl = _FakeCtrl("Submit", "ButtonControl", _Rect(110, 200, 190, 235))

    class _FakeScope:
        BoundingRectangle = _Rect(0, 0, 1000, 800)

        def Exists(self, *_a):
            return True

        def DocumentControl(self):
            return self

        def Control(self, **_kw):
            # Simulate native Name match returning non-interactive TextControl first
            return text_ctrl

    fake_auto = types.SimpleNamespace(
        ControlFromHandle=lambda _h: _FakeScope(),
        WalkControl=lambda _s, maxDepth=32: [(group_ctrl, 1), (text_ctrl, 2), (btn_ctrl, 3)],
    )
    fake_wrapper_mod = types.SimpleNamespace(UIAWrapper=lambda elem: elem)
    monkeypatch.setitem(sys.modules, "uiautomation", fake_auto)
    monkeypatch.setitem(sys.modules, "pywinauto.controls.uiawrapper", fake_wrapper_mod)

    chosen = browser_uia_searcher._find_in_document_tree(1234, [], ["submit"])
    assert chosen == "elem:ButtonControl:Submit"


def test_target_healer_local_gray_uses_matched_coords_and_detects_small_ui_change(tmp_path, monkeypatch):
    import numpy as np

    images_dir = tmp_path / "images"
    images_dir.mkdir(parents=True)

    # Create high-contrast patterned button template (with std >= 10)
    tpl = Image.new("RGB", (60, 28), (255, 255, 255))
    d_tpl = ImageDraw.Draw(tpl)
    d_tpl.rectangle((2, 2, 57, 25), outline=(10, 10, 10), width=3)
    d_tpl.line((10, 14, 50, 14), fill=(20, 20, 20), width=4)
    d_tpl.line((30, 6, 30, 22), fill=(20, 20, 20), width=3)
    tpl_path = images_dir / "evt_0020_crop.png"
    tpl.save(tpl_path)

    # Place grayscale-shifted version shifted by +20px X, +15px Y inside local search pad
    screen = Image.new("RGB", (300, 240), (240, 240, 240))
    d_scr = ImageDraw.Draw(screen)
    # Shifted box center is at (120, 115) instead of expected (100, 100)
    d_scr.rectangle((92, 103, 147, 126), fill=(180, 180, 180), outline=(40, 40, 40), width=3)
    d_scr.line((100, 115, 140, 115), fill=(40, 40, 40), width=4)
    d_scr.line((120, 107, 120, 123), fill=(40, 40, 40), width=3)

    # Verify small region change detector catches 80x30 UI appearance on large screen
    prev = np.full((1080, 1920, 3), 240, dtype=np.uint8)
    nxt = prev.copy()
    nxt[200:230, 400:480] = 30
    assert target_healer._has_small_region_changed(prev, nxt) is True
    assert target_healer._has_small_region_changed(prev, prev.copy()) is False

    # Check local gray match computes shifted center, not stale (100, 100)
    ok, _drift_fb, local_fb = target_healer._evaluate_template_on_screen(
        screen, {"left": 0, "top": 0}, tpl_path, {"x": 100, "y": 100}, (0, 0), "wf_test"
    )
    assert ok is False
    assert local_fb is not None
    assert 115 <= local_fb[0] <= 125
    assert 110 <= local_fb[1] <= 120


def test_step_dispatcher_dynamic_loop_and_safe_win_rect_sync(tmp_path, monkeypatch):
    from pynput.keyboard import Controller as KeyboardController
    from pynput.mouse import Controller as MouseController
    from core.executor import step_dispatcher

    dispatcher = step_dispatcher.StepDispatcher(
        workflow_id="wf_loop",
        target_dir=tmp_path,
        commands=[],
        mouse=MouseController(),
        keyboard=KeyboardController(),
    )

    heal_calls = []

    def _fake_heal(*a, **kw):
        heal_calls.append(a[0])
        return a[0] + 1, False, False

    monkeypatch.setattr(step_dispatcher, "heal_target_or_skip", _fake_heal)
    monkeypatch.setattr(step_dispatcher, "perform_screen_matching", lambda *a, **kw: None)
    monkeypatch.setattr(
        step_dispatcher,
        "execute_step_action",
        lambda **kw: (kw["step_index"] + 1, kw["last_win_args"], kw["current_win_rect"], None, None, False),
    )

    # 1. Loop iteration 0 (offset=0): must still verify/heal static base target
    cmd_loop = {
        "method": "click",
        "args": {"x": 100, "y": 100, "target_id": "tgt_1", "raw_event_id": "evt_0001", "seq_vars": {"y": {"step": 30}}},
    }
    dispatcher.dispatch_step(0, cmd_loop, {}, [{"current_iteration": 0}], "Step 1: click")
    assert heal_calls == [0]

    # 2. Loop iteration 1 (offset=30): must skip static crop healing
    dispatcher.dispatch_step(0, cmd_loop, {}, [{"current_iteration": 1}], "Step 1: click")
    assert heal_calls == [0]

    # 3. Outside loop with variable placeholder: must skip static crop healing
    cmd_var = {
        "method": "click",
        "args": {"x": 100, "y": 100, "element_name": "{{btn_label}}", "target_id": "tgt_2", "raw_event_id": "evt_0002"},
    }
    dispatcher.dispatch_step(1, cmd_var, {"btn_label": "OK"}, [], "Step 2: click")
    assert heal_calls == [0]

    # 4. _sync_current_win_rect preserves recorded offset origin and never overwrites with (0,0,0,0)
    dispatcher.current_win_rect = (120, 80, 900, 700)
    dispatcher._sync_current_win_rect({"mapped_hwnd": None, "x": 0, "y": 0, "width": 0, "height": 0})
    assert dispatcher.current_win_rect == (120, 80, 900, 700)

    monkeypatch.setattr(step_dispatcher, "get_window_rect_xywh", lambda _h: (500, 300, 1000, 800))
    dispatcher._sync_current_win_rect({"mapped_hwnd": 999, "x": 100, "y": 50, "width": 800, "height": 600})
    assert dispatcher.current_win_rect == (100, 50, 800, 600)

    dispatcher._sync_current_win_rect({"mapped_hwnd": 999, "x": 0, "y": 0, "width": 0, "height": 0})
    assert dispatcher.current_win_rect == (500, 300, 1000, 800)
