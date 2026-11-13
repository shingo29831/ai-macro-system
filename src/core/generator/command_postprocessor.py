"""Module: @role: 実行コマンド列への待機時間補完および連続スクロールコマンド集約を担当する。"""

from typing import Any, Dict, List, Optional


def append_wait_if_needed(
    raw_commands_data: List[Dict[str, Any]],
    current_timestamp: int,
    prev_timestamp: Optional[int],
) -> int:
    prev_step_cmd = raw_commands_data[-1].get("method") if raw_commands_data else ""
    if prev_step_cmd == "move":
        # Why: ホバー後のドロップダウンメニュー展開アニメーション時間を最低0.5秒保証
        calc_dur = max(0.5, min((current_timestamp - (prev_timestamp or current_timestamp)) / 1000.0, 1.5))
        raw_commands_data.append({"method": "wait", "args": {"duration": round(calc_dur, 3)}})
    elif prev_timestamp is not None:
        duration = (current_timestamp - prev_timestamp) / 1000.0
        if duration > 0.01:
            raw_commands_data.append({"method": "wait", "args": {"duration": round(min(duration, 1.5), 3)}})
    return current_timestamp


def merge_scroll_commands(raw_commands_data: List[Dict[str, Any]]) -> List[Dict[str, Any]]:
    commands_data: List[Dict[str, Any]] = []
    for cmd in raw_commands_data:
        if not commands_data or cmd["method"] != "scroll":
            commands_data.append(cmd)
            continue

        c_dx = cmd["args"].get("dx", 0.0)
        c_dy = cmd["args"].get("dy", 0.0)
        if c_dx == 0.0 and c_dy == 0.0:
            continue

        merged = False
        last_cmd = commands_data[-1]
        if last_cmd["method"] == "scroll":
            l_dx = last_cmd["args"].get("dx", 0.0)
            l_dy = last_cmd["args"].get("dy", 0.0)
            # Why: 同方向スクロールのみ集約し逆方向の意図的操作は相殺せず保護
            is_same_dir = (l_dy * c_dy >= 0) and (l_dx * c_dx >= 0)
            if (
                is_same_dir
                and abs(last_cmd["args"]["x"] - cmd["args"]["x"]) <= 200
                and abs(last_cmd["args"]["y"] - cmd["args"]["y"]) <= 300
            ):
                last_cmd["args"]["dx"] = round(l_dx + c_dx, 2)
                last_cmd["args"]["dy"] = round(l_dy + c_dy, 2)
                if not last_cmd["args"].get("raw_event_id") and cmd["args"].get("raw_event_id"):
                    last_cmd["args"]["raw_event_id"] = cmd["args"]["raw_event_id"]
                merged = True
        elif last_cmd["method"] == "wait" and len(commands_data) >= 2:
            prev_cmd = commands_data[-2]
            if prev_cmd["method"] == "scroll":
                p_dx = prev_cmd["args"].get("dx", 0.0)
                p_dy = prev_cmd["args"].get("dy", 0.0)
                # Why: スクロール間の短時間待機も挟んだ戻しスクロールを正味量へ集約
                if (
                    last_cmd["args"]["duration"] < 1.2
                    and abs(prev_cmd["args"]["x"] - cmd["args"]["x"]) <= 200
                    and abs(prev_cmd["args"]["y"] - cmd["args"]["y"]) <= 300
                ):
                    new_dx = round(p_dx + c_dx, 2)
                    new_dy = round(p_dy + c_dy, 2)
                    commands_data.pop()
                    if new_dx == 0.0 and new_dy == 0.0:
                        commands_data.pop()
                    else:
                        prev_cmd["args"]["dx"] = new_dx
                        prev_cmd["args"]["dy"] = new_dy
                        if not prev_cmd["args"].get("raw_event_id") and cmd["args"].get("raw_event_id"):
                            prev_cmd["args"]["raw_event_id"] = cmd["args"]["raw_event_id"]
                    merged = True

        if not merged:
            commands_data.append(cmd)
    return commands_data
