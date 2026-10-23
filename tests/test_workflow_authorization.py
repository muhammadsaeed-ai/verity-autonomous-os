"""Policy truth table and safety boundaries for privileged issue workflows."""

from __future__ import annotations

from pathlib import Path

import yaml

ROOT = Path(__file__).resolve().parents[1]
TRUSTED = {"OWNER", "MEMBER", "COLLABORATOR"}


def architect_allowed(
    *,
    action: str,
    label: str | None,
    sender: str,
    owner: str,
    author_association: str,
    title: str,
) -> bool:
    """Mirror the deliberately narrow GitHub Actions job guard for truth-table tests."""
    trusted = author_association in TRUSTED
    return (
        action == "labeled"
        and label == "new-project"
        and (sender == owner or trusted)
    ) or (
        action == "opened"
        and title.startswith("[Mission]")
        and trusted
    )


def builder_allowed(
    *,
    label: str,
    sender: str,
    owner: str,
    author_association: str,
) -> bool:
    """Only approved labels on trusted-author issues or owner-authorized issues run the builder."""
    return label == "approved" and (
        sender == owner or author_association in TRUSTED
    )


def _workflow(path: str) -> dict:
    return yaml.load(Path(ROOT / path).read_text(encoding="utf-8"), Loader=yaml.BaseLoader)


def test_architect_authorization_matrix() -> None:
    cases = [
        # action, label, sender, association, title, expected
        ("opened", None, "owner", "OWNER", "[Mission] trusted", True),
        ("opened", None, "member", "MEMBER", "[Mission] trusted", True),
        ("opened", None, "collaborator", "COLLABORATOR", "[Mission] trusted", True),
        ("opened", None, "outsider", "FIRST_TIMER", "[Mission] public request", False),
        ("opened", None, "member", "MEMBER", "ordinary issue title", False),
        ("labeled", "new-project", "owner", "FIRST_TIMER", "public request", True),
        ("labeled", "new-project", "random-user", "FIRST_TIMER", "public request", False),
        ("labeled", "approved", "owner", "FIRST_TIMER", "public request", False),
        ("labeled", "new-project", "member", "MEMBER", "public request", True),
        ("labeled", "other-label", "owner", "OWNER", "[Mission] title", False),
    ]
    for action, label, sender, association, title, expected in cases:
        assert architect_allowed(
            action=action, label=label, sender=sender, owner="owner",
            author_association=association, title=title,
        ) is expected, (action, label, sender, association, title)


def test_builder_authorization_matrix() -> None:
    cases = [
        ("approved", "owner", "FIRST_TIMER", True),
        ("approved", "member", "MEMBER", True),
        ("approved", "collaborator", "COLLABORATOR", True),
        # The person applying the label must be the owner to explicitly authorize an outsider.
        ("approved", "member", "FIRST_TIMER", False),
        ("new-project", "owner", "OWNER", False),
        ("approved", "outsider", "FIRST_TIMER", False),
        ("approved", "owner", "OWNER", True),
    ]
    for label, sender, association, expected in cases:
        assert builder_allowed(
            label=label, sender=sender, owner="owner", author_association=association
        ) is expected, (label, sender, association)


def test_authorization_truth_table_matches_privileged_workflow_conditions() -> None:
    architect = _workflow(".github/workflows/01_architect_plan.yml")
    builder = _workflow(".github/workflows/02_execute_build.yml")
    architect_guard = architect["jobs"]["architect"]["if"]
    builder_guard = builder["jobs"]["build"]["if"]

    assert "github.event.action == 'opened'" in architect_guard
    assert "startsWith(github.event.issue.title, '[Mission]')" in architect_guard
    assert "github.event.label.name == 'new-project'" in architect_guard
    assert "github.event.label.name == 'approved'" in builder_guard
    for guard in (architect_guard, builder_guard):
        assert "github.event.sender.login == github.repository_owner" in guard
        assert "github.event.issue.author_association" in guard
        for association in TRUSTED:
            assert association in guard


def test_live_integration_harness_is_manual_owner_only_and_sandbox_scoped() -> None:
    path = ROOT / ".github/workflows/05_privileged_integration.yml"
    document = yaml.load(path.read_text(encoding="utf-8"), Loader=yaml.BaseLoader)
    assert set(document["on"]) == {"workflow_dispatch"}
    guard = document["jobs"]["integration"]["if"]
    assert "github.ref == 'refs/heads/main'" in guard
    assert "github.actor == github.repository_owner" in guard
    assert document["jobs"]["integration"]["permissions"]["contents"] == "read"
    env = document["jobs"]["integration"]["env"]
    assert env["GH_TOKEN"] == "${{ secrets.VERITY_AUTONOMOUS_OS_TEST_TOKEN }}"
    assert env["TARGET_REPOSITORY"] == "${{ vars.VERITY_AUTONOMOUS_OS_TEST_REPO }}"
    all_workflows = "\n".join(
        file.read_text(encoding="utf-8") for file in (ROOT / ".github/workflows").glob("*.yml")
    )
    assert "pull_request_target:" not in all_workflows
    assert "scripts/privileged_workflow_integration.mjs" in path.read_text(encoding="utf-8")


def test_integration_script_refuses_to_target_its_own_repository() -> None:
    script = (ROOT / "scripts/privileged_workflow_integration.mjs").read_text(encoding="utf-8")
    assert "target.toLowerCase() === source.toLowerCase()" in script
    assert "OWNER" in script and "MEMBER" in script and "COLLABORATOR" in script
    assert "target.toLowerCase() === source.toLowerCase()" in script
    assert "state: " + '"closed"' in script
    assert "merged_at" in script
    assert "process.exitCode = 1" in script
