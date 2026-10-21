"""Integrity checks for the knowledge base and automation manifests."""

from __future__ import annotations

import json
import re
from pathlib import Path

import yaml

ROOT = Path(__file__).resolve().parents[1]


def test_public_api_registry_has_30_plus_keyless_examples() -> None:
    items = json.loads((ROOT / "knowledge_vault" / "public_apis_min.json").read_text(encoding="utf-8"))
    assert len(items) >= 30
    assert len({item["name"] for item in items}) == len(items)
    assert len({item["category"] for item in items}) >= 8
    assert all(item["method"] == "GET" and item["auth"] == "none" for item in items)
    assert all(item["url"].startswith(("https://", "http://")) for item in items)
    assert all(item.get("notes", "").strip() for item in items)


def test_architecture_knowledge_is_substantive() -> None:
    design = (ROOT / "knowledge_vault" / "system_design_rules.md").read_text(encoding="utf-8")
    factors = (ROOT / "knowledge_vault" / "12_factor_rules.md").read_text(encoding="utf-8")
    assert len(design) > 1800
    assert len(factors) > 1800
    assert "Generated-code security" in design
    assert "stateless reducer" in factors


def test_workflows_and_issue_form_parse_as_yaml() -> None:
    paths = sorted((ROOT / ".github" / "workflows").glob("*.yml"))
    paths.append(ROOT / ".github" / "ISSUE_TEMPLATE" / "new_mission.yml")
    assert len(paths) == 5
    for path in paths:
        document = yaml.load(path.read_text(encoding="utf-8"), Loader=yaml.BaseLoader)
        assert isinstance(document, dict) and document, path.name
    assert {p.name for p in paths if p.parent.name == "workflows"} == {
        "01_architect_plan.yml", "02_execute_build.yml", "03_daily_auto_agent.yml", "04_ci_verification.yml"
    }


def test_external_github_actions_are_pinned_to_immutable_commits() -> None:
    for path in sorted((ROOT / ".github" / "workflows").glob("*.yml")):
        document = yaml.load(path.read_text(encoding="utf-8"), Loader=yaml.BaseLoader)
        for job in document.get("jobs", {}).values():
            for step in job.get("steps", []):
                action = step.get("uses")
                if action and not action.startswith("./"):
                    assert re.fullmatch(r"[^@]+@[0-9a-f]{40}", action), (
                        f"{path.name} uses a mutable or unpinned action reference: {action}"
                    )


def test_privileged_issue_workflows_gate_untrusted_public_issues() -> None:
    architect = yaml.load(
        (ROOT / ".github" / "workflows" / "01_architect_plan.yml").read_text(encoding="utf-8"),
        Loader=yaml.BaseLoader,
    )
    builder = yaml.load(
        (ROOT / ".github" / "workflows" / "02_execute_build.yml").read_text(encoding="utf-8"),
        Loader=yaml.BaseLoader,
    )
    architect_guard = architect["jobs"]["architect"]["if"]
    builder_guard = builder["jobs"]["build"]["if"]
    for guard in (architect_guard, builder_guard):
        assert "github.repository_owner" in guard
        assert "author_association" in guard
        assert "OWNER" in guard and "MEMBER" in guard and "COLLABORATOR" in guard
    assert "github.event.action == 'opened'" in architect_guard
    assert "github.event.label.name == 'approved'" in builder_guard


def test_initial_dashboard_state_is_valid_json() -> None:
    state = json.loads((ROOT / "docs" / "state.json").read_text(encoding="utf-8"))
    assert state["schema_version"] == 1
    assert state["status"] == "idle"
    assert isinstance(state["dag"], list)
    assert isinstance(state["artifacts"], list)
