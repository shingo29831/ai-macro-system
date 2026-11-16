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
