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
    def apply_sequence_variables(args: Dict[str, Any], loop_stack: List[Dict[str, Any]]) -> None:
        """ループ内のインクリメント変数をステップ引数へ反映"""
        if not loop_stack:
            return
        current_loop = loop_stack[-1]
        iteration = current_loop["current_iteration"]
        seq_vars = args.get("seq_vars", {})
        for key, seq_info in seq_vars.items():
            if isinstance(seq_info, dict) and "step" in seq_info:
                step_val = seq_info["step"]
                if key in args:
                    if key == "text":
                        try:
                            start_val = int(args[key])
                            args[key] = str(start_val + step_val * iteration)
                        except ValueError:
                            pass
                    else:
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
        data_source = args.get("data_source", "static")
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

            if not records:
                # Why: ファイル空等の真の0件時のみloop_endまでスキップして安全終了
                logger.warning(f"[{workflow_id}] No records available in Excel file. Skipping loop.")
                nest = 1
                j = i + 1
                while j < len(commands) and nest > 0:
                    if commands[j].get("method") == "loop_start":
                        nest += 1
                    elif commands[j].get("method") == "loop_end":
                        nest -= 1
                    j += 1
                return j

            item_var = args.get("item_variable", "row")
            for k, v in records[0].items():
                variables[k] = v
            variables[item_var] = records[0]

            max_iter = args.get("max_iterations", 10000)
            clamped_records = records[:max_iter]
            loop_stack.append({
                "start_index": i,
                "total_count": len(clamped_records),
                "current_iteration": 0,
                "data_source": "excel",
                "records": clamped_records,
                "file_path": f_path,
                "sheet_name": s_name,
                "status_column": st_col,
                "item_variable": item_var,
                "continue_on_error": args.get("continue_on_error", False)
            })
        else:
            loop_count = args.get("loop_count", 10)
            loop_stack.append({
                "start_index": i,
                "total_count": loop_count,
                "current_iteration": 0,
                "data_source": "static"
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
                if st_col and f_path:
                    write_excel_status(f_path, s_name, curr_rec.get("_row_idx"), st_col, "完了")

        current_loop["current_iteration"] += 1
        if current_loop["current_iteration"] < current_loop["total_count"]:
            if current_loop.get("data_source") == "excel":
                next_rec = current_loop["records"][current_loop["current_iteration"]]
                item_var = current_loop.get("item_variable", "row")
                for k, v in next_rec.items():
                    variables[k] = v
                variables[item_var] = next_rec
            return current_loop["start_index"] + 1
        else:
            loop_stack.pop()
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
            if s_col and fp:
                write_excel_status(fp, sn, rec.get("_row_idx"), s_col, f"エラー: {error_msg[:30]}")
        except Exception as e:
            logger.warning(f"Failed to write error status to Excel: {e}")
