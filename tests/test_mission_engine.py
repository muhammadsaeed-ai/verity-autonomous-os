"""Regression tests for deterministic scheduling and artifact provenance."""

from __future__ import annotations

import json
from pathlib import Path

import pytest

from mcp_and_crews.mission_engine import (
    analyze_mission_dag,
    assess_mission_readiness,
    resolve_mission_frontier,
    verify_artifact_manifest,
    write_artifact_manifest,
)


def test_scheduler_builds_deterministic_parallel_waves_and_critical_path() -> None:
    steps = [
        {"id": "discover", "name": "Discover", "dependencies": []},
        {"id": "api", "name": "API", "dependencies": ["discover"]},
        {"id": "ui", "name": "UI", "dependencies": ["discover"]},
        {"id": "verify", "name": "Verify", "dependencies": ["api", "ui"]},
        {"id": "docs", "name": "Docs", "dependencies": []},
    ]

    result = analyze_mission_dag(steps)

    assert result["waves"] == [["discover", "docs"], ["api", "ui"], ["verify"]]
    assert result["topological_order"] == ["discover", "docs", "api", "ui", "verify"]
    assert result["critical_path_length"] == 3
    assert result["critical_path"] in (["discover", "api", "verify"], ["discover", "ui", "verify"])
    assert result["max_parallelism"] == 2
    assert result["dependency_edges"] == 4
    assert result["theoretical_parallelism_ratio"] == 1.667
    assert analyze_mission_dag(steps) == result


@pytest.mark.parametrize(
    ("steps", "message"),
    [
        ([{"id": "a", "dependencies": []}, {"id": "a", "dependencies": []}], "unique"),
        ([{"id": "a", "dependencies": ["missing"]}], "unknown dependencies"),
        ([{"id": "a", "dependencies": ["b"]}, {"id": "b", "dependencies": ["a"]}], "acyclic"),
        ([{"id": "a", "dependencies": ["a"]}], "itself"),
        ([{"id": "a", "dependencies": ["a", "a"]}], "duplicates"),
    ],
)
def test_scheduler_rejects_invalid_graphs(steps: list[dict], message: str) -> None:
    with pytest.raises(ValueError, match=message):
        analyze_mission_dag(steps)


def test_scheduler_rejects_oversized_graph() -> None:
    steps = [{"id": f"step-{i}", "dependencies": []} for i in range(13)]
    with pytest.raises(ValueError, match="At most 12"):
        analyze_mission_dag(steps)


def _make_files(root: Path) -> list[Path]:
    (root / "blueprint.json").write_text('{"mission_id":"issue-12"}\n', encoding="utf-8")
    (root / "output").mkdir()
    (root / "output" / "mission_solution.py").write_text("def solve(payload):\n    return payload\n", encoding="utf-8")
    (root / "output" / "mission_schedule.json").write_text('{"waves":[["a"]]}\n', encoding="utf-8")
    return [Path("blueprint.json"), Path("output/mission_solution.py"), Path("output/mission_schedule.json")]


def test_artifact_manifest_seals_and_verifies_build_outputs(tmp_path: Path) -> None:
    paths = _make_files(tmp_path)
    manifest = write_artifact_manifest(
        tmp_path, paths, Path("output/artifact_manifest.json"), "issue-12"
    )
    assert manifest["artifact_count"] == 3
    assert "output/artifact_manifest.json" not in [item["path"] for item in manifest["artifacts"]]

    checked = verify_artifact_manifest(tmp_path)
    assert checked["valid"] is True
    assert checked["checked"] == 3
    assert checked["errors"] == []


def test_artifact_manifest_detects_changed_file_bytes(tmp_path: Path) -> None:
    paths = _make_files(tmp_path)
    write_artifact_manifest(tmp_path, paths, Path("output/artifact_manifest.json"), "issue-12")
    (tmp_path / "output" / "mission_solution.py").write_text("def solve(payload):\n    return {'changed': True}\n", encoding="utf-8")

    checked = verify_artifact_manifest(tmp_path)
    assert checked["valid"] is False
    assert any("SHA-256 mismatch" in error for error in checked["errors"])


def test_artifact_manifest_detects_modified_ledger(tmp_path: Path) -> None:
    paths = _make_files(tmp_path)
    write_artifact_manifest(tmp_path, paths, Path("output/artifact_manifest.json"), "issue-12")
    manifest_path = tmp_path / "output" / "artifact_manifest.json"
    payload = json.loads(manifest_path.read_text(encoding="utf-8"))
    payload["mission_id"] = "issue-13"
    manifest_path.write_text(json.dumps(payload), encoding="utf-8")

    checked = verify_artifact_manifest(tmp_path)
    assert checked["valid"] is False
    assert any("Manifest seal mismatch" in error for error in checked["errors"])


@pytest.mark.parametrize("path", ["../outside.txt", "/tmp/absolute.txt", "output/../outside.txt"])
def test_artifact_manifest_rejects_path_traversal(tmp_path: Path, path: str) -> None:
    paths = _make_files(tmp_path)
    with pytest.raises(ValueError, match="relative|traversal"):
        write_artifact_manifest(tmp_path, paths + [Path(path)], Path("output/artifact_manifest.json"), "issue-12")


def test_artifact_manifest_detects_missing_files(tmp_path: Path) -> None:
    paths = _make_files(tmp_path)
    write_artifact_manifest(tmp_path, paths, Path("output/artifact_manifest.json"), "issue-12")
    (tmp_path / "blueprint.json").unlink()

    checked = verify_artifact_manifest(tmp_path)
    assert checked["valid"] is False
    assert any("blueprint.json" in error for error in checked["errors"])


def test_frontier_resolves_ready_and_waiting_nodes() -> None:
    nodes = [
        {"id": "input", "dependencies": [], "status": "completed"},
        {"id": "api", "dependencies": ["input"], "status": "pending"},
        {"id": "ui", "dependencies": ["input"], "status": "pending"},
        {"id": "integration", "dependencies": ["api", "ui"], "status": "pending"},
        {"id": "docs", "dependencies": [], "status": "pending"},
    ]
    result = resolve_mission_frontier(nodes)
    assert result["ready"] == ["docs", "api", "ui"]
    assert result["waiting"] == [{"id": "integration", "waiting_for": ["api", "ui"]}]
    assert result["blocked"] == []
    assert result["has_ready_work"] is True
    assert result["recommendation"] == "run_ready_nodes"


def test_frontier_propagates_failure_to_downstream_nodes() -> None:
    nodes = [
        {"id": "root", "dependencies": [], "status": "failed"},
        {"id": "child", "dependencies": ["root"], "status": "pending"},
        {"id": "grandchild", "dependencies": ["child"], "status": "pending"},
    ]
    result = resolve_mission_frontier(nodes)
    assert result["effective_statuses"] == {
        "root": "failed", "child": "blocked", "grandchild": "blocked"
    }
    assert [item["id"] for item in result["blocked"]] == ["child", "grandchild"]
    assert result["healthy"] is False
    assert result["terminal"] is True
    assert result["recommendation"] == "investigate_blockers"


def test_frontier_requires_completed_dependencies_for_running_or_completed_nodes() -> None:
    nodes = [
        {"id": "root", "dependencies": [], "status": "pending"},
        {"id": "child", "dependencies": ["root"], "status": "completed"},
    ]
    with pytest.raises(ValueError, match="dependencies are not completed"):
        resolve_mission_frontier(nodes)


def test_frontier_reports_successful_terminal_mission() -> None:
    result = resolve_mission_frontier([
        {"id": "root", "dependencies": [], "status": "completed"},
        {"id": "child", "dependencies": ["root"], "status": "completed"},
    ])
    assert result["terminal"] is True
    assert result["healthy"] is True
    assert result["recommendation"] == "mission_complete"


def test_frontier_rejects_invalid_state_status() -> None:
    with pytest.raises(ValueError, match="invalid status"):
        resolve_mission_frontier([{"id": "node", "dependencies": [], "status": "done"}])


def test_readiness_assessment_accepts_default_reviewable_blueprint() -> None:
    from mcp_and_crews.run_architect import build_blueprint

    blueprint, _ = build_blueprint(
        31,
        "Build an auditable data export service",
        "Generate and verify a bounded data export package with a reviewable manifest.",
        dry_run=True,
    )
    report = assess_mission_readiness(blueprint.model_dump(mode="json"))
    assert report["score"] >= 80
    assert report["recommendation"] == "ready_for_human_review"
    assert report["counts"]["blockers"] == 0
    assert report["policy"]["generated_candidate_execution"] == "not_performed"
    assert report["policy"]["human_review_required"] is True


def test_readiness_assessment_flags_incomplete_and_ambiguous_blueprint() -> None:
    report = assess_mission_readiness({
        "mission_id": "issue-32",
        "title": "",
        "objective": "Do it.",
        "steps": [
            {"id": "a", "name": "A", "dependencies": ["b"]},
            {"id": "b", "name": "B", "dependencies": ["a"]},
        ],
        "acceptance_criteria": ["Make it awesome"],
        "constraints": [],
        "risks": [],
        "human_approval_required": False,
    })
    codes = {item["code"] for item in report["findings"]}
    assert report["recommendation"] == "needs_revision"
    assert report["counts"]["blockers"] >= 3
    assert "title_invalid" in codes
    assert "dependency_graph_invalid" in codes
    assert "acceptance_criterion_ambiguous" in codes
    assert "constraints_missing" in codes
    assert "risks_missing" in codes
    assert "human_gate_disabled" in codes
