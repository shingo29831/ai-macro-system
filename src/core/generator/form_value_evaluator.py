"""Module: @role: Webフォーム要素のシステムUIノイズ判定およびLLM/ヒューリスティックによる最適入力値の評価・選定を担当する。"""

import logging
import re
from typing import Any, Dict, List, Optional

logger = logging.getLogger(__name__)


def is_system_ui_event(evt: Dict[str, Any]) -> bool:
    ctx = evt.get("app_context") or {}
    elem_name = str(ctx.get("element_name", "")).lower()
    elem_text = str(ctx.get("text", "")).lower()
    role = str(evt.get("semantic_role", "")).lower()
    css = str(ctx.get("css_selector", "")).lower()
    c_type = str(ctx.get("control_type", "")).lower()
    system_terms = [
        "記録を終了", "記録中", "停止中", "実行中", "ai macro system", "マクロ生成中",
        "qapplication.qwidget", "windows 入力エクスペリエンス", "textinputhost",
        "corewindow", "windows.ui.core"
    ]
    return any(
        term in elem_name or term in elem_text or term in role or term in css or term in c_type
        for term in system_terms
    )


def select_candidate_with_llm(candidates: List[str], elem_name: str, config: Optional[Any] = None) -> Optional[str]:
    # Why: 小型モデル(Gemma 2B)でも崩れない番号選択プロンプトで曖昧な候補値を高精度判定
    if not config or len(candidates) < 2:
        return None
    try:
        from engines.llm.client import LLMClient
        client = LLMClient(host=config.llm_host, port=int(config.llm_port), timeout=3, max_retries=1)
        options_text = "\n".join(f"{idx + 1}: {c}" for idx, c in enumerate(candidates))
        prompt = (
            f"You are a form field input analyzer.\n"
            f"Target input field: \"{elem_name}\"\n"
            f"Candidates:\n{options_text}\n\n"
            f"Select the single most complete, definitive value intended for submission.\n"
            f"Respond ONLY with the choice number (e.g., 1 or 2)."
        )
        res = client.generate(prompt)
        if res.get("success") and "response" in res:
            content = res["response"]["choices"][0]["message"]["content"].strip()
            match = re.search(r"\b([1-9])\b", content)
            if match:
                selected_idx = int(match.group(1)) - 1
                if 0 <= selected_idx < len(candidates):
                    logger.info(
                        f"LLM resolved field '{elem_name}' value to candidate [{selected_idx + 1}]: {candidates[selected_idx]}"
                    )
                    return candidates[selected_idx].strip()
    except Exception as e:
        logger.debug(f"LLM candidate evaluation skipped/failed: {e}")
    return None


def _score_candidate_value(val: str, idx: int, elem_name: str) -> float:
    if not val or not isinstance(val, str):
        return -1000.0
    v = val.strip()
    if not v or v.lower() == elem_name.lower() or v in ["検索", "Search", "クリア", "×", "left_click", "move"]:
        return -500.0
    # Why: 未確定ローマ字の末尾子音(kaisy, satou等)を大幅減点し確定値を救済
    is_raw_romaji = bool(
        re.fullmatch(r"[a-z]+", v.lower())
        and any(v.lower().endswith(c) for c in ["sy", "ty", "ky", "sh", "ch", "ou", "ei"])
    )
    if is_raw_romaji:
        return -100.0 + min(len(v), 10)
    # Why: 入力途中の1~2文字ひらがな単体より漢字・英数字混在や確定長文を自然に優先
    if re.fullmatch(r"[\u3040-\u309f]{1,2}", v):
        return -50.0
    score = 10.0 + idx * 5.0 + min(len(v), 15) * 4.0
    if re.search(r"[\u4e00-\u9fff]", v):
        score += 40.0
    if re.search(r"[A-Za-z0-9]", v):
        score += 20.0
    if v.isdigit():
        score += 30.0
    return score


def evaluate_form_value(
    candidates: List[str],
    elem_name: str,
    has_snapshot_val: Optional[str] = None,
    config: Optional[Any] = None,
) -> str:
    llm_val = select_candidate_with_llm(candidates, elem_name, config)
    if llm_val:
        return llm_val
    # Why: ブラウザ確定スナップショット値を絶対最優先し普遍判定で最適値を決定
    if has_snapshot_val and str(has_snapshot_val).strip():
        s_val = str(has_snapshot_val).strip()
        # Why: 候補に確定値の前方一致完全長文(例: 佐藤 -> 佐藤 健一)が存在する場合は完全版を採用
        for c in reversed(candidates):
            c_str = str(c).strip()
            if len(c_str) > len(s_val) and (c_str.startswith(s_val) or s_val in c_str):
                return c_str
        return s_val
    if not candidates:
        return ""

    best_val, best_score = "", -999.0
    for i, c in enumerate(candidates):
        s = _score_candidate_value(c, i, elem_name)
        if s > best_score:
            best_score, best_val = s, c.strip()
    return best_val if best_score > 0 else (candidates[-1].strip() if candidates else "")
