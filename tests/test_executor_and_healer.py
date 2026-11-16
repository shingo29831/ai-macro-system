"""Tests for variable resolution, Excel I/O, LoopController, and healer matching."""

import openpyxl

from core.executor.excel_data_io import read_excel_records, skip_failed_loop_step, write_excel_status
from core.executor.excel_utils import (
    col_idx_to_letter,
    extract_referenced_columns,
    format_excel_cell_value,
    is_completed_record,
)
from core.executor.input_helper import resolve_variables
from core.executor.loop_controller import LoopController
from core.healer.recovery_manager import _is_logo_symbol_noise, _match_target_text


def test_resolve_variables_single_pass_and_nested_case_insensitive():
    variables = {
        "A": "100",
        "b": "200",
        "injected": "{{secret}}",
        "secret": "LEAKED",
        "row": {"A": "Alice", "Status": "Done"},
        "nested": {"level1": {"level2": "DeepVal"}},
    }
    assert resolve_variables("{{ A }} / {{列A}} / {{列b}} / {{row.status}}", variables) == "100 / 100 / 200 / Done"
    assert resolve_variables("{{nested.level1.level2}}", variables) == "DeepVal"
    # Must not double-interpolate {{secret}} inside injected value
    assert resolve_variables("{{injected}}", variables) == "{{secret}}"


def test_excel_utils_and_io_roundtrip(tmp_path):
    assert col_idx_to_letter(1) == "A"
    assert col_idx_to_letter(27) == "AA"
    assert format_excel_cell_value(100.0) == "100"
    assert is_completed_record({"E": "完了", "Status": "完了"}, "Status") is True
    assert is_completed_record({"E": "処理中", "Status": "処理中"}, "Status") is False

    refs = extract_referenced_columns(
        [{"args": {"text": "{{名前}} - ${row.B}"}}],
        header_map={"A": "名前", "B": "金額"},
    )
    assert "A" in refs and "B" in refs

    xlsx = tmp_path / "data.xlsx"
    wb = openpyxl.Workbook()
    ws = wb.active
    ws.append(["Name", "Status"])
    ws.append(["Alice", "完了"])
    ws.append(["Bob", ""])
    wb.save(xlsx)
    wb.close()

    records = read_excel_records("data.xlsx", status_col="Status", skip_completed=True, target_dir=tmp_path)
    assert len(records) == 1
    assert records[0]["Name"] == "Bob"

    # Writing by header name (case-insensitive) resolves to the Status column (B)
    write_excel_status("data.xlsx", None, records[0]["_row_idx"], "status", "完了", target_dir=tmp_path)
    all_after = read_excel_records("data.xlsx", status_col="Status", skip_completed=True, target_dir=tmp_path)
    assert all_after == []


def test_loop_controller_static_variable_and_excel_error_skip(tmp_path):
    # 1. Static loop with count=0 skips directly past loop_end
    cmds_zero = [
        {"method": "loop_start", "args": {"data_source": "static", "loop_count": 0}},
        {"method": "wait", "args": {"duration": 1.0}},
        {"method": "loop_end", "args": {}},
    ]
    stack = []
    next_i = LoopController.handle_loop_start(0, cmds_zero, cmds_zero[0]["args"], stack, {}, tmp_path, "wf_t")
    assert next_i == 3
    assert stack == []

    # 2. Variable loop iterates over ragged dicts and cleans up expanded keys on completion
    cmds_var = [
        {"method": "loop_start", "args": {"data_source": "variable", "items_variable": "users", "item_variable": "u"}},
        {"method": "type_text", "args": {"text": "{{u.name}}"}},
        {"method": "loop_end", "args": {}},
    ]
    variables = {"users": [{"name": "Taro", "role": "admin"}, {"name": "Hanako"}], "keep_me": "ok"}
    stack = []
    idx = LoopController.handle_loop_start(0, cmds_var, cmds_var[0]["args"], stack, variables, tmp_path, "wf_t")
    assert idx == 1
    assert variables["u"]["name"] == "Taro"
    assert variables["role"] == "admin"
    idx = LoopController.handle_loop_end(2, stack, variables)
    assert idx == 1
    assert variables["u"]["name"] == "Hanako"
    assert "role" not in variables
    idx = LoopController.handle_loop_end(2, stack, variables)
    assert idx == 3
    assert stack == []
    assert "name" not in variables
    assert "u" not in variables
    assert variables["keep_me"] == "ok"

    # 3. Sequence variables safely handle seq_vars=None and None target values
    step_args = {"x": 100, "text": "10", "seq_vars": None}
    LoopController.apply_sequence_variables(step_args, [{"current_iteration": 2}])
    assert step_args["x"] == 100

    step_args_none = {"x": None, "text": None, "seq_vars": {"x": {"step": 10}, "text": {"step": 5}}}
    LoopController.apply_sequence_variables(step_args_none, [{"current_iteration": 2}])
    assert step_args_none["x"] is None
    assert step_args_none["text"] is None

    step_args2 = {"x": 100, "text": "10", "seq_vars": {"x": {"step": 20}, "text": {"step": 5}}}
    LoopController.apply_sequence_variables(step_args2, [{"current_iteration": 2}])
    assert step_args2["x"] == 140
    assert step_args2["text"] == "20"

    # 4. Excel continue_on_error skips to loop_end and writes error status by header name without overwriting with 完了
    xlsx = tmp_path / "loop.xlsx"
    wb = openpyxl.Workbook()
    ws = wb.active
    ws.append(["Item", "Status"])
    ws.append(["Row1", ""])
    ws.append(["Row2", ""])
    wb.save(xlsx)
    wb.close()

    cmds_xl = [
        {"method": "loop_start", "args": {"data_source": "excel", "file_path": "loop.xlsx", "status_column": "Status", "continue_on_error": True}},
        {"method": "type_text", "args": {"text": "{{A}}"}},
        {"method": "loop_end", "args": {}},
    ]
    xl_vars = {}
    xl_stack = []
    LoopController.handle_loop_start(0, cmds_xl, cmds_xl[0]["args"], xl_stack, xl_vars, tmp_path, "wf_xl")
    jump_i = skip_failed_loop_step(1, cmds_xl, xl_stack, "wf_xl", None, "boom")
    assert jump_i == 2
    next_after_end = LoopController.handle_loop_end(2, xl_stack, xl_vars)
    assert next_after_end == 1

    # Complete remaining iteration and confirm loop variables are cleaned up on final exit
    next_after_end = LoopController.handle_loop_end(2, xl_stack, xl_vars)
    assert next_after_end == 3
    assert xl_stack == []
    assert "Item" not in xl_vars
    assert "row" not in xl_vars

    wb_check = openpyxl.load_workbook(xlsx)
    assert str(wb_check.active["B2"].value).startswith("エラー:")
    assert wb_check.active["B3"].value == "完了"
    wb_check.close()


def test_healer_noise_filter_and_text_matching():
    assert _is_logo_symbol_noise("---") is True
    assert _is_logo_symbol_noise("o") is True
    assert _is_logo_symbol_noise("Submit") is False

    matched, score = _match_target_text("ログイン", "ログインする")
    assert matched is True and score >= 0.9
    matched_bad, _ = _match_target_text("キャンセル", "ログイン")
    assert matched_bad is False
