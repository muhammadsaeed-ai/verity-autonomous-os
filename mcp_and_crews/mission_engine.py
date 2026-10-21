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
