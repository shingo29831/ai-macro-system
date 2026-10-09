"""Module: @role: マクロ実行前の対象ウィンドウ抽出・グループ化およびWindowMappingDialogを通じたHWND解決を担当する。"""

from typing import Any, Dict, List
import re
from PySide6.QtWidgets import QDialog, QWidget

from ui.views.window_mapping_dialog import WindowMappingDialog

IGNORED_SYSTEM_TITLES = [
    "マクロ生成中", "aiマクロ生成中", "ai macro system", "記録中", "停止中",
    "実行中", "設定", "ウィンドウの紐付け", "program manager", "taskbar"
]


def _extract_app(t: str) -> str:
    parts = re.split(r"[\-—–―]", t)
    app = parts[-1].strip()
    return app if app else t.strip()


def resolve_window_mapping(parent: QWidget, commands: List[Dict[str, Any]]) -> bool:
    """コマンドリストから対象ウィンドウを抽出し、WindowMappingDialogを通じてHWNDと起動オプションを各コマンドへ反映する"""
    groups: Dict[str, Dict[str, Any]] = {}

    for cmd in commands:
        if cmd.get("method") == "activate_window":
            args = cmd.get("args", {})
            title = args.get("window_title", "").strip()
            alias = args.get("window_alias", "")
            if not title or any(kw in title.lower() for kw in IGNORED_SYSTEM_TITLES):
                continue

            app_key = _extract_app(title).lower()
            group_key = alias if alias else app_key

            if group_key not in groups:
                groups[group_key] = {
                    "display_title": title,
                    "titles": set(),
                    "aliases": set()
                }
            groups[group_key]["titles"].add(title)
            if alias:
                groups[group_key]["aliases"].add(alias)
            # より具体的なタイトル（例: 'Book1 - Excel' > 'Excel'）を表示用タイトルとして採用
            if len(title) > len(groups[group_key]["display_title"]):
                groups[group_key]["display_title"] = title

    unique_titles = [g["display_title"] for g in groups.values()]
    has_loop = any(cmd.get("method") == "loop_start" for cmd in commands)

    if not unique_titles:
        return True

    dialog = WindowMappingDialog(unique_titles, has_loop=has_loop, parent=parent)
    if dialog.exec() != QDialog.Accepted:
        return False

    mapping = dialog.get_mapping()
    loop_launch_each_time = dialog.get_loop_launch_each_time()

    for grp in groups.values():
        disp = grp["display_title"]
        if disp in mapping:
            mapped_hwnd = mapping[disp]
            for cmd in commands:
                if cmd.get("method") == "activate_window":
                    c_args = cmd.get("args", {})
                    c_title = c_args.get("window_title", "").strip()
                    c_alias = c_args.get("window_alias", "")
                    if c_title in grp["titles"] or (c_alias and c_alias in grp["aliases"]):
                        c_args["mapped_hwnd"] = mapped_hwnd
                        # Why: 新規起動(-1)かつユーザーが希望した場合のみループ毎起動フラグを付与
                        if mapped_hwnd == -1 and loop_launch_each_time:
                            c_args["loop_launch_each_time"] = True
                        else:
                            c_args["loop_launch_each_time"] = False
                        if loop_launch_each_time:
                            c_args["loop_launch_each_time"] = True

    return True
