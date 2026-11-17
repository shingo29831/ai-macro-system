"""Module: @role: アクティブアプリのプロファイルに応じて使用する複数ログの取捨選択・ルーティングを担当する。"""

from typing import Any, Dict, List, Optional

from core.shared.app_log_profile import (
    AppProfile,
    detect_app_profile,
    get_app_log_policy,
    has_explicit_app_identity,
    is_channel_allowed_for_profile,
    resolve_log_channels,
    sanitize_context_for_profile,
)


def _find_recent_office_source(routed_logs: List[Dict[str, Any]]) -> Optional[Dict[str, Any]]:
    """直近のイベント列からフォーカスアウト確定元のExcel操作ログを探索する。"""
    for prev in reversed(routed_logs[-4:]):
        prev_type = str(prev.get("Type", "")).lower()
        if "office" in prev_type or "recording" in prev_type or "meta" in prev_type:
            continue
        if get_app_log_policy(detect_app_profile(prev), source=prev).use_office_com:
            if any(k in prev_type for k in ("key", "type", "click", "drag")):
                return prev
    return None


def route_raw_logs_by_active_app(raw_logs: List[Dict[str, Any]]) -> List[Dict[str, Any]]:
    """各生ログエントリのアクティブアプリを判定し、そのアプリで必要な複数種類のログチャネルのみを通過させる。"""
    if not raw_logs:
        return []

    routed: List[Dict[str, Any]] = []
    last_active_profile: AppProfile = AppProfile.DESKTOP
    last_valid_window_entry: Optional[Dict[str, Any]] = None

    for raw_entry in raw_logs:
        if not isinstance(raw_entry, dict):
            continue

        entry = dict(raw_entry)
        raw_type = str(entry.get("Type", "")).strip()
        r_lower = raw_type.lower()

        if any(term in r_lower for term in ("recording", "meta")):
            routed.append(entry)
            continue

        # Why: ルーティング前に空タイトルのmouse_scrollへ直前の有為ウィンドウ情報を継承し誤判定を防止
        win_name = str(entry.get("WindowName") or "").strip()
        if not win_name and "scroll" in r_lower and last_valid_window_entry is not None:
            entry["WindowName"] = last_valid_window_entry.get("WindowName", "")
            if not entry.get("ProcessName") and last_valid_window_entry.get("ProcessName"):
                entry["ProcessName"] = last_valid_window_entry["ProcessName"]
            if not entry.get("WindowCommandLine") and last_valid_window_entry.get("WindowCommandLine"):
                entry["WindowCommandLine"] = last_valid_window_entry["WindowCommandLine"]
            win_name = str(entry.get("WindowName") or "").strip()

        has_identity = has_explicit_app_identity(entry)
        profile = detect_app_profile(entry) if has_identity else last_active_profile

        if "office" in r_lower:
            content = entry.get("Content") if isinstance(entry.get("Content"), dict) else {}
            office_info = content.get("office_info") if isinstance(content, dict) else {}
            msg = str(office_info.get("message", "")) if isinstance(office_info, dict) else ""
            if (isinstance(office_info, dict) and office_info.get("event_type") == "system") or "[System]" in msg:
                continue

            # Why: フォーカス喪失によるOnSheetChange(cell_change)のみ直近のExcel入力元ウィンドウへ帰属させて救出
            if not get_app_log_policy(profile, source=entry).use_office_com:
                is_cell_change = (
                    isinstance(office_info, dict) and office_info.get("event_type") == "cell_change"
                ) or "入力確定" in msg
                recent_excel = _find_recent_office_source(routed) if is_cell_change else None
                if recent_excel is not None:
                    entry["WindowName"] = recent_excel.get("WindowName", "")
                    entry["ProcessName"] = recent_excel.get("ProcessName", "")
                    entry["WindowCommandLine"] = recent_excel.get("WindowCommandLine", "")
                    profile = AppProfile.OFFICE
                    has_identity = True
                else:
                    continue

        if has_identity and profile != AppProfile.SYSTEM:
            last_active_profile = profile
            if win_name:
                last_valid_window_entry = entry

        raw_ctx = entry.get("AppSpecificContext") if isinstance(entry.get("AppSpecificContext"), dict) else None
        sanitized_ctx = sanitize_context_for_profile(profile, raw_ctx, source_info=entry)

        images = entry.get("Images") if isinstance(entry.get("Images"), dict) else {}
        has_crop = bool(images.get("Crop") and images.get("Crop") != "切り抜き失敗")
        primary_channel, active_channels = resolve_log_channels(
            raw_type=raw_type,
            app_profile=profile,
            app_context=sanitized_ctx,
            has_crop_image=has_crop,
            source=entry,
        )

        # Why: アクティブなアプリで許可されていないログチャネル（ブラウザ中のoffice_com、Excel中のuia_scan等）を完全遮断
        if not is_channel_allowed_for_profile(primary_channel, profile, source=entry):
            continue

        entry["AppProfile"] = profile.value
        entry["LogChannel"] = primary_channel.value
        entry["ActiveChannels"] = active_channels
        if sanitized_ctx is not None or "AppSpecificContext" in entry:
            entry["AppSpecificContext"] = sanitized_ctx

        routed.append(entry)

    return routed


def get_event_app_profile(info: Dict[str, Any]) -> AppProfile:
    """統合イベント辞書(workflow_info)からAppProfileを取得する。"""
    return detect_app_profile(info)
