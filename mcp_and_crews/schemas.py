"""Validated domain models shared by the CLI, MCP server, and dashboard."""

from __future__ import annotations

from datetime import datetime, timezone
from typing import Literal

from pydantic import BaseModel, ConfigDict, Field, field_validator

NodeStatus = Literal["pending", "running", "completed", "blocked", "failed"]
MissionStatus = Literal["idle", "planned", "approved", "building", "ready_for_review", "failed"]


def utc_now() -> datetime:
    return datetime.now(timezone.utc)


class StrictModel(BaseModel):
    model_config = ConfigDict(extra="forbid", validate_assignment=True)


class DAGNode(StrictModel):
    id: str = Field(min_length=1, max_length=64, pattern=r"^[a-z0-9][a-z0-9_-]*$")
    name: str = Field(min_length=1, max_length=120)
    description: str = Field(min_length=1, max_length=1200)
    dependencies: list[str] = Field(default_factory=list)
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
