"""Deterministic mission DAG analysis and artifact provenance utilities.

The scheduler never executes work. It calculates a stable topological plan, dependency
waves, critical path, and theoretical unit-cost parallelism. The provenance ledger detects
missing or changed build outputs; it is not a cryptographic signature against an attacker
who can replace both files and the manifest.
"""

from __future__ import annotations

import hashlib
import json
import re
from pathlib import Path
from typing import Any, Mapping, Sequence

_MISSION_ID_RE = re.compile(r"^[A-Za-z0-9][A-Za-z0-9_-]{0,79}$")
_NODE_ID_RE = re.compile(r"^[a-z0-9][a-z0-9_-]{0,63}$")
_SHA256_RE = re.compile(r"^[0-9a-f]{64}$")
_MAX_NODES = 12
_MANIFEST_SCHEMA_VERSION = 1


def _canonical_json(payload: Mapping[str, Any]) -> bytes:
    return json.dumps(
        payload, sort_keys=True, separators=(",", ":"), ensure_ascii=False
    ).encode("utf-8")


def _safe_relative_file(root: Path, relative: str | Path) -> tuple[Path, str]:
    """Resolve a regular file under root while rejecting traversal and symlinks."""
    root_resolved = root.resolve()
    requested = Path(relative)
    if requested.is_absolute() or not requested.parts or any(part in {"", ".", ".."} for part in requested.parts):
        raise ValueError("Artifact paths must be non-empty, relative, and traversal-free")
    candidate = root_resolved.joinpath(requested)
    cursor = root_resolved
    for part in requested.parts:
        cursor = cursor / part
        if cursor.is_symlink():
            raise ValueError("Symlinks are not allowed in artifact paths: " + requested.as_posix())
    resolved = candidate.resolve(strict=True)
    if not resolved.is_relative_to(root_resolved):
        raise ValueError("Artifact path escapes the mission root")
    if not resolved.is_file():
        raise ValueError("Artifact path is not a regular file: " + requested.as_posix())
    return resolved, requested.as_posix()


def analyze_mission_dag(steps: Sequence[Mapping[str, Any]]) -> dict[str, Any]:
    """Validate a bounded DAG and return a stable topological schedule.

    Nodes in one wave have no dependencies on each other and are eligible to run
    concurrently. Each node is modeled as unit-cost for critical-path estimates.
    """
    if not isinstance(steps, (list, tuple)):
        raise ValueError("steps must be a list")
    if not steps:
        return {
            "schema_version": 1,
            "node_count": 0,
            "dependency_edges": 0,
            "topological_order": [],
            "waves": [],
            "critical_path": [],
            "critical_path_length": 0,
            "max_parallelism": 0,
            "theoretical_parallelism_ratio": 0.0,
            "ready": [],
        }
    if len(steps) > _MAX_NODES:
        raise ValueError(f"At most {_MAX_NODES} mission steps are supported")

    identifiers: list[str] = []
    dependencies: dict[str, list[str]] = {}
    labels: dict[str, str] = {}
    for index, step in enumerate(steps):
        if not isinstance(step, Mapping):
            raise ValueError(f"Step {index} must be an object")
        node_id = step.get("id")
        if not isinstance(node_id, str) or not _NODE_ID_RE.fullmatch(node_id):
            raise ValueError(f"Step {index} has an invalid id")
        if node_id in dependencies:
            raise ValueError("Step identifiers must be unique: " + node_id)
        raw_dependencies = step.get("dependencies", [])
        if not isinstance(raw_dependencies, list) or len(raw_dependencies) > _MAX_NODES:
            raise ValueError(f"Dependencies for {node_id} must be a bounded list")
        if any(not isinstance(dep, str) for dep in raw_dependencies):
            raise ValueError(f"Dependencies for {node_id} must be strings")
        if len(raw_dependencies) != len(set(raw_dependencies)):
            raise ValueError(f"Dependencies for {node_id} must not contain duplicates")
        labels[node_id] = str(step.get("name") or node_id)[:120]
        identifiers.append(node_id)
        dependencies[node_id] = list(raw_dependencies)

    known = set(identifiers)
    for node_id, deps in dependencies.items():
        if node_id in deps:
            raise ValueError(f"Step {node_id} cannot depend on itself")
        missing = sorted(set(deps) - known)
        if missing:
            raise ValueError(f"Step {node_id} references unknown dependencies: {', '.join(missing)}")

    remaining = set(identifiers)
    completed: set[str] = set()
    waves: list[list[str]] = []
    while remaining:
        wave = [
            node_id for node_id in identifiers
            if node_id in remaining and set(dependencies[node_id]).issubset(completed)
        ]
        if not wave:
            raise ValueError("Mission dependency graph must be acyclic")
        waves.append(wave)
        completed.update(wave)
        remaining.difference_update(wave)

    topological_order = [node_id for wave in waves for node_id in wave]
    depth: dict[str, int] = {}
    predecessor: dict[str, str | None] = {}
    for node_id in topological_order:
        parents = dependencies[node_id]
        if not parents:
            depth[node_id] = 1
            predecessor[node_id] = None
        else:
            best_parent = max(parents, key=lambda item: depth[item])
            depth[node_id] = depth[best_parent] + 1
            predecessor[node_id] = best_parent
    endpoint = max(topological_order, key=lambda item: depth[item])
    critical_path: list[str] = []
    cursor: str | None = endpoint
    while cursor is not None:
        critical_path.append(cursor)
        cursor = predecessor[cursor]
    critical_path.reverse()

    return {
        "schema_version": 1,
        "node_count": len(identifiers),
        "dependency_edges": sum(len(item) for item in dependencies.values()),
        "topological_order": topological_order,
        "waves": waves,
        "critical_path": critical_path,
        "critical_path_length": len(critical_path),
        "max_parallelism": max(map(len, waves)),
        "theoretical_parallelism_ratio": round(len(identifiers) / len(critical_path), 3),
        "ready": list(waves[0]),
        "labels": labels,
    }


def write_artifact_manifest(
    mission_root: Path | str,
    artifact_paths: Sequence[Path | str],
    manifest_path: Path | str,
    mission_id: str,
) -> dict[str, Any]:
    """Write a SHA-256 ledger for mission files, excluding the ledger itself."""
    if not isinstance(mission_id, str) or not _MISSION_ID_RE.fullmatch(mission_id):
        raise ValueError("Invalid mission identifier")
    root = Path(mission_root).resolve()
    if not root.is_dir():
        raise ValueError("Mission root must be an existing directory")

    ledger_relative = Path(manifest_path)
    if ledger_relative.is_absolute():
        try:
            ledger_relative = ledger_relative.resolve().relative_to(root)
        except ValueError as exc:
            raise ValueError("Manifest must be inside the mission root") from exc
    ledger_target, ledger_name = _safe_relative_parent(root, ledger_relative)

    seen: set[str] = set()
    artifacts: list[dict[str, Any]] = []
    for item in artifact_paths:
        target, relative = _safe_relative_file(root, item)
        if relative == ledger_name:
            raise ValueError("The artifact manifest cannot hash itself")
        if relative in seen:
            raise ValueError("Duplicate artifact path: " + relative)
        seen.add(relative)
        digest = hashlib.sha256(target.read_bytes()).hexdigest()
        artifacts.append({
            "path": relative,
            "bytes": target.stat().st_size,
            "sha256": digest,
        })
    artifacts.sort(key=lambda item: item["path"])
    payload: dict[str, Any] = {
        "schema_version": _MANIFEST_SCHEMA_VERSION,
        "mission_id": mission_id,
        "hash_algorithm": "sha256",
        "self_hash_excluded": True,
        "artifact_count": len(artifacts),
        "artifacts": artifacts,
    }
    payload["manifest_sha256"] = hashlib.sha256(_canonical_json(payload)).hexdigest()
    ledger_target.parent.mkdir(parents=True, exist_ok=True)
    ledger_target.write_text(json.dumps(payload, indent=2, ensure_ascii=False) + "\n", encoding="utf-8")
    return payload


def _safe_relative_parent(root: Path, relative: Path) -> tuple[Path, str]:
    """Validate a manifest destination, allowing only its final component to be new."""
    if relative.is_absolute() or not relative.parts or any(part in {"", ".", ".."} for part in relative.parts):
        raise ValueError("Manifest path must be non-empty, relative, and traversal-free")
    root_resolved = root.resolve()
    cursor = root_resolved
    for part in relative.parts[:-1]:
        cursor = cursor / part
        if cursor.is_symlink():
            raise ValueError("Symlinks are not allowed in manifest paths")
    target = root_resolved.joinpath(relative)
    if target.is_symlink():
        raise ValueError("Manifest path cannot be a symlink")
    if not target.resolve().is_relative_to(root_resolved):
        raise ValueError("Manifest path escapes the mission root")
    return target, relative.as_posix()


def verify_artifact_manifest(
    mission_root: Path | str,
    manifest_path: Path | str = "output/artifact_manifest.json",
) -> dict[str, Any]:
    """Verify the ledger seal and every listed artifact without executing any file."""
    errors: list[str] = []
    try:
        root = Path(mission_root).resolve(strict=True)
        if not root.is_dir():
            raise ValueError("Mission root must be a directory")
        ledger, ledger_name = _safe_relative_file(root, manifest_path)
        payload = json.loads(ledger.read_text(encoding="utf-8"))
        if not isinstance(payload, dict):
            raise ValueError("Manifest root must be a JSON object")
        stored_digest = payload.get("manifest_sha256")
        if not isinstance(stored_digest, str) or not _SHA256_RE.fullmatch(stored_digest):
            raise ValueError("Manifest seal is missing or malformed")
        unsigned = {key: value for key, value in payload.items() if key != "manifest_sha256"}
        actual_digest = hashlib.sha256(_canonical_json(unsigned)).hexdigest()
        if actual_digest != stored_digest:
            errors.append("Manifest seal mismatch; ledger content changed")
        if payload.get("schema_version") != _MANIFEST_SCHEMA_VERSION:
            errors.append("Unsupported artifact manifest schema version")
        mission_id = payload.get("mission_id")
        if not isinstance(mission_id, str) or not _MISSION_ID_RE.fullmatch(mission_id):
            errors.append("Invalid mission identifier in artifact manifest")
        artifacts = payload.get("artifacts")
        if not isinstance(artifacts, list):
            raise ValueError("Manifest artifacts must be a list")
        if payload.get("artifact_count") != len(artifacts):
            errors.append("Artifact count does not match the ledger")
        seen: set[str] = set()
        checked = 0
        for item in artifacts:
            if not isinstance(item, dict):
                errors.append("Invalid artifact entry")
                continue
            relative = item.get("path")
            expected_hash = item.get("sha256")
            expected_bytes = item.get("bytes")
            if not isinstance(relative, str) or relative == ledger_name:
                errors.append("Invalid or self-referential artifact path")
                continue
            if relative in seen:
                errors.append("Duplicate artifact path: " + relative)
                continue
            seen.add(relative)
            if not isinstance(expected_hash, str) or not _SHA256_RE.fullmatch(expected_hash):
                errors.append("Malformed SHA-256 digest for " + relative)
                continue
            if not isinstance(expected_bytes, int) or isinstance(expected_bytes, bool) or expected_bytes < 0:
                errors.append("Malformed file size for " + relative)
                continue
            try:
                target, _ = _safe_relative_file(root, relative)
            except (OSError, ValueError) as exc:
                errors.append(relative + ": " + str(exc))
                continue
            actual_bytes = target.stat().st_size
            actual_hash = hashlib.sha256(target.read_bytes()).hexdigest()
            checked += 1
            if actual_bytes != expected_bytes:
                errors.append(relative + ": size mismatch")
            if actual_hash != expected_hash:
                errors.append(relative + ": SHA-256 mismatch")
        return {
            "valid": not errors,
            "mission_id": mission_id,
            "checked": checked,
            "expected": len(artifacts),
            "manifest_sha256": actual_digest,
            "errors": errors,
        }
    except (OSError, ValueError, json.JSONDecodeError) as exc:
        return {
            "valid": False,
            "mission_id": None,
            "checked": 0,
            "expected": 0,
            "manifest_sha256": None,
            "errors": [str(exc)],
        }


def resolve_mission_frontier(nodes: Sequence[Mapping[str, Any]]) -> dict[str, Any]:
    """Resolve currently runnable work from dependency edges and persisted node states.

    This is a pure decision function: it never invokes a node or changes any state.
    Pending nodes whose dependency chain includes a failed/blocked node are reported
    as effectively blocked even if their stored state is still pending.
    """
    if not isinstance(nodes, (list, tuple)):
        raise ValueError("nodes must be a list")
    if not nodes:
        return {
            "schema_version": 1,
            "node_count": 0,
            "ready": [],
            "waiting": [],
            "blocked": [],
            "running": [],
            "completed": [],
            "failed": [],
            "effective_statuses": {},
            "terminal": False,
            "healthy": True,
            "has_ready_work": False,
            "stalled": False,
            "recommendation": "no_nodes",
        }

    normalized: list[dict[str, Any]] = []
    valid_statuses = {"pending", "running", "completed", "blocked", "failed"}
    for index, node in enumerate(nodes):
        if not isinstance(node, Mapping):
            raise ValueError(f"Node {index} must be an object")
        node_id = node.get("id")
        status = node.get("status", "pending")
        if not isinstance(status, str) or status not in valid_statuses:
            raise ValueError(f"Node {node_id!r} has an invalid status")
        normalized.append({
            "id": node_id,
            "name": node.get("name", node_id),
            "dependencies": node.get("dependencies", []),
            "status": status,
        })

    schedule = analyze_mission_dag(normalized)
    by_id = {node["id"]: node for node in normalized}
    effective: dict[str, str] = {}
    reasons: dict[str, list[str]] = {}

    for node_id in schedule["topological_order"]:
        node = by_id[node_id]
        status = node["status"]
        deps = node["dependencies"]
        if status in {"completed", "running"}:
            unfinished = [dep for dep in deps if effective.get(dep) != "completed"]
            if unfinished:
                raise ValueError(
                    f"Node {node_id!r} is {status} but dependencies are not completed: "
                    + ", ".join(unfinished)
                )
            effective[node_id] = status
            continue
        if status == "failed":
            effective[node_id] = "failed"
            reasons[node_id] = []
            continue
        if status == "blocked":
            effective[node_id] = "blocked"
            reasons[node_id] = []
            continue

        blockers = [dep for dep in deps if effective.get(dep) in {"failed", "blocked"}]
        if blockers:
            effective[node_id] = "blocked"
            reasons[node_id] = blockers
        elif all(effective.get(dep) == "completed" for dep in deps):
            effective[node_id] = "ready"
        else:
            effective[node_id] = "waiting"

    ready = [node_id for node_id in schedule["topological_order"] if effective[node_id] == "ready"]
    waiting = [
        {
            "id": node_id,
            "waiting_for": [
                dep for dep in by_id[node_id]["dependencies"]
                if effective.get(dep) != "completed"
            ],
        }
        for node_id in schedule["topological_order"] if effective[node_id] == "waiting"
    ]
    blocked = [
        {
            "id": node_id,
            "blocking_dependencies": reasons.get(node_id, []),
            "reason": (
                "Upstream dependency is failed or blocked."
                if reasons.get(node_id)
                else "Node is explicitly marked blocked; inspect its recorded diagnostics."
            ),
        }
        for node_id in schedule["topological_order"] if effective[node_id] == "blocked"
    ]
    running = [node_id for node_id in schedule["topological_order"] if effective[node_id] == "running"]
    completed = [node_id for node_id in schedule["topological_order"] if effective[node_id] == "completed"]
    failed = [node_id for node_id in schedule["topological_order"] if effective[node_id] == "failed"]
    terminal_states = {"completed", "failed", "blocked"}
    terminal = len(effective) > 0 and all(status in terminal_states for status in effective.values())
    healthy = not failed and not blocked

    if failed or blocked:
        recommendation = "investigate_blockers"
    elif ready:
        recommendation = "run_ready_nodes"
    elif running:
        recommendation = "wait_for_running_nodes"
    elif terminal and len(completed) == len(effective):
        recommendation = "mission_complete"
    elif waiting:
        recommendation = "inspect_dependency_state"
    else:
        recommendation = "no_work"

    return {
        "schema_version": 1,
        "node_count": len(normalized),
        "topological_order": schedule["topological_order"],
        "ready": ready,
        "waiting": waiting,
        "blocked": blocked,
        "running": running,
        "completed": completed,
        "failed": failed,
        "effective_statuses": effective,
        "terminal": terminal,
        "healthy": healthy,
        "has_ready_work": bool(ready),
        "stalled": bool(waiting) and not ready and not running and not terminal,
        "recommendation": recommendation,
    }


_OBSERVABLE_CRITERION_RE = re.compile(
    r"\b(verif\w*|validat\w*|return\w*|contain\w*|create\w*|produce\w*|reject\w*|"
    r"report\w*|match\w*|equal\w*|raise\w*|fail\w*|pass\w*|record\w*|"
    r"include\w*|write\w*|compile\w*|test\w*|isolat\w*|automated|bounded|review\w*|"
    r"given|when|then|must|shall|should|at least|at most|no more than|exactly)\b|[<>]=?|\b\d+\b",
    re.IGNORECASE,
)


def assess_mission_readiness(blueprint: Mapping[str, Any]) -> dict[str, Any]:
    """Apply transparent deterministic rules to assess a blueprint before review.

    This is a triage aid, not a proof that requirements are correct. Every finding
    explains its rule; no LLM or network call is made.
    """
    if not isinstance(blueprint, Mapping):
        raise ValueError("blueprint must be an object")

    findings: list[dict[str, str]] = []

    def add(code: str, severity: str, message: str) -> None:
        findings.append({"code": code, "severity": severity, "message": message})

    title = blueprint.get("title")
    if not isinstance(title, str) or not title.strip() or len(title.strip()) > 200:
        add("title_invalid", "blocker", "Provide a non-empty mission title of at most 200 characters.")
    objective = blueprint.get("objective")
    if not isinstance(objective, str) or not objective.strip() or len(objective.strip()) > 4000:
        add("objective_invalid", "blocker", "Provide a non-empty objective of at most 4,000 characters.")
    elif len(objective.strip()) < 20:
        add("objective_underspecified", "warning", "Objective is very short; define the expected outcome and boundaries.")

    raw_steps = blueprint.get("steps")
    if not isinstance(raw_steps, list) or not raw_steps:
        add("steps_missing", "blocker", "Provide at least one bounded implementation step.")
    else:
        try:
            schedule = analyze_mission_dag(raw_steps)
            if schedule["node_count"] > 10:
                add("scope_large", "warning", "Mission is near the 12-step schema limit; consider splitting it.")
            if not schedule["dependency_edges"]:
                add("no_dependencies", "info", "No dependency edges are defined; confirm that all steps are independent.")
        except (TypeError, ValueError) as exc:
            schedule = None
            add("dependency_graph_invalid", "blocker", "Fix the mission dependency graph: " + str(exc))
    if isinstance(raw_steps, list) and len(raw_steps) > _MAX_NODES:
        add("step_limit_exceeded", "blocker", f"Use at most {_MAX_NODES} steps per mission.")

    criteria = blueprint.get("acceptance_criteria")
    if not isinstance(criteria, list) or not criteria or len(criteria) > 20:
        add("acceptance_criteria_invalid", "blocker", "Provide between 1 and 20 acceptance criteria.")
    else:
        for index, criterion in enumerate(criteria, start=1):
            if not isinstance(criterion, str) or not criterion.strip():
                add("acceptance_criterion_empty", "blocker", f"Acceptance criterion {index} is empty or not text.")
                continue
            if len(criterion.strip()) < 12:
                add("acceptance_criterion_short", "warning", f"Acceptance criterion {index} is too short to review reliably.")
            if not _OBSERVABLE_CRITERION_RE.search(criterion):
                add(
                    "acceptance_criterion_ambiguous",
                    "warning",
                    f"Acceptance criterion {index} may be subjective; express a testable output, condition, or threshold.",
                )

    constraints = blueprint.get("constraints", [])
    if not isinstance(constraints, list):
        add("constraints_invalid", "blocker", "Constraints must be provided as a list.")
    elif not constraints or not any(isinstance(item, str) and item.strip() for item in constraints):
        add("constraints_missing", "warning", "Record relevant scope, privacy, integration, or operational constraints.")

    risks = blueprint.get("risks", [])
    if not isinstance(risks, list):
        add("risks_invalid", "blocker", "Risks must be provided as a list.")
    elif not risks or not any(isinstance(item, str) and item.strip() for item in risks):
        add("risks_missing", "warning", "Record at least one risk or explicitly document why the mission has no material risks.")

    if blueprint.get("human_approval_required", True) is not True:
        add("human_gate_disabled", "blocker", "Human review must remain enabled before generated behavior is promoted.")

    blockers = sum(item["severity"] == "blocker" for item in findings)
    warnings = sum(item["severity"] == "warning" for item in findings)
    infos = sum(item["severity"] == "info" for item in findings)
    score = max(0, 100 - blockers * 30 - warnings * 8 - infos * 1)
    recommendation = "needs_revision" if blockers or score < 80 else "ready_for_human_review"
    return {
        "schema_version": 1,
        "mission_id": blueprint.get("mission_id"),
        "score": score,
        "recommendation": recommendation,
        "counts": {"blockers": blockers, "warnings": warnings, "info": infos},
        "findings": findings,
        "policy": {
            "generated_candidate_execution": "not_performed",
            "human_review_required": True,
            "method": "deterministic_rules_v1",
            "interpretation": "Heuristic triage for review prioritization; not a proof of correctness or security.",
        },
    }
