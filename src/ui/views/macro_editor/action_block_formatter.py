"""Module: @role: マクロアクションブロックのタイトル・要約文生成およびスクリーンショットへのカーソル描画を担当する。"""

from pathlib import Path
from typing import Any, Dict

from PySide6.QtGui import QColor, QPainter, QPen, QPixmap


_BROWSER_ACTION_TITLES: Dict[str, str] = {
    "open_url": "ブラウザ: URLを開く",
    "click_element": "ブラウザ: 要素クリック",
    "type_text": "ブラウザ: テキスト入力",
    "select_option": "ブラウザ: リスト選択",
    "set_checkbox": "ブラウザ: チェックボックス",
    "read_text": "ブラウザ: テキスト取得",
    "wait_element": "ブラウザ: 要素待機",
    "close_tab": "ブラウザ: タブを閉じる",
    "switch_tab": "ブラウザ: タブ切り替え",
}

_EXCEL_ACTION_TITLES: Dict[str, str] = {
    "open_workbook": "Excel: ブックを開く",
    "save_workbook": "Excel: ブックを保存",
    "close_workbook": "Excel: ブックを閉じる",
    "write_cell": "Excel: セル書き込み",
    "read_cell": "Excel: セル読み取り",
    "select_sheet": "Excel: シート選択",
}

_METHOD_TITLES: Dict[str, str] = {
    "click": "クリック",
    "move": "マウス移動",
    "type_text": "テキスト入力",
    "press_key": "キー入力",
    "wait": "待機",
    "scroll": "スクロール",
    "activate_window": "ウィンドウアクティブ化",
    "loop_start": "ループ開始",
    "loop_end": "ループ終了",
}


def get_action_title(method: str, args: Dict[str, Any]) -> str:
    """アクション種別と引数からブロック表示用タイトルを生成する。"""
    if method == "loop_start" and args.get("data_source") == "excel":
        return "Excelデータ連携ループ"
    if method == "browser_action":
        return _BROWSER_ACTION_TITLES.get(args.get("action", ""), "ブラウザ操作")
    if method == "excel_action":
        return _EXCEL_ACTION_TITLES.get(args.get("action", ""), "Excel操作")
    return _METHOD_TITLES.get(method, method)


def get_action_info_text(method: str, args: Dict[str, Any]) -> str:
    """アクション引数の内容をユーザー向け要約文字列へ整形する。"""
    if method == "click":
        name = args.get("element_name", "")
        btn = "左" if args.get("button") == "left" else "右"
        coords = f"({args.get('x', 0)}, {args.get('y', 0)})"
        return f"対象: {name} | {btn}クリック {coords}" if name else f"{btn}クリック {coords}"
    if method == "move":
        name = args.get("element_name", "")
        coords = f"({args.get('x', 0)}, {args.get('y', 0)})"
        return f"ホバー対象: {name} {coords}" if name else f"カーソル移動 {coords}"
    if method == "scroll":
        dy = args.get("dy", 0.0)
        direction = "下スクロール" if dy < 0 else "上スクロール"
        coords = f"({args.get('x', 0)}, {args.get('y', 0)})"
        return f"{direction}: {abs(dy)} {coords}"
    if method == "type_text":
        info = f"入力内容: {args.get('text', '')}"
        flags = []
        if args.get("clear_before_typing"):
            flags.append("クリア有")
        if args.get("use_clipboard"):
            flags.append("貼付")
        return f"{info} ({', '.join(flags)})" if flags else info
    if method == "press_key":
        return f"キー: {args.get('key', '')}"
    if method == "wait":
        return f"待機時間: {args.get('duration', 0)} 秒"
    if method == "activate_window":
        return f"対象: {args.get('window_title', '')}"
    if method == "browser_action":
        return _format_browser_info(args)
    if method == "excel_action":
        return _format_excel_info(args)
    if method == "loop_start":
        if args.get("data_source") == "excel":
            f_name = Path(args.get("file_path", "")).name if args.get("file_path") else "未選択"
            st_col = args.get("status_column") or "-"
            return f"Excel: {f_name} | ステータス列: {st_col}"
        return f"回数: {args.get('loop_count', 1)} 回"
    return ""


def _format_browser_info(args: Dict[str, Any]) -> str:
    b_act = args.get("action", "")
    elem = args.get("element_name") or args.get("selector") or ""
    target_url = args.get("url", "")
    if b_act == "open_url":
        return f"URL: {target_url}"
    if b_act == "click_element":
        url_extra = f" ({target_url})" if target_url else ""
        return f"対象: {elem}{url_extra}" if elem else f"URLクリック: {target_url}"
    if b_act == "type_text":
        return f"対象: {elem} | 入力: {args.get('text', '')}"
    if b_act == "select_option":
        return f"対象: {elem} | 選択値: {args.get('value') or args.get('text', '')}"
    if b_act == "set_checkbox":
        val = args.get("value", True)
        return f"対象: {elem} | 状態: {'ON' if val in [True, 'True', 'true'] else 'OFF'}"
    if b_act == "read_text":
        return f"対象: {elem} | 変数: {args.get('variable_name', '')}"
    return f"操作: {b_act}"


def _format_excel_info(args: Dict[str, Any]) -> str:
    e_act = args.get("action", "")
    cell = args.get("cell", "")
    val = args.get("value", "")
    if e_act == "write_cell":
        return f"セル: {cell} | 書込値: {val}"
    if e_act == "read_cell":
        return f"セル: {cell} | 変数名: {args.get('variable_name', '')}"
    f_name = Path(args.get("file_path", "")).name if args.get("file_path") else "-"
    return f"操作: {e_act} | ファイル: {f_name}"


def draw_cursor_on_pixmap(pixmap: QPixmap, x: int, y: int) -> QPixmap:
    """スクリーンショット画像上の操作座標に視認用ターゲットマーカーを描画する。"""
    painter = QPainter(pixmap)
    painter.setRenderHint(QPainter.RenderHint.Antialiasing)
    pen = QPen(QColor(255, 0, 0, 200), 4)
    painter.setPen(pen)
    painter.setBrush(QColor(255, 0, 0, 80))
    radius = 24
    painter.drawEllipse(x - radius, y - radius, radius * 2, radius * 2)
    painter.drawLine(x - radius - 10, y, x + radius + 10, y)
    painter.drawLine(x, y - radius - 10, x, y + radius + 10)
    painter.end()
    return pixmap
