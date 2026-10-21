"""Schema boundary tests for the mission DAG and persisted identity."""

from __future__ import annotations

from pydantic import ValidationError
import pytest

from mcp_and_crews.run_architect import build_blueprint
from mcp_and_crews.schemas import DAGNode, MissionBlueprint, MissionState


def _blueprint_payload() -> dict:
    blueprint, _ = build_blueprint(
        27,
        "Test mission graph",
        "Validate the mission graph before building.",
        dry_run=True,
    )
    return blueprint.model_dump(mode="json")


def test_blueprint_rejects_duplicate_step_ids() -> None:
    payload = _blueprint_payload()
    payload["steps"][1]["id"] = payload["steps"][0]["id"]
    with pytest.raises(ValidationError, match="identifiers must be unique"):
        MissionBlueprint.model_validate(payload)


def test_blueprint_rejects_unknown_dependencies() -> None:
    payload = _blueprint_payload()
    payload["steps"][1]["dependencies"] = ["not-a-step"]
    with pytest.raises(ValidationError, match="unknown dependencies"):
        MissionBlueprint.model_validate(payload)


def test_blueprint_rejects_dependency_cycles() -> None:
    payload = _blueprint_payload()
    payload["steps"][0]["dependencies"] = ["architecture"]
    with pytest.raises(ValidationError, match="must be acyclic"):
        MissionBlueprint.model_validate(payload)


def test_persisted_state_rejects_unknown_dag_dependencies() -> None:
    with pytest.raises(ValidationError, match="unknown dependencies"):
        MissionState(
            mission_id="test-mission",
            issue_number=27,
            title="Test mission",
            dag=[
                DAGNode(
                    id="step-a",
                    name="Step A",
                    description="A bounded test step.",
                    dependencies=["missing-step"],
                )
            ],
        )
