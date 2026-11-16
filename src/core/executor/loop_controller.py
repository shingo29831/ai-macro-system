"""Module: @role: マクロ実行における静的・Excelデータ駆動ループのスタック管理、シーケンス変数展開およびエラースキップ処理を担当する。"""

from pathlib import Path
from typing import Any, Dict, List, Optional
import logging

from core.executor.excel_handler import (
    extract_referenced_columns,
    read_excel_records,
    write_excel_status,
)

logger = logging.getLogger(__name__)


class LoopController:
    """ループ実行状態、データソースバインディング、およびシーケンス変数展開を統括するコントローラー"""

    @staticmethod
    def preload_excel_variables(commands: List[Dict[str, Any]], target_dir: Path, variables: Dict[str, Any]) -> None:
        """Why: loop_start前の操作でもExcel変数を参照できるよう第1レコードを先行ロード"""
        first_excel_loop = next(
            (c for c in commands if c.get("method") == "loop_start" and c.get("args", {}).get("data_source") == "excel"),
            None
        )
        if not first_excel_loop:
            return

        f_args = first_excel_loop.get("args", {})
        f_st_col = f_args.get("status_column")
        all_ref_cols = extract_referenced_columns(commands)
        # Why: 抽出対象列とステータス列が一致する場合はステータス列参照を解除
        if f_st_col and f_st_col.strip().upper() in all_ref_cols:
            f_st_col = None

        pre_records = read_excel_records(
            f_args.get("file_path"),
            f_args.get("sheet_name"),
            f_args.get("start_row", 2),
            f_args.get("end_row"),
            f_st_col,
            f_args.get("skip_completed", True),
            target_dir=target_dir
        )
        # Why: 全件完了済みの場合も第1行の変数を解決可能にするため全レコードから先行取得
        if not pre_records and f_args.get("skip_completed", True):
            pre_records = read_excel_records(
                f_args.get("file_path"),
                f_args.get("sheet_name"),
                f_args.get("start_row", 2),
                f_args.get("end_row"),
                f_args.get("status_column"),
                skip_completed=False,
                target_dir=target_dir
            )

        if pre_records:
            logger.info(f"Pre-loaded {len(pre_records)} Excel records. First row keys: {list(pre_records[0].keys())}")
            for k, v in pre_records[0].items():
                variables[k] = v
            variables[f_args.get("item_variable", "row")] = pre_records[0]

    @staticmethod
    def _find_matching_loop_end(commands: List[Dict[str, Any]], start_idx: int) -> int:
        nest = 1
        j = start_idx + 1
        while j < len(commands) and nest > 0:
            if commands[j].get("method") == "loop_start":
                nest += 1
            elif commands[j].get("method") == "loop_end":
                nest -= 1
            j += 1
        return j

    @staticmethod
    def apply_sequence_variables(args: Dict[str, Any], loop_stack: List[Dict[str, Any]]) -> None:
        """ループ内のインクリメント変数をステップ引数へ反映"""
        if not loop_stack:
            return
        current_loop = loop_stack[-1]
        iteration = current_loop["current_iteration"]
        seq_vars = args.get("seq_vars") or {}
        for key, seq_info in seq_vars.items():
            if isinstance(seq_info, dict) and "step" in seq_info:
                step_val = seq_info["step"]
                if key in args and args[key] is not None:
                    if key == "text":
                        try:
                            start_val = int(args[key])
                            args[key] = str(start_val + step_val * iteration)
                        except (ValueError, TypeError):
                            pass
                    elif isinstance(args[key], (int, float)) and not isinstance(args[key], bool):
                        args[key] = args[key] + step_val * iteration

    @staticmethod
    def handle_loop_start(
        i: int,
        commands: List[Dict[str, Any]],
        args: Dict[str, Any],
        loop_stack: List[Dict[str, Any]],
        variables: Dict[str, Any],
        target_dir: Path,
        workflow_id: str
    ) -> int:
        """loop_startの処理を行い、次に進むべきコマンドインデックスを返す"""
        data_source = args.get("data_source") or "static"
        raw_max_iter = args.get("max_iterations")
        max_iter = 10000 if raw_max_iter is None else int(raw_max_iter)

        if data_source == "excel":
            f_path = args.get("file_path")
            s_name = args.get("sheet_name")
            st_row = args.get("start_row", 2)
            ed_row = args.get("end_row")
            st_col = args.get("status_column")
            skip_comp = args.get("skip_completed", True)

            # Why: ループ内コマンドを走査し文字抽出対象セルへのステータス上書き破壊を完全遮断
            nest_sub = 1
            j_sub = i + 1
            sub_cmds = []
            while j_sub < len(commands) and nest_sub > 0:
                if commands[j_sub].get("method") == "loop_start":
                    nest_sub += 1
                elif commands[j_sub].get("method") == "loop_end":
                    nest_sub -= 1
                if nest_sub > 0:
                    sub_cmds.append(commands[j_sub])
                j_sub += 1

            extracted_cols = extract_referenced_columns(sub_cmds)
            if st_col and st_col.strip().upper() in extracted_cols:
                logger.warning(f"[{workflow_id}] status_column '{st_col}' overlaps with extracted data. Disabling status write.")
                st_col = None

            records = read_excel_records(f_path, s_name, st_row, ed_row, st_col, skip_comp, target_dir=target_dir)
            # Why: 全件完了済み等で未処理行が0件の場合、テスト再試行のため全レコードから再取得
            if not records and skip_comp:
                logger.info(f"[{workflow_id}] No uncompleted records found. Retrying with skip_completed=False.")
                records = read_excel_records(f_path, s_name, st_row, ed_row, st_col, skip_completed=False, target_dir=target_dir)

            clamped_records = records[:max(0, max_iter)]
            if not clamped_records:
                # Why: ファイル空等の真の0件時のみloop_endまでスキップして安全終了
                logger.warning(f"[{workflow_id}] No records available in Excel file. Skipping loop.")
                return LoopController._find_matching_loop_end(commands, i)

            item_var = args.get("item_variable") or "row"
            for k, v in clamped_records[0].items():
                variables[k] = v
            variables[item_var] = clamped_records[0]

            loop_stack.append({
                "start_index": i,
                "total_count": len(clamped_records),
                "current_iteration": 0,
                "data_source": "excel",
                "records": clamped_records,
                "file_path": f_path,
                "sheet_name": s_name,
                "status_column": st_col,
                "target_dir": target_dir,
                "item_variable": item_var,
                "expanded_keys": list(clamped_records[0].keys()),
                "continue_on_error": bool(args.get("continue_on_error", False)),
            })
        elif data_source == "variable":
            items_var = args.get("items_variable")
            raw_items = variables.get(items_var) if items_var else []
            if isinstance(raw_items, str):
                import json
                try:
                    parsed = json.loads(raw_items)
                    items = list(parsed) if isinstance(parsed, (list, tuple)) else [parsed]
                except Exception:
                    items = [line for line in raw_items.splitlines() if line]
            elif isinstance(raw_items, (list, tuple)):
                items = list(raw_items)
            else:
                items = []

            clamped_items = items[:max(0, max_iter)]
            if not clamped_items:
                logger.warning(f"[{workflow_id}] Variable loop '{items_var}' has 0 items. Skipping loop.")
                return LoopController._find_matching_loop_end(commands, i)

            item_var = args.get("item_variable") or "row"
            first_item = clamped_items[0]
            expanded_keys: List[str] = []
            if isinstance(first_item, dict):
                for k, v in first_item.items():
                    variables[k] = v
                    expanded_keys.append(k)
            variables[item_var] = first_item

            loop_stack.append({
                "start_index": i,
                "total_count": len(clamped_items),
                "current_iteration": 0,
                "data_source": "variable",
                "records": clamped_items,
                "item_variable": item_var,
                "expanded_keys": expanded_keys,
                "continue_on_error": bool(args.get("continue_on_error", False)),
            })
        else:
            raw_count = args.get("loop_count")
            loop_count = 10 if raw_count is None else int(raw_count)
            effective_count = min(loop_count, max(0, max_iter))
            # Why: 回数0以下の静的ループで本体が1回誤実行されるのを防止
            if effective_count <= 0:
                return LoopController._find_matching_loop_end(commands, i)
            loop_stack.append({
                "start_index": i,
                "total_count": effective_count,
                "current_iteration": 0,
                "data_source": "static",
                "continue_on_error": bool(args.get("continue_on_error", False)),
            })
        return i + 1

    @staticmethod
    def handle_loop_end(i: int, loop_stack: List[Dict[str, Any]], variables: Dict[str, Any]) -> int:
        """loop_endの処理を行い、次に進むべきコマンドインデックスを返す"""
        if not loop_stack:
            return i + 1

        current_loop = loop_stack[-1]
        if current_loop.get("data_source") == "excel":
            # Why: エラー行に対する「完了」ステータスの上書きを防止
            if not current_loop.pop("current_had_error", False):
                curr_rec = current_loop["records"][current_loop["current_iteration"]]
                st_col = current_loop.get("status_column")
                f_path = current_loop.get("file_path")
                s_name = current_loop.get("sheet_name")
                t_dir = current_loop.get("target_dir")
                if st_col and f_path:
                    write_excel_status(f_path, s_name, curr_rec.get("_row_idx"), st_col, "完了", target_dir=t_dir)
        else:
            current_loop.pop("current_had_error", False)

        current_loop["current_iteration"] += 1
        if current_loop["current_iteration"] < current_loop["total_count"]:
            if current_loop.get("data_source") in ("excel", "variable"):
                # Why: 要素間で辞書キーが不揃いな場合に前周回の展開変数が残留するのを防止
                for prev_k in current_loop.get("expanded_keys", []):
                    variables.pop(prev_k, None)
                next_rec = current_loop["records"][current_loop["current_iteration"]]
                item_var = current_loop.get("item_variable") or "row"
                new_keys: List[str] = []
                if isinstance(next_rec, dict):
                    for k, v in next_rec.items():
                        variables[k] = v
                        new_keys.append(k)
                current_loop["expanded_keys"] = new_keys
                variables[item_var] = next_rec
            return current_loop["start_index"] + 1
        else:
            # Why: ループ完了後の最終レコード展開キーとitem_variable残留による後続ステップ誤参照を防止
            if current_loop.get("data_source") in ("excel", "variable"):
                for prev_k in current_loop.get("expanded_keys", []):
                    variables.pop(prev_k, None)
                item_var = current_loop.get("item_variable")
                if item_var:
                    variables.pop(item_var, None)
            loop_stack.pop()
            if loop_stack and loop_stack[-1].get("data_source") in ("excel", "variable"):
                parent_loop = loop_stack[-1]
                parent_rec = parent_loop["records"][parent_loop["current_iteration"]]
                parent_item_var = parent_loop.get("item_variable") or "row"
                if isinstance(parent_rec, dict):
                    for k, v in parent_rec.items():
                        variables[k] = v
                variables[parent_item_var] = parent_rec
            return i + 1

    @staticmethod
    def mark_current_iteration_error(loop_stack: List[Dict[str, Any]], error_msg: str) -> None:
        """Why: 異常停止時にExcelのステータス列へエラー詳細を即時書き戻し二重処理を抑止"""
        if not loop_stack or loop_stack[-1].get("data_source") != "excel":
            return
        try:
            curr_loop = loop_stack[-1]
            rec = curr_loop["records"][curr_loop["current_iteration"]]
            s_col = curr_loop.get("status_column")
            fp = curr_loop.get("file_path")
            sn = curr_loop.get("sheet_name")
            t_dir = curr_loop.get("target_dir")
            if s_col and fp:
                write_excel_status(fp, sn, rec.get("_row_idx"), s_col, f"エラー: {error_msg[:30]}", target_dir=t_dir)
        except Exception as e:
            logger.warning(f"Failed to write error status to Excel: {e}")
