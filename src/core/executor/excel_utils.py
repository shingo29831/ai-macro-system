"""Module: @role: Excel操作で共通利用される列記号変換、参照列抽出、ファイルロック検知、およびレコード整形ヘルパーを提供する。"""

from pathlib import Path
import re
from typing import Any, Dict, Iterable, Optional, Set

COMPLETED_STATUSES = frozenset({"完了", "DONE", "済", "スキップ", "SUCCESS"})


def col_idx_to_letter(col_idx: int) -> str:
    # Why: openpyxl依存なしで26列超(AA, AB等)のExcel列記号を確実に算出
    res = ""
    while col_idx > 0:
        col_idx, rem = divmod(col_idx - 1, 26)
        res = chr(65 + rem) + res
    return res


def extract_referenced_columns(
    commands_slice: list[Dict[str, Any]],
    header_map: Optional[Dict[str, str]] = None,
) -> Set[str]:
    # Why: 日本語ヘッダー名と列文字の双方を網羅検出し元データ列の上書き破壊を完全遮断
    ref_cols: Set[str] = set()
    pattern = re.compile(r"\{\{\s*([^{}]+?)\s*\}\}|\$\{([^{}]+?)\}")

    def _scan(val: Any) -> None:
        if isinstance(val, str):
            for m in pattern.finditer(val):
                token = (m.group(1) or m.group(2)).strip()
                if "." in token:
                    token = token.split(".")[-1].strip()
                ref_cols.add(token.upper())
                if header_map:
                    for col_letter, h_name in header_map.items():
                        if token.lower() == str(h_name).lower():
                            ref_cols.add(col_letter.upper())
        elif isinstance(val, dict):
            for v in val.values():
                _scan(v)
        elif isinstance(val, list):
            for item in val:
                _scan(item)

    for c in commands_slice:
        _scan(c.get("args", {}))
    return ref_cols


def is_file_locked(path_str: str) -> bool:
    # Why: ファイル排他ロックの事前検知によりopenpyxl PermissionErrorを完全回避
    if not Path(path_str).exists():
        return False
    try:
        with open(path_str, "a+b"):
            pass
        return False
    except (PermissionError, IOError):
        return True


def format_excel_cell_value(raw_val: Any) -> str:
    # Why: Excel数値のfloat化(100 -> 100.0)や指数表記を防ぎコード値の整合性を担保
    if raw_val is None:
        return ""
    if hasattr(raw_val, "strftime"):
        return raw_val.strftime("%Y-%m-%d")
    if isinstance(raw_val, float) and raw_val.is_integer():
        return str(int(raw_val))
    return str(raw_val).strip()


def build_excel_row_record(
    row_idx: int,
    cells: Iterable[Any],
    header_map: Dict[str, str],
) -> Optional[Dict[str, Any]]:
    cell_list = list(cells)
    if not any(c is not None and str(c).strip() != "" for c in cell_list):
        return None

    row_dict: Dict[str, Any] = {"_row_idx": row_idx}
    for idx, cell in enumerate(cell_list):
        col_letter = col_idx_to_letter(idx + 1)
        val_str = format_excel_cell_value(cell)
        row_dict[col_letter] = val_str
        row_dict[f"列{col_letter}"] = val_str
        row_dict[f"{col_letter}列"] = val_str
        if col_letter in header_map:
            row_dict[header_map[col_letter]] = val_str
    return row_dict


def is_completed_record(row_dict: Dict[str, Any], status_col: Optional[str]) -> bool:
    # Why: 中断・再テスト時のスタックを防ぐため「処理中」は未完了扱いで再実行
    if not status_col:
        return False
    cur_status = str(row_dict.get(status_col.upper(), "")).strip()
    return cur_status in COMPLETED_STATUSES
