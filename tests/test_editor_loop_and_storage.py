"""Tests for macro editor loop logic, macro storage, and ViewModel filtering."""

import pytest

from models.data_types import MacroSummary
from ui.viewmodels import macro_storage
from ui.views.macro_editor.canvas_loop_logic import (
    analyze_loops,
    ensure_loop_ids,
    generate_unique_loop_id,
    validate_loops,
)


def test_canvas_loop_logic_ids_validation_and_lanes():
    commands = [
        {"method": "loop_start", "loop_id": None, "args": {"loop_count": 3}},
        {"method": "loop_start", "args": {"loop_count": 2}},
        {"method": "click", "args": {"x": 10, "y": 20}},
        {"method": "loop_end", "args": {}},
        {"method": "loop_end", "args": {}},
    ]
    ensure_loop_ids(commands)
    assert commands[0]["loop_id"] == "loop_0"
    assert commands[1]["loop_id"] == "loop_1"
    assert commands[3]["loop_id"] == "loop_1"
    assert commands[4]["loop_id"] == "loop_0"

    assert validate_loops(commands) is True
    assert generate_unique_loop_id(commands) == "loop_2"

    lanes = analyze_loops(commands)
    assert len(lanes) == 2
    assert {item["lane"] for item in lanes} == {0, 1}

    empty_loop = [
        {"method": "loop_start", "loop_id": "loop_0", "args": {"loop_count": 2}},
        {"method": "loop_end", "loop_id": "loop_0", "args": {}},
    ]
    assert validate_loops(empty_loop) is False


def test_macro_storage_list_save_load_and_delete(tmp_path, monkeypatch):
    macros_root = tmp_path / "macros"
    macros_root.mkdir()
    monkeypatch.setattr(macro_storage, "get_macros_root_path", lambda: macros_root)

    (macros_root / "wf_2").mkdir()
    (macros_root / "wf_10").mkdir()
    macro_storage.save_executable_commands("wf_2", [{"method": "wait", "args": {"duration": 0.5}}])
    macro_storage.save_executable_commands("wf_10", [{"method": "click", "args": {"x": 1, "y": 2}}])

    loaded = macro_storage.load_executable_commands("wf_2")
    assert loaded == [{"method": "wait", "args": {"duration": 0.5}}]

    summaries, id_map = macro_storage.list_macro_summaries()
    assert set(id_map.keys()) == {"マクロ 2", "マクロ 10"}
    assert len(summaries) == 2

    deleted = macro_storage.delete_macro_folders(["wf_2"])
    assert deleted == 1
    assert not (macros_root / "wf_2").exists()


def test_main_viewmodel_filter_and_sort_macros(monkeypatch):
    pytest.importorskip("PySide6")
    from ui.viewmodels import main_viewmodel

    monkeypatch.setattr(main_viewmodel.os_hook, "set_shortcut_stop_callback", lambda cb: None)
    vm = main_viewmodel.MainViewModel()
    items = [
        MacroSummary(name="マクロ 10", status="success", status_text="待機中", heals="0回", heal_level="none", last_run="-", created_timestamp=10.0, updated_timestamp=20.0),
        MacroSummary(name="マクロ 2", status="success", status_text="待機中", heals="0回", heal_level="none", last_run="-", created_timestamp=30.0, updated_timestamp=10.0),
    ]
    sorted_natural = vm.filter_and_sort_macros(items, "", "マクロ名順")
    assert [m.name for m in sorted_natural] == ["マクロ 2", "マクロ 10"]

    filtered = vm.filter_and_sort_macros(items, "10", "更新が新しい順")
    assert [m.name for m in filtered] == ["マクロ 10"]
