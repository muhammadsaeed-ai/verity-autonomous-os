"""Direct helper and MCP stdio protocol tests."""

from __future__ import annotations

import json
import os
import select
import subprocess
import sys
from pathlib import Path

import pytest

from mcp_and_crews import mcp_stdio_server
from mcp_and_crews.mcp_stdio_server import (
    assess_blueprint_readiness,
    calculate_change_impact,
    calculate_mission_frontier,
    calculate_recovery_plan,
    create_blueprint,
    generate_svg_spec,
    mission_schedule,
    read_mission_state,
    search_public_apis,
    validate_mission,
    verify_mission_artifacts,
)
from mcp_and_crews.mission_engine import write_artifact_manifest

ROOT = Path(__file__).resolve().parents[1]


def test_mission_validation_accepts_and_rejects_fields() -> None:
    good = validate_mission("Clinic intake", "Route intake forms for review.", ["A record is created"])
    bad = validate_mission("", "", [])
    assert good["valid"] is True
    assert bad["valid"] is False
    assert len(bad["issues"]) >= 3


def test_public_api_search_filters_category() -> None:
    assert len(search_public_apis()) >= 30
    items = search_public_apis("developer")
    assert items and all("developer" in item["category"] for item in items)


def test_blueprint_tool_returns_validated_structure() -> None:
    blueprint = create_blueprint(42, "Example mission", "Build a typed report generator.")
    assert blueprint["mission_id"] == "issue-42"
    assert blueprint["provider"] == "deterministic"
    assert len(blueprint["steps"]) == 5
    assert blueprint["acceptance_criteria"]


def test_mission_schedule_tool_returns_dependency_waves_and_critical_path() -> None:
    result = mcp_stdio_server.schedule_mission([
        {"id": "input", "name": "Input", "dependencies": []},
        {"id": "left", "name": "Left branch", "dependencies": ["input"]},
        {"id": "right", "name": "Right branch", "dependencies": ["input"]},
        {"id": "merge", "name": "Merge", "dependencies": ["left", "right"]},
    ])
    assert result["waves"] == [["input"], ["left", "right"], ["merge"]]
    assert result["critical_path_length"] == 3
    assert result["max_parallelism"] == 2


def test_impact_and_recovery_mcp_helpers_are_functional() -> None:
    impact = calculate_change_impact(
        [{"id": "source", "dependencies": []}, {"id": "consumer", "dependencies": ["source"]}],
        ["source"],
        ["consumer"],
    )
    assert impact["impacted_nodes"] == ["source", "consumer"]
    assert impact["completed_outputs_to_invalidate"] == ["consumer"]

    recovery = calculate_recovery_plan([{
        "id": "source", "dependencies": [], "status": "failed",
        "error_class": "transient", "idempotent": True,
    }])
    assert recovery["failed_nodes"][0]["action"] == "retry_candidate_after_backoff"
    assert recovery["policy"]["automatic_retry_enabled"] is False


def test_mission_frontier_tool_resolves_ready_and_failed_work() -> None:
    result = mcp_stdio_server.calculate_mission_frontier([
        {"id": "source", "dependencies": [], "status": "failed"},
        {"id": "consumer", "dependencies": ["source"], "status": "pending"},
    ])
    assert result["blocked"][0]["id"] == "consumer"
    assert result["recommendation"] == "investigate_blockers"


def test_mission_readiness_tool_returns_actionable_assessment() -> None:
    blueprint = create_blueprint(43, "Create a report manifest", "Create and validate a reviewable report manifest with deterministic output.")
    report = assess_blueprint_readiness(blueprint)
    assert report["recommendation"] == "ready_for_human_review"
    assert report["score"] >= 80
    assert report["policy"]["method"] == "deterministic_rules_v1"


def test_mission_artifact_integrity_tool_checks_repository_mission(tmp_path: Path, monkeypatch) -> None:
    monkeypatch.setattr(mcp_stdio_server, "PROJECT_ROOT", tmp_path)
    mission = tmp_path / "generated_artifacts" / "missions" / "issue-7"
    (mission / "output").mkdir(parents=True)
    (mission / "blueprint.json").write_text('{"mission_id":"issue-7"}\n', encoding="utf-8")
    (mission / "output" / "result.json").write_text('{"status":"passed"}\n', encoding="utf-8")
    write_artifact_manifest(
        mission,
        [Path("blueprint.json"), Path("output/result.json")],
        Path("output/artifact_manifest.json"),
        "issue-7",
    )
    checked = verify_mission_artifacts("issue-7")
    assert checked["valid"] is True
    assert checked["mission_id"] == "issue-7"
    assert checked["checked"] == 2
    with pytest.raises(ValueError, match="unsupported characters"):
        verify_mission_artifacts("../outside")


def test_state_reader_blocks_paths_outside_repository() -> None:
    assert read_mission_state("docs/not-created-yet.json")["available"] is False
    with pytest.raises(ValueError):
        read_mission_state("../../etc/passwd")
    with pytest.raises(ValueError):
        read_mission_state(str((ROOT / "docs" / "state.json").resolve()))


def test_svg_spec_escapes_untrusted_labels() -> None:
    svg = generate_svg_spec("<Mission>", ["Input", "Build & test"])
    assert svg.startswith("<svg")
    assert svg.endswith("</svg>")
    assert "&lt;Mission&gt;" in svg
    assert "Build &amp; test" in svg


def _read_json_line(process: subprocess.Popen[str], timeout: float = 12.0) -> dict:
    assert process.stdout is not None
    ready, _, _ = select.select([process.stdout], [], [], timeout)
    if not ready:
        raise AssertionError("MCP stdio handshake timed out")
    line = process.stdout.readline()
    if not line:
        raise AssertionError("MCP server closed stdout before returning JSON-RPC")
    return json.loads(line)


def test_mcp_stdio_initialize_and_tools_list_handshake() -> None:
    env = os.environ.copy()
    env["PYTHONPATH"] = str(ROOT) + os.pathsep + env.get("PYTHONPATH", "")
    env["VERITY_OFFLINE"] = "1"
    process = subprocess.Popen(
        [sys.executable, "-m", "mcp_and_crews.mcp_stdio_server"],
        cwd=ROOT, env=env, stdin=subprocess.PIPE, stdout=subprocess.PIPE, stderr=subprocess.PIPE,
        text=True, bufsize=1,
    )
    try:
        assert process.stdin is not None
        process.stdin.write(json.dumps({
            "jsonrpc": "2.0", "id": 1, "method": "initialize",
            "params": {
                "protocolVersion": "2025-03-26", "capabilities": {},
                "clientInfo": {"name": "verity-ci", "version": "1.0"},
            },
        }) + "\n")
        process.stdin.flush()
        initialize = _read_json_line(process)
        assert initialize.get("id") == 1 and "result" in initialize
        process.stdin.write(json.dumps({"jsonrpc": "2.0", "method": "notifications/initialized"}) + "\n")
        process.stdin.write(json.dumps({"jsonrpc": "2.0", "id": 2, "method": "tools/list", "params": {}}) + "\n")
        process.stdin.flush()
        response = _read_json_line(process)
        assert response.get("id") == 2
        names = {item["name"] for item in response["result"]["tools"]}
        assert names == {"mission_validate", "public_api_search", "mission_blueprint", "mission_state", "svg_architecture_spec", "mission_schedule", "mission_artifact_integrity", "mission_frontier", "mission_readiness", "mission_change_impact", "mission_recovery_plan"}
    finally:
        if process.stdin is not None:
            process.stdin.close()
        process.terminate()
        try:
            process.wait(timeout=4)
        except subprocess.TimeoutExpired:
            process.kill()
            process.wait(timeout=4)
