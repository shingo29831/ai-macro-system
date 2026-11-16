"""Module: @role: ワークフロー生成パイプライン全体で共有するシステムUIイベント除外判定を提供する。"""

from typing import Any, Dict


def is_system_ui_event(evt: Dict[str, Any]) -> bool:
    """自システムやOS入力UI等に由来する除外対象イベントかを判定する。"""
    ctx = evt.get("app_context") or {}
    elem_name = str(ctx.get("element_name", "")).lower()
    elem_text = str(ctx.get("text", "")).lower()
    role = str(evt.get("semantic_role", "")).lower()
    css = str(ctx.get("css_selector", "")).lower()
    c_type = str(ctx.get("control_type", "")).lower()
    system_terms = [
        "記録を終了",
        "記録中",
        "停止中",
        "実行中",
        "ai macro system",
        "マクロ生成中",
        "qapplication.qwidget",
        "windows 入力エクスペリエンス",
        "textinputhost",
        "corewindow",
        "windows.ui.core",
    ]
    return any(
        term in elem_name or term in elem_text or term in role or term in css or term in c_type
        for term in system_terms
    )
