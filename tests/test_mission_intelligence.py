"""Regression tests for downstream impact analysis and safe recovery advice."""

from __future__ import annotations

import pytest

from mcp_and_crews.mission_intelligence import analyze_change_impact, plan_mission_recovery


def test_change_impact_marks_transitive_dependents_and_invalidates_completed_outputs() -> None:
    steps = [
        {"id": "input", "name": "Input", "dependencies": []},
        {"id": "api", "name": "API", "dependencies": ["input"]},
        {"id": "ui", "name": "UI", "dependencies": ["input"]},
        {"id": "release", "name": "Release", "dependencies": ["api", "ui"]},
        {"id": "docs", "name": "Docs", "dependencies": []},
    ]

    result = analyze_change_impact(
        steps,
        changed_node_ids=["input"],
        completed_node_ids=["api", "release", "docs"],
    )

    assert result["changed_nodes"] == ["input"]
    assert result["impacted_nodes"] == ["input", "api", "ui", "release"]
    assert result["unaffected_nodes"] == ["docs"]
    assert result["completed_outputs_to_invalidate"] == ["api", "release"]
    assert result["rerun_waves"] == [["input"], ["api", "ui"], ["release"]]
    assert result["critical_path_intersects"] is True
    assert result["severity"] == "critical"
    assert result["policy"]["execution_performed"] is False


def test_change_impact_on_leaf_has_small_blast_radius() -> None:
    result = analyze_change_impact(
        [
            {"id": "root", "dependencies": []},
            {"id": "middle", "dependencies": ["root"]},
            {"id": "leaf", "dependencies": ["middle"]},
            {"id": "independent", "dependencies": []},
        ],
        ["independent"],
    )

    assert result["impacted_nodes"] == ["independent"]
    assert result["unaffected_nodes"] == ["root", "middle", "leaf"]
    assert result["blast_radius_percent"] == 25.0
    assert result["severity"] == "medium"
    assert result["rerun_waves"] == [["independent"]]


def test_change_impact_rejects_unknown_and_duplicate_node_ids() -> None:
    steps = [{"id": "known", "dependencies": []}]
    with pytest.raises(ValueError, match="Unknown changed"):
        analyze_change_impact(steps, ["missing"])
    with pytest.raises(ValueError, match="duplicates"):
        analyze_change_impact(steps, ["known", "known"])
    with pytest.raises(ValueError, match="strings"):
        analyze_change_impact(steps, [1])


def test_change_impact_rejects_invalid_completed_node_references() -> None:
    with pytest.raises(ValueError, match="Unknown completed"):
        analyze_change_impact([{"id": "known", "dependencies": []}], [], ["missing"])


def test_recovery_recommends_retry_only_for_transient_idempotent_failure() -> None:
    result = plan_mission_recovery([
        {
            "id": "fetch",
            "dependencies": [],
            "status": "failed",
            "attempt_count": 2,
            "error_class": "rate_limit",
            "idempotent": True,
        },
        {"id": "transform", "dependencies": ["fetch"], "status": "pending"},
    ])

    candidate = result["failed_nodes"][0]
    assert candidate["action"] == "retry_candidate_after_backoff"
    assert candidate["suggested_delay_seconds"] == 10
    assert candidate["retries_remaining"] == 1
    assert result["blocked_downstream"][0]["id"] == "transform"
    assert result["recommendation"] == "retry_candidates_ready_for_operator_approval"
    assert result["policy"]["automatic_retry_enabled"] is False


@pytest.mark.parametrize(
    ("node", "expected_action"),
    [
        ({"error_class": "transient", "idempotent": False}, "manual_review_non_idempotent"),
        ({"error_class": "permanent", "idempotent": True}, "do_not_retry_permanent_failure"),
        ({"error_class": "unknown", "idempotent": True}, "manual_triage_required"),
        ({"error_class": "transient", "idempotent": True, "attempt_count": 3}, "escalate_retry_budget_exhausted"),
    ],
)
def test_recovery_requires_review_for_unsafe_or_exhausted_retries(node: dict, expected_action: str) -> None:
    payload = {"id": "broken", "dependencies": [], "status": "failed", **node}
    result = plan_mission_recovery([payload], max_retries=2)
    assert result["failed_nodes"][0]["action"] == expected_action
    assert result["failed_nodes"][0]["requires_human_review"] is True
    assert result["recommendation"] == "human_review_or_escalation_required"


def test_recovery_empty_failures_and_retry_configuration_validation() -> None:
    result = plan_mission_recovery([{"id": "ok", "dependencies": [], "status": "completed"}])
    assert result["recommendation"] == "no_failed_nodes"
    assert result["failed_nodes"] == []
    with pytest.raises(ValueError, match="max_retries"):
        plan_mission_recovery([], max_retries=True)
    with pytest.raises(ValueError, match="max_delay_seconds"):
        plan_mission_recovery([], base_delay_seconds=10, max_delay_seconds=5)


def test_recovery_rejects_unknown_failure_class_and_invalid_attempt_count() -> None:
    with pytest.raises(ValueError, match="error_class"):
        plan_mission_recovery([{
            "id": "bad", "dependencies": [], "status": "failed", "error_class": "maybe"
        }])
    with pytest.raises(ValueError, match="attempt_count"):
        plan_mission_recovery([{
            "id": "bad", "dependencies": [], "status": "failed", "attempt_count": 0
        }])
