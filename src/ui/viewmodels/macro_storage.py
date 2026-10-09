"""Module: @role: マクロ保存先ディレクトリの走査、要約リスト生成、コマンドJSON読み書きおよび削除処理を担当するストレージ層。"""

from datetime import datetime
from pathlib import Path
from typing import Any, Dict, List, Tuple
import json
import logging
import re
import shutil

from models.data_types import MacroSummary

logger = logging.getLogger(__name__)


def get_macros_root_path() -> Path:
    try:
        from core.recorder.screen_capturer import get_macros_root
        return get_macros_root()
    except Exception as e:
        logger.warning(f"Failed to call get_macros_root, falling back to relative path: {e}")
        return Path(__file__).resolve().parent / "../../../macros"


def natural_sort_key(s: str) -> List[Any]:
    return [int(t) if t.isdigit() else t.lower() for t in re.split(r"(\d+)", str(s))]


def list_macro_summaries() -> Tuple[List[MacroSummary], Dict[str, str]]:
    macros_root = get_macros_root_path()
    macros: List[MacroSummary] = []
    macro_id_map: Dict[str, str] = {}

    if not macros_root.exists() or not macros_root.is_dir():
        return macros, macro_id_map

    macro_dirs = [d for d in macros_root.glob("wf_*") if d.is_dir()]
    macro_dirs.sort(key=lambda d: natural_sort_key(d.name))

    for wf_dir in macro_dirs:
        workflow_id = wf_dir.name
        # 非ITユーザーが直感的に識別できるよう「マクロ 1」形式で表記
        if workflow_id.startswith("wf_") and workflow_id[3:].isdigit():
            macro_name = f"マクロ {workflow_id[3:]}"
        else:
            macro_name = f"マクロ {workflow_id}"

        if macro_name in macro_id_map:
            macro_name = f"マクロ {workflow_id}"

        stat = wf_dir.stat()
        created_ts = getattr(stat, "st_birthtime", stat.st_ctime)
        updated_ts = stat.st_mtime

        # フォルダ内重要ファイルの最新更新日時を反映
        for f_name in ("executable_macro.json", "workflow.json", "integrated.json"):
            target_file = wf_dir / f_name
            if target_file.exists():
                try:
                    f_mtime = target_file.stat().st_mtime
                    if f_mtime > updated_ts:
                        updated_ts = f_mtime
                except OSError:
                    pass

        created_at_str = datetime.fromtimestamp(created_ts).strftime("%Y/%m/%d %H:%M")
        updated_at_str = datetime.fromtimestamp(updated_ts).strftime("%Y/%m/%d %H:%M")

        macro_id_map[macro_name] = workflow_id
        macros.append(MacroSummary(
            name=macro_name,
            status="success",
            status_text="待機中",
            heals="0回",
            heal_level="none",
            last_run="-",
            created_at=created_at_str,
            updated_at=updated_at_str,
            created_timestamp=created_ts,
            updated_timestamp=updated_ts
        ))

    # 初期一覧は直近で作業したものが上位に来る更新順降順
    macros.sort(key=lambda m: m.updated_timestamp, reverse=True)
    return macros, macro_id_map


def load_executable_commands(workflow_id: str) -> List[Dict[str, Any]]:
    macros_root = get_macros_root_path()
    macro_file = macros_root / workflow_id / "executable_macro.json"

    if not macro_file.exists():
        return []

    with open(macro_file, "r", encoding="utf-8") as f:
        data = json.load(f)
        return data.get("commands", [])


def save_executable_commands(workflow_id: str, commands: List[Dict[str, Any]]) -> None:
    macros_root = get_macros_root_path()
    macro_file = macros_root / workflow_id / "executable_macro.json"

    if macro_file.exists():
        with open(macro_file, "r", encoding="utf-8") as f:
            data = json.load(f)
    else:
        data = {"macro_id": workflow_id, "target_application": "auto_generated"}

    data["commands"] = commands

    with open(macro_file, "w", encoding="utf-8") as f:
        json.dump(data, f, indent=2, ensure_ascii=False)


def delete_macro_folders(workflow_ids: List[str]) -> int:
    macros_root = get_macros_root_path()
    deleted_count = 0

    for workflow_id in workflow_ids:
        try:
            target_dir = macros_root / workflow_id
            if target_dir.exists() and target_dir.is_dir():
                shutil.rmtree(target_dir)
                deleted_count += 1
                logger.info(f"Physically deleted macro directory tree: {target_dir}")
        except Exception as e:
            logger.error(f"Failed to delete macro workflow {workflow_id}: {e}")

    return deleted_count
