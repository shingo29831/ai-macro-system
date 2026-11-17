"""Module: @role: アクティブアプリの種別判定と、アプリごとに使用する複数ログチャネルの切り替えポリシー定義を担当する。"""

from dataclasses import dataclass
from enum import Enum
from pathlib import PurePath
import shlex
from typing import Any, Dict, FrozenSet, List, Optional, Tuple

from core.shared.window_utils import extract_app_name, is_browser_window, is_system_shell_window


class AppProfile(str, Enum):
    """アプリケーション種別プロファイル"""

    OFFICE = "office"
    BROWSER = "browser"
    DESKTOP = "desktop"
    SYSTEM = "system"


class LogChannel(str, Enum):
    """マクロ記録・生成で扱うログソース（チャネル）種別"""

    OS_MOUSE = "os_mouse"
    OS_KEYBOARD = "os_keyboard"
    OFFICE_COM = "office_com"
    OFFICE_UIA = "office_uia"
    BROWSER_DOM = "browser_dom"
    UIA_SCAN = "uia_scan"
    GENERIC_UIA = "generic_uia"
    CV_VISION = "cv_vision"
    META = "meta"


@dataclass(frozen=True)
class AppLogPolicy:
    """アプリプロファイルごとに使用する複数ログチャネルと優先順位の定義"""

    profile: AppProfile
    allowed_channels: FrozenSet[LogChannel]
    priority_channels: Tuple[LogChannel, ...]
    use_office_com: bool = False
    use_form_snapshot: bool = False
    use_search_query_inference: bool = False


OFFICE_PROCESS_MAP: Dict[str, str] = {
    "excel.exe": "Excel",
    "winword.exe": "Word",
    "powerpnt.exe": "PowerPoint",
    "msaccess.exe": "Access",
    "outlook.exe": "Outlook",
    "soffice.bin": "Office",
    "soffice.exe": "Office",
}

OFFICE_APP_SUFFIX_MAP: Dict[str, str] = {
    "excel": "Excel",
    "microsoft excel": "Excel",
    "word": "Word",
    "microsoft word": "Word",
    "powerpoint": "PowerPoint",
    "microsoft powerpoint": "PowerPoint",
    "access": "Access",
    "microsoft access": "Access",
    "outlook": "Outlook",
    "microsoft outlook": "Outlook",
}

NON_OFFICE_APP_SUFFIXES: FrozenSet[str] = frozenset({
    "visual studio code",
    "code",
    "notepad",
    "メモ帳",
    "notepad++",
    "explorer",
    "エクスプローラー",
    "powershell",
    "windows powershell",
    "コマンド プロンプト",
    "cmd",
    "terminal",
    "windows terminal",
    "pycharm",
    "cursor",
})

BROWSER_PROCESS_NAMES: FrozenSet[str] = frozenset({
    "chrome.exe",
    "msedge.exe",
    "firefox.exe",
    "brave.exe",
    "opera.exe",
    "vivaldi.exe",
    "iexplore.exe",
})

OFFICE_EXCEL_LOG_POLICY = AppLogPolicy(
    profile=AppProfile.OFFICE,
    allowed_channels=frozenset({
        LogChannel.OFFICE_COM,
        LogChannel.OFFICE_UIA,
        LogChannel.OS_MOUSE,
        LogChannel.OS_KEYBOARD,
        LogChannel.CV_VISION,
        LogChannel.META,
    }),
    priority_channels=(
        LogChannel.OFFICE_COM,
        LogChannel.OFFICE_UIA,
        LogChannel.OS_KEYBOARD,
        LogChannel.OS_MOUSE,
        LogChannel.CV_VISION,
    ),
    use_office_com=True,
    use_form_snapshot=False,
    use_search_query_inference=False,
)

# Why: Word/PowerPoint/Outlook等の非Excel系OfficeではUIA_SCAN(Tab補完)を許可しExcel COM混入を遮断
OFFICE_GENERAL_LOG_POLICY = AppLogPolicy(
    profile=AppProfile.OFFICE,
    allowed_channels=frozenset({
        LogChannel.OFFICE_UIA,
        LogChannel.UIA_SCAN,
        LogChannel.OS_MOUSE,
        LogChannel.OS_KEYBOARD,
        LogChannel.CV_VISION,
        LogChannel.META,
    }),
    priority_channels=(
        LogChannel.OFFICE_UIA,
        LogChannel.UIA_SCAN,
        LogChannel.OS_KEYBOARD,
        LogChannel.OS_MOUSE,
        LogChannel.CV_VISION,
    ),
    use_office_com=False,
    use_form_snapshot=False,
    use_search_query_inference=False,
)

APP_LOG_POLICIES: Dict[AppProfile, AppLogPolicy] = {
    AppProfile.OFFICE: OFFICE_EXCEL_LOG_POLICY,
    AppProfile.BROWSER: AppLogPolicy(
        profile=AppProfile.BROWSER,
        allowed_channels=frozenset({
            LogChannel.BROWSER_DOM,
            LogChannel.UIA_SCAN,
            LogChannel.OS_MOUSE,
            LogChannel.OS_KEYBOARD,
            LogChannel.CV_VISION,
            LogChannel.META,
        }),
        priority_channels=(
            LogChannel.BROWSER_DOM,
            LogChannel.UIA_SCAN,
            LogChannel.OS_KEYBOARD,
            LogChannel.OS_MOUSE,
            LogChannel.CV_VISION,
        ),
        use_office_com=False,
        use_form_snapshot=True,
        use_search_query_inference=True,
    ),
    AppProfile.DESKTOP: AppLogPolicy(
        profile=AppProfile.DESKTOP,
        allowed_channels=frozenset({
            LogChannel.GENERIC_UIA,
            LogChannel.UIA_SCAN,
            LogChannel.OS_MOUSE,
            LogChannel.OS_KEYBOARD,
            LogChannel.CV_VISION,
            LogChannel.META,
        }),
        priority_channels=(
            LogChannel.GENERIC_UIA,
            LogChannel.UIA_SCAN,
            LogChannel.OS_KEYBOARD,
            LogChannel.OS_MOUSE,
            LogChannel.CV_VISION,
        ),
        use_office_com=False,
        use_form_snapshot=False,
        use_search_query_inference=False,
    ),
    AppProfile.SYSTEM: AppLogPolicy(
        profile=AppProfile.SYSTEM,
        allowed_channels=frozenset({LogChannel.META}),
        priority_channels=(LogChannel.META,),
        use_office_com=False,
        use_form_snapshot=False,
        use_search_query_inference=False,
    ),
}


def _extract_exe_basename(raw_path_or_cmd: str) -> str:
    """プロセス名またはコマンドライン文字列の先頭トークンから実行ファイル名(小文字)のみを抽出する。"""
    text = (raw_path_or_cmd or "").strip()
    if not text:
        return ""
    if text.startswith('"'):
        end_quote = text.find('"', 1)
        first_token = text[1:end_quote] if end_quote > 1 else text.strip('"')
    else:
        try:
            tokens = shlex.split(text, posix=False)
            first_token = tokens[0].strip('"') if tokens else text.split()[0]
        except ValueError:
            first_token = text.split()[0].strip('"')
    normalized = first_token.replace("\\", "/")
    return PurePath(normalized).name.lower()


def detect_office_sub_app(source: Optional[Dict[str, Any]]) -> str:
    """Office系アプリの詳細種別('Excel', 'Word', 'PowerPoint', 'Outlook', 'Access', '')を返す。"""
    if not isinstance(source, dict):
        return ""

    proc_exe = _extract_exe_basename(
        str(source.get("process_name") or source.get("ProcessName") or source.get("processName") or "")
    )
    if proc_exe in OFFICE_PROCESS_MAP:
        return OFFICE_PROCESS_MAP[proc_exe]
    if proc_exe:
        return ""

    cmd_exe = _extract_exe_basename(
        str(source.get("command_line") or source.get("WindowCommandLine") or "")
    )
    if cmd_exe in OFFICE_PROCESS_MAP:
        return OFFICE_PROCESS_MAP[cmd_exe]
    if cmd_exe:
        return ""

    title = str(
        source.get("title") or source.get("WindowName") or source.get("window_name") or ""
    ).strip()
    if title and not is_browser_window(title):
        app_suffix = extract_app_name(title).lower().strip()
        if app_suffix in NON_OFFICE_APP_SUFFIXES:
            return ""
        if app_suffix in OFFICE_APP_SUFFIX_MAP:
            return OFFICE_APP_SUFFIX_MAP[app_suffix]
        title_lower = title.lower()
        if any(title_lower.endswith(ext) for ext in (".xlsx", ".xlsm", ".xlsb", ".xls")):
            return "Excel"
        if any(title_lower.endswith(ext) for ext in (".docx", ".docm", ".doc")):
            return "Word"
        if any(title_lower.endswith(ext) for ext in (".pptx", ".pptm", ".ppt")):
            return "PowerPoint"

    app_ctx = (
        source.get("app_context")
        or source.get("AppSpecificContext")
        or source.get("appSpecificContext")
        or {}
    )
    if isinstance(app_ctx, dict):
        ctx_app = str(app_ctx.get("app", "")).strip()
        if ctx_app.lower() in OFFICE_APP_SUFFIX_MAP:
            return OFFICE_APP_SUFFIX_MAP[ctx_app.lower()]
        if ctx_app.lower() == "office":
            return "Office"

    return ""


def get_app_log_policy(
    profile: AppProfile | str,
    source: Optional[Dict[str, Any]] = None,
) -> AppLogPolicy:
    """指定されたAppProfile（およびOfficeサブ種別）に対するログ切り替えポリシーを返す。"""
    try:
        prof_enum = profile if isinstance(profile, AppProfile) else AppProfile(str(profile).lower())
    except ValueError:
        prof_enum = AppProfile.DESKTOP

    if prof_enum == AppProfile.OFFICE and isinstance(source, dict):
        sub_app = detect_office_sub_app(source)
        if sub_app in ("Word", "PowerPoint", "Outlook", "Access"):
            return OFFICE_GENERAL_LOG_POLICY

    return APP_LOG_POLICIES.get(prof_enum, APP_LOG_POLICIES[AppProfile.DESKTOP])


def has_explicit_app_identity(source: Optional[Dict[str, Any]]) -> bool:
    """イベント辞書にアプリ種別を特定可能な明示情報が含まれているかを判定する。"""
    if not isinstance(source, dict):
        return False
    if source.get("app_profile") or source.get("AppProfile") or source.get("appProfile"):
        return True
    if source.get("process_name") or source.get("ProcessName") or source.get("processName"):
        return True
    if source.get("command_line") or source.get("WindowCommandLine"):
        return True
    if (source.get("title") or source.get("WindowName") or source.get("window_name") or "").strip():
        return True
    app_ctx = (
        source.get("app_context")
        or source.get("AppSpecificContext")
        or source.get("appSpecificContext")
        or {}
    )
    if isinstance(app_ctx, dict) and app_ctx.get("app"):
        return True
    return False


def detect_app_profile(source: Optional[Dict[str, Any]]) -> AppProfile:
    """window_info、生ログエントリ、またはworkflow_info辞書からアクティブアプリのAppProfileを判定する。"""
    if not isinstance(source, dict):
        return AppProfile.DESKTOP

    explicit = source.get("app_profile") or source.get("AppProfile") or source.get("appProfile")
    if explicit:
        try:
            return AppProfile(str(explicit).lower())
        except ValueError:
            pass

    proc_exe = _extract_exe_basename(
        str(source.get("process_name") or source.get("ProcessName") or source.get("processName") or "")
    )
    cmd_exe = _extract_exe_basename(
        str(source.get("command_line") or source.get("WindowCommandLine") or "")
    )
    title = str(
        source.get("title") or source.get("WindowName") or source.get("window_name") or ""
    ).strip()

    # Why: 実行ファイル名(basename)が存在する場合はプロセス種別を絶対優先しタイトル部分一致誤爆を防止
    if proc_exe in BROWSER_PROCESS_NAMES or cmd_exe in BROWSER_PROCESS_NAMES:
        return AppProfile.BROWSER
    if proc_exe in OFFICE_PROCESS_MAP or cmd_exe in OFFICE_PROCESS_MAP:
        return AppProfile.OFFICE
    if proc_exe or cmd_exe:
        if title and is_system_shell_window(title, check_keywords_for_browser=False):
            return AppProfile.SYSTEM
        return AppProfile.DESKTOP

    app_ctx = (
        source.get("app_context")
        or source.get("AppSpecificContext")
        or source.get("appSpecificContext")
        or {}
    )
    ctx_app = str(app_ctx.get("app", "")).lower() if isinstance(app_ctx, dict) else ""

    if is_browser_window(title) or ctx_app == "browser":
        return AppProfile.BROWSER

    if detect_office_sub_app(source):
        return AppProfile.OFFICE

    if title and is_system_shell_window(title, check_keywords_for_browser=False):
        return AppProfile.SYSTEM

    return AppProfile.DESKTOP


def is_channel_allowed_for_profile(
    channel: LogChannel,
    app_profile: AppProfile,
    source: Optional[Dict[str, Any]] = None,
) -> bool:
    """指定されたログチャネルが当該アプリプロファイルで許可されているかを判定する。"""
    policy = get_app_log_policy(app_profile, source=source)
    return channel in policy.allowed_channels


def resolve_log_channels(
    raw_type: str,
    app_profile: AppProfile,
    app_context: Optional[Dict[str, Any]] = None,
    has_crop_image: bool = False,
    source: Optional[Dict[str, Any]] = None,
) -> Tuple[LogChannel, List[str]]:
    """イベント種別とアプリプロファイルから、主チャネル(LogChannel)と併用チャネル一覧(ActiveChannels)を解決する。"""
    policy = get_app_log_policy(app_profile, source=source)
    r_lower = str(raw_type or "").lower()
    ctx = app_context if isinstance(app_context, dict) else {}

    if any(k in r_lower for k in ("meta", "recording")):
        primary = LogChannel.META
    elif "office" in r_lower:
        primary = LogChannel.OFFICE_COM
    elif "uia" in r_lower:
        primary = LogChannel.UIA_SCAN
    elif any(k in r_lower for k in ("click", "move", "hover", "drag", "scroll", "mouse")):
        primary = LogChannel.OS_MOUSE
    elif any(k in r_lower for k in ("key", "type", "text_field", "text_candidate")):
        primary = LogChannel.OS_KEYBOARD
    else:
        primary = LogChannel.OS_MOUSE

    active: List[LogChannel] = []
    if primary in policy.allowed_channels:
        active.append(primary)

    # Why: アクティブなアプリで必要な複数種類の補助ログチャネルを優先度順に追加
    if ctx:
        if app_profile == AppProfile.BROWSER and LogChannel.BROWSER_DOM in policy.allowed_channels:
            if any(
                ctx.get(k)
                for k in (
                    "css_selector",
                    "xpath",
                    "url",
                    "page_url",
                    "form_snapshot",
                    "committed_values",
                    "element_name",
                    "value",
                    "text",
                )
            ):
                active.append(LogChannel.BROWSER_DOM)
        elif app_profile == AppProfile.OFFICE and LogChannel.OFFICE_UIA in policy.allowed_channels:
            if any(ctx.get(k) for k in ("element_name", "control_type", "value", "text")):
                active.append(LogChannel.OFFICE_UIA)
        elif app_profile == AppProfile.DESKTOP and LogChannel.GENERIC_UIA in policy.allowed_channels:
            if any(ctx.get(k) for k in ("element_name", "control_type", "value", "text")):
                active.append(LogChannel.GENERIC_UIA)

    if has_crop_image and LogChannel.CV_VISION in policy.allowed_channels and primary == LogChannel.OS_MOUSE:
        active.append(LogChannel.CV_VISION)

    deduped = [ch.value for ch in policy.priority_channels if ch in active]
    for ch in active:
        if ch.value not in deduped:
            deduped.append(ch.value)

    if not deduped:
        deduped.append(primary.value)

    return primary, deduped


def sanitize_context_for_profile(
    app_profile: AppProfile,
    app_context: Optional[Dict[str, Any]],
    source_info: Optional[Dict[str, Any]] = None,
) -> Optional[Dict[str, Any]]:
    """アクティブアプリのプロファイルに不要な異種アプリのコンテキスト属性を除去する。"""
    if not isinstance(app_context, dict) or not app_context:
        return app_context

    cleaned = dict(app_context)
    if app_profile == AppProfile.OFFICE:
        # Why: Office操作時にブラウザ用のDOMセレクタやフォームスナップショットが混入するのを防止
        for k in (
            "css_selector",
            "xpath",
            "form_snapshot",
            "committed_values",
            "committed_previous_value",
            "is_address_bar",
            "page_url",
            "url_details",
            "query",
        ):
            cleaned.pop(k, None)
        sub_app = detect_office_sub_app(source_info) or detect_office_sub_app({"app_context": cleaned})
        if cleaned.get("app") in (None, "", "GenericUIA", "Browser"):
            cleaned["app"] = sub_app or "Office"
    elif app_profile == AppProfile.BROWSER:
        cleaned["app"] = "Browser"
    elif app_profile == AppProfile.DESKTOP:
        for k in ("form_snapshot", "committed_values", "committed_previous_value", "is_address_bar"):
            cleaned.pop(k, None)

    return cleaned
