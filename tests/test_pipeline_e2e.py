"""End-to-end Architect and Builder execution in local deterministic mode."""

from __future__ import annotations

import json
import subprocess
import sys
from pathlib import Path

import pytest

from mcp_and_crews.run_architect import run_architect
from mcp_and_crews.run_builder import run_builder


def test_architect_builder_produces_a_verified_review_bundle(tmp_path: Path) -> None:
    artifacts = tmp_path / "generated_artifacts"
    dashboard = tmp_path / "docs" / "state.json"
    initial = run_architect(
        issue_number=0,
        title="Build an internal reporting tool",
        description="Generate a typed report manifest and visual dependency plan.",
        output_dir=artifacts, state_path=dashboard, dry_run=True,
    )
    assert initial.status == "planned"
    mission = artifacts / "missions" / "local-validation"
    assert json.loads((mission / "blueprint.json").read_text(encoding="utf-8"))["mission_id"] == "local-validation"

    final = run_builder(issue_number=0, output_dir=artifacts, state_path=dashboard, dry_run=True)
    output = mission / "output"
    runner = output / "mission_runner.py"
    solution = output / "mission_solution.py"
    generator = json.loads((output / "generator.json").read_text(encoding="utf-8"))
    assert final.status == "ready_for_review"
    assert runner.is_file()
    assert solution.is_file()
    assert generator["provider"] == "deterministic"
    assert generator["syntax"] == "valid"
    assert generator["executed"] is False
    assert "def solve(" in solution.read_text(encoding="utf-8")
    compile(solution.read_text(encoding="utf-8"), str(solution), "exec")
    assert (output / "architecture.svg").read_text(encoding="utf-8").startswith("<svg")
    assert (output / "mission.json").is_file()
    compile(runner.read_text(encoding="utf-8"), str(runner), "exec")

    result_file = output / "test-result.json"
    subprocess.run(
        [sys.executable, str(runner), "--output", str(result_file), "--print-json"],
        text=True, capture_output=True, check=True, timeout=10,
    )
    manifest = json.loads(result_file.read_text(encoding="utf-8"))
    assert manifest["mission_id"] == "local-validation"
    assert manifest["executed_task_logic"] is False

    snapshot = json.loads(dashboard.read_text(encoding="utf-8"))
    assert snapshot["status"] == "ready_for_review"
    assert snapshot["metrics"]["steps"] == 5
    assert snapshot["mission"]["human_approval_required"] is True


def test_builder_refuses_to_run_without_architect_state(tmp_path: Path) -> None:
    with pytest.raises(FileNotFoundError):
        run_builder(issue_number=77, output_dir=tmp_path / "generated_artifacts",
                    state_path=tmp_path / "state.json", dry_run=True)
