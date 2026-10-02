# Role: マクロ録画セッションにおける作業ディレクトリ（temp/images）の作成・パス解決および相対パス変換を担当する。

from pathlib import Path

# =========================
# 状態管理
# =========================

_current_macro_dir: Path | None = None
_temp_dir: Path | None = None
_images_dir: Path | None = None


# =========================
# パス管理・解決
# =========================

def get_macros_root() -> Path:
    """プロジェクト直下の macros ディレクトリの絶対パスを返す"""
    return (Path(__file__).resolve().parent / "../../../macros").resolve()


def make_directory() -> dict:
    """新規マクロ録画用のディレクトリ（wf_X）とサブディレクトリ（temp, images）を生成する"""
    global _current_macro_dir
    global _temp_dir
    global _images_dir

    macros_root = get_macros_root()
    macros_root.mkdir(parents=True, exist_ok=True)

    index = 1
    while True:
        macro_dir = macros_root / f"wf_{index}"
        if not macro_dir.exists():
            break
        index += 1

    temp_dir = macro_dir / "temp"
    images_dir = macro_dir / "images"

    temp_dir.mkdir(parents=True, exist_ok=False)
    images_dir.mkdir(parents=True, exist_ok=False)

    _current_macro_dir = macro_dir
    _temp_dir = temp_dir
    _images_dir = images_dir

    return {
        "macro_name": macro_dir.name,
        "macro_dir": str(macro_dir),
        "temp_dir": str(temp_dir),
        "images_dir": str(images_dir),
    }


def get_current_macro_dir() -> Path:
    if _current_macro_dir is None:
        raise RuntimeError("macro_dir が未作成です。start_recording() を先に呼んでください。")
    return _current_macro_dir


def get_temp_dir() -> Path:
    if _temp_dir is None:
        raise RuntimeError("temp_dir が未作成です。start_recording() を先に呼んでください。")
    return _temp_dir


def get_images_dir() -> Path:
    if _images_dir is None:
        raise RuntimeError("images_dir が未作成です。start_recording() を先に呼んでください。")
    return _images_dir


def to_macro_relative_path(path: Path) -> str:
    """JSON保存用に macros フォルダ基準の相対パスに変換する"""
    macros_root = get_macros_root()
    try:
        relative = path.resolve().relative_to(macros_root.resolve())
        return relative.as_posix()
    except Exception:
        return path.name
