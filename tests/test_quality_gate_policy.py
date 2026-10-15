"""Regression tests for quality-gate acceptance policy."""

from core.generator import quality_gate


def test_passing_candidate_outranks_higher_scoring_failure(monkeypatch):
    evaluator = quality_gate.QualityGateEvaluator()

    def fake_optimize(events, workflow_id, check_cancel_callback=None, config=None, strategy="hybrid"):
        return ([{"raw_action": "click", "strategy": strategy}], {})

    def fake_evaluate(candidate, config):
        if candidate.strategy == "semantic":
            candidate.total_score = 60.0
            candidate.judgment = quality_gate.QualityGateJudgment(
                status="PASS", reason="approved", confidence=0.9
            )
        else:
            candidate.total_score = 99.0
            candidate.judgment = quality_gate.QualityGateJudgment(
                status="FAIL", reason="rejected", confidence=0.9
            )

    monkeypatch.setattr(quality_gate, "optimize_workflow_events", fake_optimize)
    monkeypatch.setattr(evaluator, "_evaluate_candidate", fake_evaluate)

    candidate = evaluator.generate_and_select_best_candidate(
        temp_workflow_info=[{"raw_action": "click"}],
        workflow_id="test-workflow",
    )

    assert candidate.strategy == "semantic"
    assert candidate.judgment.status == "PASS"


def test_raw_fallback_is_not_implicitly_approved(monkeypatch):
    evaluator = quality_gate.QualityGateEvaluator()

    def fail_optimization(*args, **kwargs):
        raise RuntimeError("optimization failed")

    monkeypatch.setattr(quality_gate, "optimize_workflow_events", fail_optimization)
    monkeypatch.setattr(evaluator, "_evaluate_static_rules", lambda events: (100.0, []))

    candidate = evaluator.generate_and_select_best_candidate(
        temp_workflow_info=[{"raw_action": "click"}],
        workflow_id="test-workflow",
    )

    assert candidate.strategy == "raw_fallback"
    assert candidate.judgment.status == "FAIL"
