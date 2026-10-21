"""Validated domain models shared by the CLI, MCP server, and dashboard."""

from __future__ import annotations

from datetime import datetime, timezone
from typing import Literal

from pydantic import BaseModel, ConfigDict, Field, field_validator, model_validator

NodeStatus = Literal["pending", "running", "completed", "blocked", "failed"]
MissionStatus = Literal["idle", "planned", "approved", "building", "ready_for_review", "failed"]


def utc_now() -> datetime:
    return datetime.now(timezone.utc)


def _validate_dependency_graph(nodes: list[tuple[str, list[str]]], label: str) -> None:
    """Reject ambiguous or cyclic dependency graphs at every model boundary."""
    identifiers = [node_id for node_id, _ in nodes]
    known = set(identifiers)
    if len(known) != len(identifiers):
        raise ValueError(f"{label} node identifiers must be unique")

    dependencies_by_id: dict[str, list[str]] = {}
    for node_id, dependencies in nodes:
        if len(dependencies) != len(set(dependencies)):
            raise ValueError(f"{label} dependencies must not contain duplicates")
        if node_id in dependencies:
            raise ValueError(f"{label} node {node_id!r} cannot depend on itself")
        missing = sorted(set(dependencies) - known)
        if missing:
            raise ValueError(f"{label} node {node_id!r} references unknown dependencies: {', '.join(missing)}")
        dependencies_by_id[node_id] = dependencies

    visiting: set[str] = set()
    visited: set[str] = set()

    def visit(node_id: str) -> None:
        if node_id in visiting:
            raise ValueError(f"{label} dependency graph must be acyclic; cycle detected at {node_id!r}")
        if node_id in visited:
            return
        visiting.add(node_id)
        for dependency in dependencies_by_id[node_id]:
            visit(dependency)
        visiting.remove(node_id)
        visited.add(node_id)

    for node_id in identifiers:
        visit(node_id)


class StrictModel(BaseModel):
    model_config = ConfigDict(extra="forbid", validate_assignment=True)


class DAGNode(StrictModel):
    id: str = Field(min_length=1, max_length=64, pattern=r"^[a-z0-9][a-z0-9_-]*$")
    name: str = Field(min_length=1, max_length=120)
    description: str = Field(min_length=1, max_length=1200)
    dependencies: list[str] = Field(default_factory=list, max_length=12)
    status: NodeStatus = "pending"
    output_path: str | None = None


class PlanStep(StrictModel):
    id: str = Field(min_length=1, max_length=64, pattern=r"^[a-z0-9][a-z0-9_-]*$")
    name: str = Field(min_length=1, max_length=120)
    objective: str = Field(min_length=1, max_length=1200)
    dependencies: list[str] = Field(default_factory=list)
    expected_output: str = Field(min_length=1, max_length=300)


class MissionBlueprint(StrictModel):
    schema_version: int = Field(default=1, ge=1)
    mission_id: str = Field(min_length=1, max_length=80)
    issue_number: int = Field(ge=0)
    title: str = Field(min_length=1, max_length=200)
    objective: str = Field(min_length=1, max_length=4000)
    summary: str = Field(min_length=1, max_length=2000)
    goals: list[str] = Field(min_length=1, max_length=12)
    constraints: list[str] = Field(default_factory=list, max_length=20)
    steps: list[PlanStep] = Field(min_length=1, max_length=12)
    risks: list[str] = Field(default_factory=list, max_length=12)
    acceptance_criteria: list[str] = Field(min_length=1, max_length=20)
    planner_notes: str = Field(default="", max_length=5000)
    created_at: datetime = Field(default_factory=lambda: datetime.now(timezone.utc))

    @field_validator("mission_id")
    @classmethod
    def validate_mission_id(cls, value: str) -> str:
        if not value.replace("-", "").replace("_", "").isalnum():
            raise ValueError("mission_id may contain only letters, numbers, underscores, and hyphens")
        return value

    @model_validator(mode="after")
    def validate_step_graph(self) -> "MissionBlueprint":
        _validate_dependency_graph(
            [(step.id, step.dependencies) for step in self.steps],
            "Mission blueprint",
        )
        return self


class MissionState(StrictModel):
    schema_version: int = Field(default=1, ge=1)
    mission_id: str = Field(min_length=1, max_length=80)
    issue_number: int = Field(ge=0)
    title: str = Field(min_length=1, max_length=200)
    description: str = Field(default="", max_length=4000)
    status: MissionStatus = "planned"
    phase: Literal["idle", "architect", "builder", "maintenance"] = "architect"
    created_at: datetime = Field(default_factory=lambda: datetime.now(timezone.utc))
    updated_at: datetime = Field(default_factory=lambda: datetime.now(timezone.utc))
    blueprint_path: str | None = None
    artifacts: list[str] = Field(default_factory=list)
    dag: list[DAGNode] = Field(default_factory=list)
    errors: list[str] = Field(default_factory=list)
    llm_provider: str = "deterministic"
    human_approval_required: bool = True

    @field_validator("mission_id")
    @classmethod
    def validate_mission_id(cls, value: str) -> str:
        if not value.replace("-", "").replace("_", "").isalnum():
            raise ValueError("mission_id may contain only letters, numbers, underscores, and hyphens")
        return value

    @model_validator(mode="after")
    def validate_state_graph(self) -> "MissionState":
        _validate_dependency_graph(
            [(node.id, node.dependencies) for node in self.dag],
            "Mission state",
        )
        return self
