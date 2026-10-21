"""Advanced mission impact and recovery planning.

All functions are deterministic advisory analysis. They never execute mission work,
change persisted state, run generated code, or trigger retries.
"""
from __future__ import annotations

from typing import Any, Mapping, Sequence

from mcp_and_crews.mission_engine import analyze_mission_dag, resolve_mission_frontier

_MAX_RETRIES = 10
_MAX_ATTEMPT_COUNT = 100_000
_ALLOWED_ERROR_CLASSES = {"transient", "rate_limit", "permanent", "unknown"}


def _require_sequence(value: Any, label: str) -> Sequence[Any]:
    if not isinstance(value, (list, tuple)):
        raise ValueError(f"{label} must be a list")
    return value


def analyze_change_impact(
    steps: Sequence[Mapping[str, Any]],
    changed_node_ids: Sequence[str],
    completed_node_ids: Sequence[str] | None = None,
) -> dict[str, Any]:
    """Calculate the downstream blast radius of changed nodes in a mission DAG.

    A changed node and every transitive dependent are marked impacted. Prerequisites
    upstream of a change are treated as unchanged. The returned waves describe a
    rerun plan only; they do not schedule or execute work.
    """
    _require_sequence(steps, "steps")
    changed = list(_require_sequence(changed_node_ids, "changed_node_ids"))
    if len(changed) != len(set(changed)):
        raise ValueError("changed_node_ids must not contain duplicates")
    if any(not isinstance(item, str) for item in changed):
        raise ValueError("changed_node_ids must contain strings")

    schedule = analyze_mission_dag(steps)
    by_id = {item["id"]: item for item in steps}
    known = set(by_id)
    missing = sorted(set(changed) - known)
    if missing:
        raise ValueError("Unknown changed node ids: " + ", ".join(missing))

    completed = list(_require_sequence(completed_node_ids or [], "completed_node_ids"))
    if len(completed) != len(set(completed)):
        raise ValueError("completed_node_ids must not contain duplicates")
    if any(not isinstance(item, str) for item in completed):
        raise ValueError("completed_node_ids must contain strings")
    unknown_completed = sorted(set(completed) - known)
    if unknown_completed:
        raise ValueError("Unknown completed node ids: " + ", ".join(unknown_completed))

    dependents: dict[str, list[str]] = {node_id: [] for node_id in by_id}
    for node_id, node in by_id.items():
        for dependency in node.get("dependencies", []):
            dependents[dependency].append(node_id)

    affected_by: dict[str, list[str]] = {node_id: [] for node_id in by_id}
    for source in changed:
        visited: set[str] = set()
        stack = [source]
        while stack:
            current = stack.pop()
            if current in visited:
                continue
            visited.add(current)
            affected_by[current].append(source)
            stack.extend(reversed(dependents[current]))

    impacted_set = {node_id for node_id, sources in affected_by.items() if sources}
    impacted = [node_id for node_id in schedule["topological_order"] if node_id in impacted_set]
    unaffected = [node_id for node_id in schedule["topological_order"] if node_id not in impacted_set]
    rerun_steps = [
        {
            "id": node_id,
            "name": by_id[node_id].get("name", node_id),
            "dependencies": [
                dependency for dependency in by_id[node_id].get("dependencies", [])
                if dependency in impacted_set
            ],
        }
        for node_id in impacted
    ]
    rerun_schedule = analyze_mission_dag(rerun_steps) if rerun_steps else {
        "waves": [], "critical_path": [], "critical_path_length": 0, "max_parallelism": 0
    }

    critical_path = set(schedule["critical_path"])
    changed_on_critical_path = [node_id for node_id in changed if node_id in critical_path]
    critical_path_intersects = bool(critical_path.intersection(impacted_set))
    total = schedule["node_count"]
    blast_radius = round((len(impacted) / total) * 100, 1) if total else 0.0
    if changed_on_critical_path:
        severity = "critical"
    elif critical_path_intersects or blast_radius >= 50:
        severity = "high"
    elif blast_radius >= 25:
        severity = "medium"
    else:
        severity = "low"

    completed_set = set(completed)
    invalidated = [node_id for node_id in impacted if node_id in completed_set]
    impact_sources = {
        node_id: sorted(affected_by[node_id])
        for node_id in impacted
    }
    recommendations: list[str] = []
    if not changed:
        recommendations.append("No changed nodes were supplied; no rerun is required by this analysis.")
    else:
        recommendations.append("Review the changed node(s), then rerun impacted nodes in the returned waves.")
        if invalidated:
            recommendations.append("Invalidate or regenerate completed outputs for impacted nodes before accepting results.")
        if critical_path_intersects:
            recommendations.append("The impact reaches the critical path; prioritize regression coverage for that path.")
        if len(unaffected) > 0:
            recommendations.append("Unaffected nodes can be retained only if their inputs and external state have not changed.")

    return {
        "schema_version": 1,
        "analysis": "downstream_dependency_blast_radius",
        "node_count": total,
        "changed_nodes": changed,
        "impacted_nodes": impacted,
        "unaffected_nodes": unaffected,
        "impact_sources": impact_sources,
        "impacted_count": len(impacted),
        "blast_radius_percent": blast_radius,
        "severity": severity,
        "critical_path": schedule["critical_path"],
        "critical_path_intersects": critical_path_intersects,
        "changed_nodes_on_critical_path": changed_on_critical_path,
        "completed_outputs_to_invalidate": invalidated,
        "rerun_waves": rerun_schedule["waves"],
        "rerun_critical_path": rerun_schedule["critical_path"],
        "rerun_critical_path_length": rerun_schedule["critical_path_length"],
        "rerun_max_parallelism": rerun_schedule["max_parallelism"],
        "recommendations": recommendations,
        "policy": {
            "execution_performed": False,
            "assumption": "Upstream prerequisites are unchanged; verify inputs and external state before reusing them.",
            "interpretation": "Dependency impact estimate, not proof that unaffected artifacts remain correct.",
        },
    }


def plan_mission_recovery(
    nodes: Sequence[Mapping[str, Any]],
    max_retries: int = 2,
    base_delay_seconds: int = 5,
    max_delay_seconds: int = 300,
) -> dict[str, Any]:
    """Produce a safety-aware, non-executing recovery plan for failed mission nodes.

    A retry is recommended only for explicitly idempotent nodes with a transient or
    rate-limit failure and remaining retry budget. Everything else requires review or
    escalation; no retry is performed by this function.
    """
    _require_sequence(nodes, "nodes")
    if isinstance(max_retries, bool) or not isinstance(max_retries, int) or not 0 <= max_retries <= _MAX_RETRIES:
        raise ValueError(f"max_retries must be an integer from 0 to {_MAX_RETRIES}")
    if isinstance(base_delay_seconds, bool) or not isinstance(base_delay_seconds, int) or not 1 <= base_delay_seconds <= 3600:
        raise ValueError("base_delay_seconds must be an integer from 1 to 3600")
    if isinstance(max_delay_seconds, bool) or not isinstance(max_delay_seconds, int) or not base_delay_seconds <= max_delay_seconds <= 86400:
        raise ValueError("max_delay_seconds must be between base_delay_seconds and 86400")

    frontier = resolve_mission_frontier(nodes)
    normalized = {node["id"]: node for node in nodes}
    failed_nodes: list[dict[str, Any]] = []
    has_review = False
    has_retry = False

    for node_id in frontier["failed"]:
        node = normalized[node_id]
        attempts = node.get("attempt_count", 1)
        if isinstance(attempts, bool) or not isinstance(attempts, int) or not 1 <= attempts <= _MAX_ATTEMPT_COUNT:
            raise ValueError(f"Node {node_id!r} attempt_count must be an integer from 1 to {_MAX_ATTEMPT_COUNT}")
        error_class = node.get("error_class", "unknown")
        if not isinstance(error_class, str) or error_class not in _ALLOWED_ERROR_CLASSES:
            raise ValueError(f"Node {node_id!r} error_class must be one of: " + ", ".join(sorted(_ALLOWED_ERROR_CLASSES)))
        idempotent = node.get("idempotent", False)
        if not isinstance(idempotent, bool):
            raise ValueError(f"Node {node_id!r} idempotent must be a boolean")

        retries_used = attempts - 1
        retries_remaining = max(0, max_retries - retries_used)
        delay = min(base_delay_seconds * (2 ** min(attempts - 1, 30)), max_delay_seconds)
        if retries_remaining == 0:
            action = "escalate_retry_budget_exhausted"
            reason = "The configured retry budget has been exhausted."
            requires_review = True
        elif error_class == "permanent":
            action = "do_not_retry_permanent_failure"
            reason = "The failure is classified as permanent; investigate the cause before another attempt."
            requires_review = True
        elif error_class == "unknown":
            action = "manual_triage_required"
            reason = "The failure class is unknown; classify it before deciding whether a retry is safe."
            requires_review = True
        elif not idempotent:
            action = "manual_review_non_idempotent"
            reason = "The node is not explicitly marked idempotent; retry could repeat side effects."
            requires_review = True
        else:
            action = "retry_candidate_after_backoff"
            reason = "Transient/rate-limit failure, explicit idempotency, and retry budget permit a retry recommendation."
            requires_review = False
        has_review = has_review or requires_review
        has_retry = has_retry or action == "retry_candidate_after_backoff"
        failed_nodes.append({
            "id": node_id,
            "attempt_count": attempts,
            "retries_used": retries_used,
            "retries_remaining": retries_remaining,
            "error_class": error_class,
            "idempotent": idempotent,
            "action": action,
            "suggested_delay_seconds": delay if action == "retry_candidate_after_backoff" else None,
            "requires_human_review": requires_review,
            "reason": reason,
        })

    blocked_downstream = []
    for item in frontier["blocked"]:
        blocked_downstream.append({
            "id": item["id"],
            "blocking_dependencies": item["blocking_dependencies"],
            "reason": item["reason"],
        })

    if not frontier["failed"]:
        recommendation = "no_failed_nodes"
    elif has_review:
        recommendation = "human_review_or_escalation_required"
    elif has_retry:
        recommendation = "retry_candidates_ready_for_operator_approval"
    else:
        recommendation = "investigate_failures"

    return {
        "schema_version": 1,
        "node_count": frontier["node_count"],
        "failed_count": len(failed_nodes),
        "retry_candidate_count": sum(item["action"] == "retry_candidate_after_backoff" for item in failed_nodes),
        "human_review_count": sum(item["requires_human_review"] for item in failed_nodes),
        "failed_nodes": failed_nodes,
        "blocked_downstream": blocked_downstream,
        "frontier_recommendation": frontier["recommendation"],
        "recommendation": recommendation,
        "policy": {
            "execution_performed": False,
            "automatic_retry_enabled": False,
            "retry_requires": ["transient_or_rate_limit_error", "explicit_idempotent_true", "remaining_retry_budget"],
            "backoff": "deterministic_exponential_capped",
            "interpretation": "Advisory recovery plan only; classify errors accurately and review side effects before acting.",
        },
    }
