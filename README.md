<div align="center">

<img src="docs/assets/architecture.svg" alt="VerityAgent Autonomous OS architecture" width="100%">

# VerityAgent Autonomous OS

**A GitHub-native mission operating system for planning, building, verifying, and reviewing AI-assisted engineering work.**

[![CI](https://github.com/muhammadsaeed-ai/verity-autonomous-os/actions/workflows/04_ci_verification.yml/badge.svg)](https://github.com/muhammadsaeed-ai/verity-autonomous-os/actions/workflows/04_ci_verification.yml)
[![Python 3.11+](https://img.shields.io/badge/Python-3.11%2B-3776AB?logo=python&logoColor=white)](https://www.python.org/)
[![FastMCP](https://img.shields.io/badge/MCP-stdio-0F766E)](https://modelcontextprotocol.io/)
[![Pydantic v2](https://img.shields.io/badge/Schema-Pydantic%20v2-E92063)](https://docs.pydantic.dev/)
[![License: MIT](https://img.shields.io/badge/License-MIT-yellow.svg)](LICENSE)

</div>

VerityAgent Autonomous OS converts a structured GitHub Issue into a validated mission blueprint, persists auditable state, generates a bounded artifact package, runs deterministic verification, and opens a pull request for human review. GitHub Actions is the execution plane; GitHub Issues provide the human state machine; GitHub Pages serves a zero-build command center.

> **Engineering contract:** a successful build verifies the generated package; it does not prove the requested business outcome has happened. No persistent application server is required, and generated code is not automatically merged or deployed.

## System map

![Mission pipeline](docs/assets/architecture.svg)

| Layer | Responsibility |
|---|---|
| Mission intake | A structured Issue captures objective, requirements, acceptance criteria, and constraints. |
| Architect | Validates inputs, creates a Pydantic blueprint, writes issue-scoped state, and publishes a dependency DAG. |
| Human gate | A maintainer reviews the blueprint and applies the approved label. |
| Builder | Emits a runnable inspection runner, JSON manifest, SVG dependency map, and package runbook under generated_artifacts. |
| Verification | Pytest, Python compilation, manifest checks, YAML parsing, and MCP stdio handshake tests verify the core pipeline. |
| Command center | docs/index.html renders state from docs/state.json without a frontend build step. |
| Model routing | Gemini, then OpenAI, then deterministic fallback; offline mode makes no network calls. |

## Fast start

Requires Python 3.11 or newer and [uv](https://docs.astral.sh/uv/).

<pre><code>git clone https://github.com/muhammadsaeed-ai/verity-autonomous-os.git
cd verity-autonomous-os
uv sync --group dev
uv run pytest -q</code></pre>

Run the deterministic pipeline locally. These commands require no API key and do not contact external LLM providers.

<pre><code>uv run verity-architect --issue-number 0 --title "Internal reporting mission" --body "Generate a reviewable reporting package." --output-dir /tmp/verity-artifacts --state-path /tmp/verity-state.json --dry-run
uv run verity-builder --issue-number 0 --output-dir /tmp/verity-artifacts --state-path /tmp/verity-state.json --dry-run</code></pre>

The builder writes an inspection runner to the mission artifact directory. Running the generated package emits a manifest; the starter intentionally does not execute mission-specific external side effects.

## Model providers

Copy .env.example to your local environment and export variables in your shell or configure repository secrets. Never commit API keys.

| Priority | Environment variable | Behavior |
|---|---|---|
| 1 | GEMINI_API_KEY | Uses Gemini generate-content when configured. |
| 2 | OPENAI_API_KEY | Tries OpenAI Chat Completions if Gemini is absent or fails. |
| 3 | No key required | Uses deterministic, repeatable planning notes and remains testable offline. |

Set VERITY_OFFLINE=1 to guarantee no provider network call. Set VERITY_STRICT_LLM=1 to raise when a configured provider fails rather than falling back. The optional agency extra enables CrewAI orchestration:

<pre><code>uv sync --extra agency --group dev
export VERITY_USE_CREWAI=1</code></pre>

Core schemas, state transitions, verification, and artifact generation do not depend on CrewAI being available.

## GitHub-native operating procedure

1. Open a mission using the New VerityAgent Mission Issue form, or apply the new-project label to an existing issue.
2. Workflow 01 validates the issue and commits blueprint.json, blueprint.md, and issue-specific state. It adds the blueprint-ready label.
3. Review the blueprint and acceptance criteria. Apply approved only after the plan is acceptable.
4. Workflow 02 builds a bounded artifact package, runs compilation and tests, and opens a mission-specific pull request.
5. Review the generated package, implement domain-specific behavior, and merge manually only after your checks pass.

Workflow 03 runs scheduled maintenance checks and opens an issue on failure. Workflow 04 runs on pushes, pull requests, and manual dispatch. GEMINI_API_KEY and OPENAI_API_KEY are optional repository secrets; deterministic checks do not need them.

## FastMCP tools and resources

Start the server over stdio:

<pre><code>uv run verity-mcp</code></pre>

Tools:
- mission_validate — validate bounded mission fields and acceptance criteria.
- public_api_search — query the local registry of unauthenticated public API examples.
- mission_blueprint — create a validated deterministic blueprint.
- mission_state — read repository-local state while rejecting path traversal.
- svg_architecture_spec — create an SVG flow preview from node names.

Resources:
- vault://system-design
- vault://12-factor

The tool surface is deliberately narrow. It does not expose arbitrary shell execution, direct file writes, deployment, or an unrestricted network proxy.

## Repository map

<pre><code>.github/
  ISSUE_TEMPLATE/new_mission.yml
  workflows/
    01_architect_plan.yml
    02_execute_build.yml
    03_daily_auto_agent.yml
    04_ci_verification.yml
knowledge_vault/
  system_design_rules.md
  12_factor_rules.md
  public_apis_min.json
mcp_and_crews/
  schemas.py
  llm_factory.py
  mcp_stdio_server.py
  run_architect.py
  run_builder.py
generated_artifacts/
  .gitkeep
docs/
  index.html
  state.json
  assets/architecture.svg
tests/
  test_knowledge_vault.py
  test_mcp_server.py
  test_pipeline_e2e.py
pyproject.toml
.env.example</code></pre>

## Verification model

The CI suite checks the public API registry, knowledge base, issue form and workflow YAML, dashboard JSON, five MCP tools, repository path restrictions, SVG escaping, and a real stdio initialize/tools-list handshake. It also runs Architect → Builder end-to-end in deterministic mode, compiles generated Python, and executes the generated manifest runner.

The latest hosted GitHub Actions result remains the source of truth for a commit. A successful pipeline means package generation and verification passed; it is not a guarantee of third-party API uptime or mission-specific production logic.

## Security and operational boundaries

- Issue text, model output, tool results, and API responses are untrusted data.
- Generated mission output stays under the issue-scoped generated_artifacts/missions directory.
- The builder does not run issue-supplied shell commands or edit workflows.
- Provider failures are recorded as warnings unless strict mode is enabled.
- State does not contain provider credentials.
- Pull-request review remains the promotion boundary. There is no automatic deployment or self-merge.

## Built by VerityAgent

VerityAgent builds practical AI agents and automation systems. This repository focuses on making agent-assisted engineering auditable, testable, and review-first rather than hiding work behind an opaque autonomous loop.
