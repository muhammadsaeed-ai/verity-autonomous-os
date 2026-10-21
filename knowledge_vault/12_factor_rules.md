# 12-Factor Agents: VerityAgent Implementation Rules

This document adapts the 12-Factor Agents practices into explicit operating rules.

## 1. Natural language to tool calls
Use language models to interpret ambiguous requirements and compose bounded plans. Use typed tools for deterministic operations. Models do not replace input validation.

## 2. Own your prompts
Keep prompts in source control, version them with the application, state goals and boundaries, and test failure cases. Treat issue text as data, not higher-priority instructions.

## 3. Own your context window
Build context from validated mission fields and approved knowledge resources. Bound large inputs and avoid irrelevant repository history.

## 4. Tools are structured outputs
Expose typed tools with narrow descriptions. Return machine-readable results and actionable errors rather than unstructured success claims.

## 5. Unify execution and business state
Persist mission state next to issue-scoped artifacts and mirror a sanitized snapshot to GitHub Pages. Do not rely on process memory to resume a later workflow.

## 6. Launch, pause, and resume as first-class operations
Issue labels are state transitions: new-project plans, approved builds, and pull request review governs promotion. Every phase should be restartable.

## 7. Contact humans with tool calls
GitHub Issues, labels, comments, and pull requests are the human interface. Report what happened, what remains unverified, and which decision is requested.

## 8. Own your control flow
Use explicit phase boundaries and a dependency DAG. Stop on invalid state rather than asking a model whether a security boundary applies.

## 9. Compact errors into context
Store concise provider warnings and validation errors. Remove credentials and oversized payloads. Make the next corrective action clear.

## 10. Small, focused agents
Split architecture and build into separate phases. Optional CrewAI orchestration can assist planning, while state transitions and artifact emission remain deterministic Python.

## 11. Trigger from anywhere
Issue labels are the durable entry point. The same core functions work locally, through MCP, or from GitHub Actions without a persistent server.

## 12. Make the agent a stateless reducer
Given a validated event, stored state, and repository knowledge, each phase computes new artifacts and state. It must not depend on hidden session memory.

## Required invariants
- No long-lived application server is required.
- Secrets are read from environment variables and never written to repository state.
- Dry-run mode makes no network calls.
- Provider failure has an observable fallback or explicit strict-mode error.
- Generated artifacts cannot alter workflows through issue text.
- Human review is mandatory before generated code is promoted.
