"""FastMCP stdio server with five tools and two knowledge resources."""

from __future__ import annotations

import json
from pathlib import Path
from typing import Any

from fastmcp import FastMCP

from mcp_and_crews.run_architect import build_blueprint


PROJECT_ROOT = Path(__file__).resolve().parents[1]
VAULT_ROOT = PROJECT_ROOT / "knowledge_vault"
mcp = FastMCP("VerityAgent Autonomous OS")


def _load_apis() -> list[dict[str, Any]]:
    payload = json.loads((VAULT_ROOT / "public_apis_min.json").read_text(encoding="utf-8"))
    if not isinstance(payload, list):
        raise ValueError("Public API registry must be a JSON list")
    return payload


def validate_mission(title: str, objective: str, acceptance_criteria: list[str]) -> dict[str, Any]:
    issues = []
    if not title.strip():
        issues.append("title must not be empty")
    if len(title.strip()) > 200:
        issues.append("title must not exceed 200 characters")
    if not objective.strip():
        issues.append("objective must not be empty")
    if len(objective.strip()) > 4000:
        issues.append("objective must not exceed 4000 characters")
    if not acceptance_criteria:
        issues.append("at least one acceptance criterion is required")
    if len(acceptance_criteria) > 20:
        issues.append("no more than 20 acceptance criteria are allowed")
    if any(not item.strip() for item in acceptance_criteria):
        issues.append("acceptance criteria must not be empty")
    return {"valid": not issues, "issues": issues, "title": title.strip()[:200]}


def search_public_apis(category: str | None = None) -> list[dict[str, Any]]:
    items = _load_apis()
    if category:
        needle = category.casefold().strip()
        items = [item for item in items if needle in str(item.get("category", "")).casefold()]
    return items


def create_blueprint(issue_number: int, title: str, objective: str) -> dict[str, Any]:
    blueprint, provider = build_blueprint(issue_number, title, objective, dry_run=True)
    payload = blueprint.model_dump(mode="json")
    payload["provider"] = provider
    return payload


def read_mission_state(path: str = "docs/state.json") -> dict[str, Any]:
    requested = Path(path)
    if requested.is_absolute():
        raise ValueError("Only repository-relative paths are allowed")
    resolved = (PROJECT_ROOT / requested).resolve()
    if not resolved.is_relative_to(PROJECT_ROOT):
        raise ValueError("Path escapes the repository root")
    if not resolved.is_file():
        return {"available": False, "path": requested.as_posix(), "message": "No state file has been written yet."}
    return {"available": True, "path": requested.as_posix(), "state": json.loads(resolved.read_text(encoding="utf-8"))}


def generate_svg_spec(title: str, node_names: list[str]) -> str:
    import html

    names = [name.strip()[:48] for name in node_names if name.strip()][:12]
    if not names:
        names = ["Requirements", "Architecture", "Build", "Verify", "Human review"]
    width = max(700, len(names) * 170)
    height = 170
    gap = (width - 100) / max(1, len(names))
    parts = [
        '<svg xmlns="http://www.w3.org/2000/svg" width="' + str(width) + '" height="' + str(height)
        + '" viewBox="0 0 ' + str(width) + ' ' + str(height) + '">',
        '<rect width="100%" height="100%" rx="15" fill="#081223"/>',
        '<text x="22" y="25" fill="#67e8f9" font-family="Arial,sans-serif" font-size="12">'
        + html.escape(title[:100]) + '</text>',
    ]
    for index, name in enumerate(names):
        x = 50 + index * gap
        parts.append(
            '<g><rect x="' + str(x) + '" y="62" width="118" height="50" rx="9" fill="#10243a" stroke="#38bdf8"/>'
            '<text x="' + str(x + 59) + '" y="91" text-anchor="middle" fill="#eff6ff" font-family="Arial,sans-serif" font-size="10">'
            + html.escape(name[:22]) + '</text></g>'
        )
        if index < len(names) - 1:
            parts.append('<path d="M ' + str(x + 118) + ' 87 L ' + str(x + gap) + ' 87" stroke="#64748b" stroke-width="2"/>')
    parts.append("</svg>")
    return "\n".join(parts)


@mcp.tool()
def mission_validate(title: str, objective: str, acceptance_criteria: list[str]) -> dict[str, Any]:
    """Validate required mission fields before the architect phase."""
    return validate_mission(title, objective, acceptance_criteria)


@mcp.tool()
def public_api_search(category: str | None = None) -> list[dict[str, Any]]:
    """Search the local registry of unauthenticated public API examples."""
    return search_public_apis(category)


@mcp.tool()
def mission_blueprint(issue_number: int, title: str, objective: str) -> dict[str, Any]:
    """Create a strict, deterministic mission blueprint."""
    return create_blueprint(issue_number, title, objective)


@mcp.tool()
def mission_state(path: str = "docs/state.json") -> dict[str, Any]:
    """Read a repository-local state snapshot while blocking path traversal."""
    return read_mission_state(path)


@mcp.tool()
def svg_architecture_spec(title: str, node_names: list[str]) -> str:
    """Generate an SVG dependency-flow preview."""
    return generate_svg_spec(title, node_names)


@mcp.resource("vault://system-design")
def system_design_resource() -> str:
    """Provide system design guidance as an MCP resource."""
    return (VAULT_ROOT / "system_design_rules.md").read_text(encoding="utf-8")


@mcp.resource("vault://12-factor")
def twelve_factor_resource() -> str:
    """Provide 12-Factor Agents guidance as an MCP resource."""
    return (VAULT_ROOT / "12_factor_rules.md").read_text(encoding="utf-8")


def main() -> None:
    mcp.run(transport="stdio")


if __name__ == "__main__":
    main()
