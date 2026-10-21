"""Phase 2 CLI: create a reviewable package from a validated blueprint."""

from __future__ import annotations

import argparse
import html
import json
import subprocess
import sys
from pathlib import Path

from mcp_and_crews.code_generator import generate_candidate, validate_source
from mcp_and_crews.mission_engine import analyze_mission_dag, verify_artifact_manifest, write_artifact_manifest
from mcp_and_crews.run_architect import dashboard_snapshot
from mcp_and_crews.schemas import DAGNode, MissionBlueprint, MissionState, utc_now


def _mission_id(issue_number: int) -> str:
    return "issue-" + str(issue_number) if issue_number > 0 else "local-validation"


def _architecture_svg(blueprint: MissionBlueprint) -> str:
    """Render a dependency view directly from validated blueprint nodes."""
    width = max(760, len(blueprint.steps) * 210)
    height = 210
    gap = (width - 140) / max(1, len(blueprint.steps) - 1)
    positions = {step.id: (70 + index * gap, 88) for index, step in enumerate(blueprint.steps)}
    parts = [
        '<svg xmlns="http://www.w3.org/2000/svg" width="' + str(width) + '" height="' + str(height)
        + '" viewBox="0 0 ' + str(width) + ' ' + str(height) + '">',
        '<defs><linearGradient id="panel" x1="0" y1="0" x2="1" y2="1"><stop stop-color="#111b35"/><stop offset="1" stop-color="#07101f"/></linearGradient></defs>',
        '<rect width="100%" height="100%" rx="18" fill="url(#panel)"/>',
        '<text x="26" y="30" fill="#7dd3fc" font-size="13" font-family="Arial,sans-serif">VERITYAGENT / MISSION DAG</text>',
    ]
    for step in blueprint.steps:
        x, y = positions[step.id]
        for dependency in step.dependencies:
            if dependency in positions:
                dx, dy = positions[dependency]
                parts.append(
                    '<path d="M ' + str(dx + 62) + ' ' + str(dy) + ' C ' + str(dx + 105) + ' ' + str(dy)
                    + ', ' + str(x - 105) + ' ' + str(y) + ', ' + str(x - 62) + ' ' + str(y)
                    + '" stroke="#3b82f6" stroke-width="2" fill="none"/>'
                )
    for index, step in enumerate(blueprint.steps):
        x, y = positions[step.id]
        parts.append(
            '<g><rect x="' + str(x - 62) + '" y="' + str(y - 29) + '" width="124" height="58" rx="11" '
            'fill="#0d2035" stroke="#2a7ac4" stroke-width="1.2"/>'
            '<text x="' + str(x) + '" y="' + str(y - 3) + '" text-anchor="middle" fill="#e5f3ff" '
            'font-size="10" font-family="Arial,sans-serif">' + html.escape(step.name[:21]) + '</text>'
            '<text x="' + str(x) + '" y="' + str(y + 14) + '" text-anchor="middle" fill="#7dd3fc" '
            'font-size="9" font-family="Arial,sans-serif">' + str(index + 1).zfill(2) + '</text></g>'
        )
    parts.append(
        '<text x="26" y="' + str(height - 15) + '" fill="#94a3b8" font-size="10" '
        'font-family="Arial,sans-serif">Generated from the validated blueprint; dependencies are directional.</text>'
    )
    parts.append("</svg>")
    return "\n".join(parts)


def _runner_source(blueprint: MissionBlueprint) -> str:
    """Build a trusted inspection runner, separate from candidate implementation."""
    titles = [step.name for step in blueprint.steps]
    steps = [
        {
            "id": step.id, "name": step.name, "objective": step.objective,
            "dependencies": step.dependencies, "expected_output": step.expected_output, "status": "not_run",
        }
        for step in blueprint.steps
    ]
    return (
        '"""Generated, reviewable mission package for VerityAgent."""\n\n'
        "from __future__ import annotations\n\n"
        "import argparse\nimport json\nfrom datetime import datetime, timezone\nfrom pathlib import Path\n\n"
        "MISSION_ID = " + repr(blueprint.mission_id) + "\n"
        "MISSION_TITLE = " + repr(blueprint.title) + "\n"
        "PLANNED_STEP_TITLES = " + repr(titles) + "\n"
        "STEPS = " + repr(steps) + "\n\n"
        "def create_manifest() -> dict:\n"
        "    return {\n"
        "        'mission_id': MISSION_ID,\n"
        "        'mission_title': MISSION_TITLE,\n"
        "        'status': 'ready_for_human_review',\n"
        "        'generated_at': datetime.now(timezone.utc).isoformat(),\n"
        "        'executed_task_logic': False,\n"
        "        'notice': 'This runner validates the package only; mission-specific code is not executed.',\n"
        "        'steps': STEPS,\n"
        "    }\n\n"
        "def main() -> int:\n"
        "    parser = argparse.ArgumentParser(description='Inspect a generated VerityAgent mission plan.')\n"
        "    parser.add_argument('--output', type=Path, default=Path('mission_execution.json'))\n"
        "    parser.add_argument('--print-json', action='store_true')\n"
        "    args = parser.parse_args()\n"
        "    manifest = create_manifest()\n"
        "    args.output.parent.mkdir(parents=True, exist_ok=True)\n"
        "    args.output.write_text(json.dumps(manifest, indent=2) + '\\n', encoding='utf-8')\n"
        "    print('Mission package: ' + MISSION_TITLE)\n"
        "    print('Planned steps: ' + str(len(PLANNED_STEP_TITLES)))\n"
        "    print('State: ready_for_human_review; mission implementation was not executed.')\n"
        "    print('Manifest: ' + str(args.output))\n"
        "    if args.print_json:\n        print(json.dumps(manifest, indent=2))\n"
        "    return 0\n\n"
        "if __name__ == '__main__':\n    raise SystemExit(main())\n"
    )


def run_builder(
    issue_number: int = 0,
    output_dir: Path | str = Path("generated_artifacts"),
    state_path: Path | str = Path("docs/state.json"),
    dry_run: bool = False,
) -> MissionState:
    """Build artifacts, compile-check model source, and only run trusted inspection code."""
    mission_dir = Path(output_dir) / "missions" / _mission_id(issue_number)
    state_file = mission_dir / "state.json"
    blueprint_file = mission_dir / "blueprint.json"
    if not state_file.is_file() or not blueprint_file.is_file():
        raise FileNotFoundError(
            "No architect state found for " + _mission_id(issue_number)
            + ". Run the architect phase first and review its blueprint."
        )

    state = MissionState.model_validate_json(state_file.read_text(encoding="utf-8"))
    blueprint = MissionBlueprint.model_validate_json(blueprint_file.read_text(encoding="utf-8"))
    if state.mission_id != blueprint.mission_id:
        raise ValueError("State and blueprint mission identifiers do not match")
    if state.issue_number != blueprint.issue_number:
        raise ValueError("State and blueprint issue numbers do not match")
    if state.title != blueprint.title:
        raise ValueError("State and blueprint titles do not match")
    if state.status not in {"planned", "approved", "building"}:
        raise ValueError("Mission is not in a buildable state: " + state.status)

    state.status = "building"
    state.phase = "builder"
    state.updated_at = utc_now()
    state_file.write_text(json.dumps(state.model_dump(mode="json"), indent=2) + "\n", encoding="utf-8")

    output = mission_dir / "output"
    output.mkdir(parents=True, exist_ok=True)

    candidate = generate_candidate(blueprint, dry_run=dry_run)
    solution = output / "mission_solution.py"
    solution.write_text(candidate.source, encoding="utf-8")
    validate_source(candidate.source)
    compile(candidate.source, str(solution), "exec")

    generator_report = {
        "provider": candidate.provider,
        "source_file": solution.name,
        "source_bytes": len(candidate.source.encode("utf-8")),
        "syntax": "valid",
        "safety_checks": "restricted_ast_policy_not_a_sandbox",
        "validation_limit": "Static analysis is defense-in-depth only; the generated candidate was not executed.",
        "executed": False,
        "warning": candidate.warning,
    }
    (output / "generator.json").write_text(
        json.dumps(generator_report, indent=2, ensure_ascii=False) + "\n",
        encoding="utf-8",
    )
    if candidate.warning:
        state.errors.append(candidate.warning)
    if candidate.provider != "deterministic":
        state.llm_provider = candidate.provider

    runner = output / "mission_runner.py"
    runner.write_text(_runner_source(blueprint), encoding="utf-8")
    svg = output / "architecture.svg"
    svg.write_text(_architecture_svg(blueprint), encoding="utf-8")
    (output / "mission.json").write_text(
        json.dumps(blueprint.model_dump(mode="json"), indent=2, ensure_ascii=False) + "\n",
        encoding="utf-8",
    )

    schedule = analyze_mission_dag([step.model_dump(mode="json") for step in blueprint.steps])
    schedule_path = output / "mission_schedule.json"
    schedule_path.write_text(
        json.dumps(schedule, indent=2, ensure_ascii=False) + "\n",
        encoding="utf-8",
    )

    runbook = [
        "# Generated Mission Package", "",
        "**Mission:** " + blueprint.title, "",
        "**Mission ID:** " + blueprint.mission_id, "",
        "## Implementation candidate", "",
        "The file mission_solution.py contains the generated local implementation candidate. CI applies restricted AST checks as defense-in-depth, but static analysis is not a sandbox and cannot guarantee arbitrary Python is safe. CI intentionally does not execute model-generated source. Review the code, add domain-specific tests, and use an appropriately isolated environment before any manual execution.",
        "", "## Planned work", "",
    ]
    runbook.extend("- **" + step.name + "** — " + step.objective for step in blueprint.steps)
    runbook.extend([
        "", "## Deterministic dependency schedule", "",
        "The schedule is calculated from the validated DAG, not generated prose. Independent nodes share a wave; "
        "the critical path uses unit-cost steps and is a planning estimate, not a runtime measurement.", "",
        "- Waves: " + str(len(schedule["waves"])),
        "- Maximum wave parallelism: " + str(schedule["max_parallelism"]),
        "- Critical path length: " + str(schedule["critical_path_length"]),
        "- Critical path: " + " → ".join(schedule["critical_path"]),
    ])
    runbook.extend([
        "", "## Safety boundary", "",
        "The trusted mission runner emits a validation manifest. It does not execute mission_solution.py, deploy services, "
        "contact external systems, or run shell commands supplied by an issue. Static AST checks are defense-in-depth, "
        "not a sandbox or guarantee of safe execution; review and test candidates in an isolated environment.",
        "", "## Inspect generated package", "",
        "<pre><code>python mission_runner.py --output mission_execution.json --print-json</code></pre>",
        "", "Review and test domain-specific behavior before production use.", "",
    ])
    (output / "README.md").write_text("\n".join(runbook), encoding="utf-8")

    compile(runner.read_text(encoding="utf-8"), str(runner), "exec")
    check_path = output / "verification_manifest.json"
    check = subprocess.run(
        [sys.executable, str(runner), "--output", str(check_path)],
        check=False, capture_output=True, text=True, timeout=15,
    )
    if check.returncode != 0:
        raise RuntimeError("Trusted runner verification failed: " + (check.stderr.strip() or check.stdout.strip()))
    manifest = json.loads(check_path.read_text(encoding="utf-8"))
    check_path.unlink()
    if manifest.get("mission_id") != blueprint.mission_id or manifest.get("executed_task_logic") is not False:
        raise ValueError("Trusted runner did not produce a valid inspection manifest")

    verification_report = {
        "schema_version": 1,
        "mission_id": blueprint.mission_id,
        "result": "passed",
        "checks": [
            {"name": "blueprint_schema", "result": "passed", "detail": "Pydantic blueprint validated before build."},
            {"name": "state_identity", "result": "passed", "detail": "Mission ID, issue number, and title match."},
            {"name": "candidate_static_policy", "result": "passed", "detail": "Restricted AST policy accepted the candidate; not a sandbox."},
            {"name": "candidate_syntax", "result": "passed", "detail": "Candidate compiled without execution."},
            {"name": "trusted_runner", "result": "passed", "detail": "Only the trusted inspection runner was executed."},
            {"name": "inspection_manifest", "result": "passed", "detail": "Runner output matches the mission ID and declares task logic unexecuted."},
            {"name": "generated_candidate_execution", "result": "not_run_by_design", "detail": "Model-generated candidate was not executed."},
        ],
    }
    verification_report_path = output / "verification_report.json"
    verification_report_path.write_text(
        json.dumps(verification_report, indent=2, ensure_ascii=False) + "\n",
        encoding="utf-8",
    )

    artifact_paths = [
        Path("blueprint.json"),
        Path("blueprint.md"),
        Path("output/mission_solution.py"),
        Path("output/generator.json"),
        Path("output/mission_runner.py"),
        Path("output/architecture.svg"),
        Path("output/mission.json"),
        Path("output/README.md"),
        Path("output/mission_schedule.json"),
        Path("output/verification_report.json"),
    ]
    artifact_manifest_path = output / "artifact_manifest.json"
    write_artifact_manifest(
        mission_dir, artifact_paths, artifact_manifest_path, blueprint.mission_id
    )
    integrity = verify_artifact_manifest(mission_dir, "output/artifact_manifest.json")
    if not integrity["valid"]:
        raise ValueError("Artifact integrity verification failed: " + "; ".join(integrity["errors"]))

    state.dag = [
        DAGNode(
            id=node.id, name=node.name, description=node.description, dependencies=node.dependencies,
            status="pending" if node.id == "human-review" else "completed", output_path=output.as_posix(),
        )
        for node in state.dag
    ]
    state.status = "ready_for_review"
    state.phase = "builder"
    state.updated_at = utc_now()
    state.artifacts = [
        (mission_dir / "blueprint.json").as_posix(),
        (mission_dir / "blueprint.md").as_posix(),
        solution.as_posix(),
        (output / "generator.json").as_posix(),
        runner.as_posix(),
        svg.as_posix(),
        (output / "mission.json").as_posix(),
        (output / "README.md").as_posix(),
        schedule_path.as_posix(),
        verification_report_path.as_posix(),
        artifact_manifest_path.as_posix(),
    ]
    state_file.write_text(json.dumps(state.model_dump(mode="json"), indent=2) + "\n", encoding="utf-8")
    dashboard = Path(state_path)
    dashboard.parent.mkdir(parents=True, exist_ok=True)
    dashboard.write_text(
        json.dumps(dashboard_snapshot(state), indent=2, ensure_ascii=False) + "\n",
        encoding="utf-8",
    )
    return state


def main() -> int:
    parser = argparse.ArgumentParser(description="Generate a safe, reviewable mission package.")
    parser.add_argument("--issue-number", type=int, default=0)
    parser.add_argument("--output-dir", type=Path, default=Path("generated_artifacts"))
    parser.add_argument("--state-path", type=Path, default=Path("docs/state.json"))
    parser.add_argument("--dry-run", action="store_true", help="Use local deterministic behavior.")
    args = parser.parse_args()
    state = run_builder(
        issue_number=args.issue_number, output_dir=args.output_dir,
        state_path=args.state_path, dry_run=args.dry_run,
    )
    print(json.dumps({
        "mission_id": state.mission_id, "status": state.status,
        "phase": state.phase, "artifacts": state.artifacts,
    }, indent=2))
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
