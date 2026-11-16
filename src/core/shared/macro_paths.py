"""Module: @role: マクロ保存ルートディレクトリ解決とmacros基準の相対パス変換を担当する。"""

from pathlib import Path


def get_macros_root() -> Path:
    """プロジェクト直下の macros ディレクトリの絶対パスを返す。"""
    return (Path(__file__).resolve().parent / "../../../macros").resolve()


def get_workflow_dir(workflow_id: str) -> Path:
    """指定ワークフローIDの作業ディレクトリを返す。"""
    return get_macros_root() / workflow_id


def to_macro_relative_path(path: Path) -> str:
    """JSON保存用に macros フォルダ基準の相対パスに変換する。"""
    macros_root = get_macros_root()
    try:
        relative = path.resolve().relative_to(macros_root.resolve())
        return relative.as_posix()
    except Exception:
        return path.name
