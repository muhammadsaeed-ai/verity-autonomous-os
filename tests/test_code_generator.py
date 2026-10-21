"""Code-generation safety and deterministic fallback tests."""

from __future__ import annotations

from pathlib import Path

import pytest

from mcp_and_crews.code_generator import clean_source, generate_candidate, validate_source
from mcp_and_crews.run_architect import build_blueprint


def _blueprint():
    blueprint, _ = build_blueprint(
        19,
        "Create a local inventory summary",
        "Validate item counts and return a structured inventory summary.",
        dry_run=True,
    )
    return blueprint


def test_offline_candidate_is_complete_parseable_and_deterministic() -> None:
    candidate = generate_candidate(_blueprint(), dry_run=True)
    assert candidate.provider == "deterministic"
    assert "def solve(" in candidate.source
    assert "TODO" not in candidate.source
    assert "pass" not in candidate.source
    validate_source(candidate.source)
    compile(candidate.source, "mission_solution.py", "exec")


def test_generated_source_rejects_unsafe_imports() -> None:
    with pytest.raises(ValueError, match="Import is not allowed"):
        validate_source("import os\ndef solve(payload: dict) -> dict:\n    return payload\n")


def test_generated_source_rejects_unsafe_calls() -> None:
    with pytest.raises(ValueError, match="Unsafe call"):
        validate_source("def solve(payload: dict) -> dict:\n    return eval('1 + 1')\n")


def test_generated_source_requires_solve_function() -> None:
    with pytest.raises(ValueError, match="must define solve"):
        validate_source("def main():\n    return {'ok': True}\n")


def test_clean_source_removes_markdown_fence() -> None:
    fence = chr(96) * 3
    source = clean_source(fence + "python\ndef solve(payload: dict) -> dict:\n    return payload\n" + fence)
    assert source.startswith("def solve(")
    validate_source(source)
