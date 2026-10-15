"""Module: @role: 複数のワークフロー候補を静的ルールおよびLLM評価器(Judge)により検証し、最高品質のマクロ候補を採択する品質ゲート管理。"""

import json
import logging
import re
from copy import deepcopy
from enum import Enum
from typing import List, Dict, Any, Optional, Callable, Tuple
from pydantic import BaseModel, Field

from models.data_types import AppConfig, QualityGateJudgment
from core.generator.workflow_optimizer import optimize_workflow_events

logger = logging.getLogger(__name__)

class OptimizationStrategy(str, Enum):
    HYBRID = "hybrid"
    SEMANTIC = "semantic"
    VISUAL = "visual"

class WorkflowCandidate(BaseModel):
    strategy: str
    events: List[Dict[str, Any]]
    variables: Dict[str, str] = Field(default_factory=dict)
    static_score: float = 0.0
    llm_score: float = 0.0
    total_score: float = 0.0
    judgment: Optional[QualityGateJudgment] = None

class QualityGateEvaluator:
    """マクロ候補の静的構造検証およびLLMによる意図整合性判定を担当する評価器"""

    def generate_and_select_best_candidate(
        self,
        temp_workflow_info: List[Dict[str, Any]],
        workflow_id: str,
        config: Optional[AppConfig] = None,
        check_cancel_callback: Optional[Callable[[], bool]] = None,
        progress_callback: Optional[Callable[[int, str], None]] = None
    ) -> WorkflowCandidate:
        strategies = [
            OptimizationStrategy.HYBRID,
            OptimizationStrategy.SEMANTIC,
            OptimizationStrategy.VISUAL
        ]
        candidates: List[WorkflowCandidate] = []

        for strat in strategies:
            if check_cancel_callback and check_cancel_callback():
                raise InterruptedError("Generation cancelled by user")

            try:
                # Why: 最適化処理による破壊的変更を防ぐため元イベント列をディープコピーして分離
                copied_events = deepcopy(temp_workflow_info)
                events, variables = optimize_workflow_events(
                    copied_events,
                    workflow_id=workflow_id,
                    check_cancel_callback=check_cancel_callback,
                    config=config,
                    strategy=strat.value
                )
                candidate = WorkflowCandidate(
                    strategy=strat.value,
                    events=events,
                    variables=variables
                )
                self._evaluate_candidate(candidate, config)
                candidates.append(candidate)
            except Exception as e:
                logger.warning(f"[{workflow_id}] Candidate generation failed for {strat.value}: {e}")

        # Why: 全戦略失敗時も生成を破綻させず元イベント列をフォールバック候補として評価
        if not candidates:
            fallback_score, _ = self._evaluate_static_rules(temp_workflow_info)
            fallback = WorkflowCandidate(
                strategy="raw_fallback",
                events=temp_workflow_info,
                variables={},
                total_score=fallback_score,
                judgment=QualityGateJudgment(
                    status="FAIL",
                    reason="All optimization strategies failed; raw fallback is not approved for execution",
                    confidence=0.0
                )
            )
            return fallback

        # A passing candidate must always outrank a failing candidate, regardless of score.
        candidates.sort(key=lambda c: (1 if c.judgment and c.judgment.status == "PASS" else 0, c.total_score), reverse=True)
        best = candidates[0]
        logger.info(f"[{workflow_id}] Quality Gate adopted '{best.strategy}' (Score: {best.total_score:.1f})")
        return best

    def _evaluate_candidate(self, candidate: WorkflowCandidate, config: Optional[AppConfig]) -> None:
        static_score, static_issues = self._evaluate_static_rules(candidate.events)
        candidate.static_score = static_score

        llm_score, llm_judgment = self._evaluate_with_llm(candidate, config)
        candidate.llm_score = llm_score

        if llm_judgment:
            candidate.total_score = round(static_score * 0.5 + llm_score * 0.5, 2)
            candidate.judgment = llm_judgment
        else:
            candidate.total_score = static_score
            is_pass = static_score >= 60.0 and len(candidate.events) > 0
            reason = "Static validation passed" if is_pass else f"Static issues: {', '.join(static_issues) if static_issues else 'Low score'}"
            candidate.judgment = QualityGateJudgment(
                status="PASS" if is_pass else "FAIL",
                reason=reason,
                confidence=min(1.0, max(0.1, static_score / 100.0))
            )

    def _evaluate_static_rules(self, events: List[Dict[str, Any]]) -> Tuple[float, List[str]]:
        score = 100.0
        issues: List[str] = []

        if not events:
            return 0.0, ["No events in candidate"]

        # 0. イベント保持率の検証（重要操作の欠落防止）
        business_ops = [e for e in events if e.get("raw_action") in ["click", "drag", "type_text", "browser_action", "excel_action"]]
        if not business_ops:
            score -= 40.0
            issues.append("No business operations present in workflow")

        has_submit = False
        submit_idx = -1
        form_input_after_submit = False

        for idx, evt in enumerate(events):
            act = evt.get("raw_action", "")
            sel = str(evt.get("selector", "")).lower()

            # 1. 送信ボタンと入力フィールドの順序不整合チェック
            if act == "browser_action" and evt.get("action") == "click_element" and any(k in sel for k in ["submit", "btn"]):
                has_submit = True
                submit_idx = idx
            elif has_submit and act in ["type_text", "browser_action"] and evt.get("action") in ["type_text", "select_option"]:
                form_input_after_submit = True

            # 2. 空テキストタイピングの検知
            if act == "type_text" and not str(evt.get("text", "")).strip() and not str(evt.get("semantic_role", "")).strip():
                score -= 15.0
                issues.append(f"Empty typing at step {idx}")

            # 3. 画面外無効座標の検知
            cx, cy = evt.get("cursor_x", evt.get("x", 0)), evt.get("cursor_y", evt.get("y", 0))
            if act in ["click", "move"] and (cx < -50 or cy < -50):
                score -= 10.0
                issues.append(f"Offscreen coordinates at step {idx}")

            # 4. セレクタ欠損のブラウザアクション検知
            if act == "browser_action" and evt.get("action") in ["click_element", "type_text", "select_option"]:
                if not evt.get("selector"):
                    score -= 15.0
                    issues.append(f"Missing selector at step {idx}")

        if form_input_after_submit:
            # Why: フォーム送信後に追加入力が走る順序破綻を重大欠陥として大幅減点
            score -= 35.0
            issues.append("Form input occurs after submit action")

        return max(0.0, score), issues

    def _evaluate_with_llm(self, candidate: WorkflowCandidate, config: Optional[AppConfig]) -> Tuple[float, Optional[QualityGateJudgment]]:
        if not config:
            return 0.0, None

        # Why: LLM未構成環境やオフライン環境では静的ルール評価へ安全にフォールバック
        host = config.llm_host or "127.0.0.1"
        port = int(config.llm_port or 8844)
        
        try:
            from engines.llm.client import LLMClient
            client = LLMClient(host=host, port=port, timeout=4, max_retries=1)

            summary_lines = []
            for i, e in enumerate(candidate.events[:15]):
                act = e.get("raw_action") or e.get("action") or "unknown"
                target = e.get("element_name") or e.get("selector") or e.get("semantic_role") or ""
                summary_lines.append(f"Step {i + 1}: {act} -> {target}")
            summary_text = "\n".join(summary_lines)

            prompt = (
                f"You are a strict QA Judge evaluating an RPA macro workflow candidate.\n"
                f"Strategy: {candidate.strategy}\n"
                f"Steps:\n{summary_text}\n\n"
                f"Evaluate if the action sequence is coherent, executable, and free of redundant noise.\n"
                f"Respond in valid JSON format ONLY:\n"
                f"{{\"status\": \"PASS\" or \"FAIL\", \"confidence\": 0.0 to 1.0, \"score\": 0 to 100, \"reason\": \"brief justification\"}}"
            )
            res = client.generate(prompt)
            if res.get("success") and "response" in res:
                content = res["response"]["choices"][0]["message"]["content"].strip()
                match = re.search(r'\{.*\}', content, re.DOTALL)
                if match:
                    parsed = json.loads(match.group(0))
                    status = "PASS" if str(parsed.get("status", "")).upper() == "PASS" else "FAIL"
                    conf = float(parsed.get("confidence", 0.8))
                    score = float(parsed.get("score", 70.0 if status == "PASS" else 40.0))
                    reason = str(parsed.get("reason", "LLM evaluation completed"))
                    return score, QualityGateJudgment(status=status, confidence=conf, reason=reason)
        except Exception as e:
            logger.debug(f"LLM quality gate evaluation skipped: {e}")

        return 0.0, None
