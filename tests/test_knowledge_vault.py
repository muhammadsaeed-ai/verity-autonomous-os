"""Integrity checks for the knowledge base and automation manifests."""

from __future__ import annotations

import json
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


def test_initial_dashboard_state_is_valid_json() -> None:
    state = json.loads((ROOT / "docs" / "state.json").read_text(encoding="utf-8"))
    assert state["schema_version"] == 1
    assert state["status"] == "idle"
    assert isinstance(state["dag"], list)
    assert isinstance(state["artifacts"], list)
