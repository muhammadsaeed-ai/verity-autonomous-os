#!/usr/bin/env node
/**
 * Runs real issue -> Architect -> approval label -> Builder -> draft PR scenarios
 * against a dedicated disposable repository. Never targets this source repository.
 *
 * Required environment:
 *   GH_TOKEN: fine-grained token restricted to the sandbox repository
 *   TARGET_REPOSITORY: owner/name of the sandbox copy with workflows enabled
 *   GITHUB_REPOSITORY: source repository, used to prohibit self-targeting
 *   SCENARIO: architect_only | full_pipeline
 */
const token = process.env.GH_TOKEN?.trim();
const target = process.env.TARGET_REPOSITORY?.trim();
const source = process.env.GITHUB_REPOSITORY?.trim();
const scenario = process.env.SCENARIO || "full_pipeline";

if (!token) throw new Error("Missing GH_TOKEN secret.");
if (!target || !/^[A-Za-z0-9_.-]+\/[A-Za-z0-9_.-]+$/.test(target)) {
  throw new Error("TARGET_REPOSITORY must be an owner/name repository variable.");
}
if (!source || target.toLowerCase() === source.toLowerCase()) {
  throw new Error("Refusing to target the source repository. Configure a separate disposable sandbox.");
}
if (!["architect_only", "full_pipeline"].includes(scenario)) {
  throw new Error("SCENARIO must be architect_only or full_pipeline.");
}

const apiRoot = `https://api.github.com/repos/${target}`;
const trustedAssociations = new Set(["OWNER", "MEMBER", "COLLABORATOR"]);
const runStart = new Date(Date.now() - 60_000).toISOString();
const createdIssues = [];
let createdPull = null;
let succeeded = false;

async function api(path, method = "GET", body = undefined) {
  const response = await fetch(`${apiRoot}/${path}`, {
    method,
    headers: {
      Accept: "application/vnd.github+json",
      Authorization: `Bearer ${token}`,
      "X-GitHub-Api-Version": "2022-11-28",
      ...(body === undefined ? {} : { "Content-Type": "application/json" }),
    },
    ...(body === undefined ? {} : { body: JSON.stringify(body) }),
  });
  const raw = await response.text();
  if (!response.ok) {
    throw new Error(`GitHub API ${method} ${path} returned ${response.status}: ${raw.slice(0, 900)}`);
  }
  return raw ? JSON.parse(raw) : null;
}

const sleep = (ms) => new Promise((resolve) => setTimeout(resolve, ms));

async function latestIssueWorkflowRun(workflow, issueNumber) {
  try {
    const data = await api(`actions/workflows/${workflow}/runs?event=issues&per_page=20`);
    return (data.workflow_runs || [])
      .filter((run) => run.created_at >= runStart && String(run.display_title || "").includes(`#${issueNumber}`))
      .sort((left, right) => right.run_number - left.run_number)[0] || null;
  } catch (error) {
    // Actions read permission is needed for richer diagnostics; issue/output checks
    // remain authoritative if the token only has the minimum issue/content scopes.
    console.warn(`Unable to inspect Actions run metadata for ${workflow}: ${error.message}`);
    return null;
  }
}

async function waitForBlueprint(issue, timeoutMs = 16 * 60 * 1000) {
  const deadline = Date.now() + timeoutMs;
  while (Date.now() < deadline) {
    const current = await api(`issues/${issue.number}`);
    if ((current.labels || []).some((label) => label.name === "blueprint-ready")) {
      const path = `contents/generated_artifacts/missions/issue-${issue.number}/blueprint.json?ref=main`;
      const file = await api(path);
      const blueprint = JSON.parse(Buffer.from(file.content.replace(/\n/g, ""), "base64").toString("utf8"));
      const matchesIssue = Number(blueprint.issue_number) === issue.number || blueprint.mission_id === `issue-${issue.number}`;
      if (!matchesIssue) {
        throw new Error(`Blueprint identity mismatch for issue #${issue.number}.`);
      }
      return { current, blueprintPath: path.split("?")[0], blueprint };
    }
    const run = await latestIssueWorkflowRun("01_architect_plan.yml", issue.number);
    if (run?.status === "completed" && run.conclusion !== "success") {
      throw new Error(`Architect workflow failed: ${run.html_url} (${run.conclusion}).`);
    }
    await sleep(5000);
  }
  const run = await latestIssueWorkflowRun("01_architect_plan.yml", issue.number);
  throw new Error(`Timed out waiting for the blueprint-ready label. Latest Architect run: ${run?.html_url || "not found"}`);
}

async function waitForBuildPullRequest(issue, timeoutMs = 22 * 60 * 1000) {
  const deadline = Date.now() + timeoutMs;
  while (Date.now() < deadline) {
    const pulls = await api("pulls?state=open&per_page=100");
    const found = pulls.find((pull) =>
      String(pull.title || "").includes(`#${issue.number}`) &&
      String(pull.body || "").includes(`Source mission: #${issue.number}`)
    );
    if (found) return found;
    const run = await latestIssueWorkflowRun("02_execute_build.yml", issue.number);
    if (run?.status === "completed" && run.conclusion !== "success") {
      throw new Error(`Builder workflow failed: ${run.html_url} (${run.conclusion}).`);
    }
    await sleep(5000);
  }
  const run = await latestIssueWorkflowRun("02_execute_build.yml", issue.number);
  throw new Error(`Timed out waiting for the review PR. Latest Builder run: ${run?.html_url || "not found"}`);
}

try {
  const repository = await api("");
  const defaultBranch = repository.default_branch || "main";
  if (repository.archived) throw new Error("The configured sandbox repository is archived.");

  // Confirm that the sandbox contains the two privileged issue workflows. This
  // integration run exercises the deployed workflow copy, not just local YAML.
  for (const workflow of ["01_architect_plan.yml", "02_execute_build.yml"]) {
    await api(`contents/.github/workflows/${workflow}?ref=${encodeURIComponent(defaultBranch)}`);
  }

  const runTag = `${new Date().toISOString()} / Actions run ${process.env.GITHUB_RUN_ID || "manual"}`;
  const issue = await api("issues", "POST", {
    title: `[Mission] isolated integration ${Date.now()}`,
    body: [
      "## Objective",
      "Verify the isolated VerityAgent Architect and Builder workflow integration.",
      "",
      "## Requirements",
      "- Use deterministic fallback when no provider key is configured.",
      "- Produce a typed, reviewable mission artifact bundle.",
      "- Do not execute generated source or deploy anything.",
      "",
      "## Acceptance criteria",
      "- Architect commits a blueprint and adds blueprint-ready.",
      "- Full-pipeline mode opens an unmerged pull request after approved is applied.",
      "",
      "## Constraints",
      "- Sandbox repository only; no external messages, no production changes, and no secrets in mission data.",
      "- Human review is required; the integration script never merges the pull request.",
      "",
      `Integration marker: ${runTag}`,
    ].join("\n"),
  });
  createdIssues.push(issue);
  console.log(`Created sandbox mission: ${issue.html_url}`);
  console.log(`Reported author association: ${issue.author_association}`);

  if (!trustedAssociations.has(issue.author_association)) {
    throw new Error(
      `Test token created the issue with author_association=${issue.author_association}; ` +
      "the real Architect workflow intentionally only accepts OWNER/MEMBER/COLLABORATOR issue authors. " +
      "Grant the token-owning account collaborator access to the sandbox and rerun."
    );
  }

  const { blueprintPath, blueprint } = await waitForBlueprint(issue);
  console.log(JSON.stringify({
    phase: "architect",
    status: "passed",
    issue_url: issue.html_url,
    blueprint_path: blueprintPath,
    mission_id: blueprint.mission_id,
  }));

  let pullRequestUrl = null;
  if (scenario === "full_pipeline") {
    // Explicitly approve only the just-created sandbox issue. The normal target
    // workflow still evaluates its actual GitHub event sender/author association.
    await api(`issues/${issue.number}/labels`, "POST", { labels: ["approved"] });
    createdPull = await waitForBuildPullRequest(issue);
    pullRequestUrl = createdPull.html_url;
    if (createdPull.merged_at || createdPull.state !== "open") {
      throw new Error("Builder result must be an open, unmerged pull request.");
    }
    console.log(JSON.stringify({
      phase: "builder",
      status: "passed",
      issue_url: issue.html_url,
      pull_request_url: pullRequestUrl,
      head_branch: createdPull.head?.ref,
      merged: Boolean(createdPull.merged_at),
    }));
  }

  // Cleanup only after every requested assertion passes. On failure the open
  // issue is deliberately left for human debugging; it is always in the sandbox.
  if (createdPull) {
    await api(`pulls/${createdPull.number}`, "PATCH", { state: "closed" });
  }
  await api(`issues/${issue.number}`, "PATCH", { state: "closed" });
  succeeded = true;
  console.log(JSON.stringify({
    status: "passed",
    scenario,
    issue_url: issue.html_url,
    blueprint_path: blueprintPath,
    pull_request_url: pullRequestUrl,
    sandbox: target,
    note: "The generated mission commit and workflow run history remain in the disposable sandbox.",
  }, null, 2));
} catch (error) {
  if (createdIssues[0]) console.error(`Debug the failed scenario in sandbox issue: ${createdIssues[0].html_url}`);
  console.error(error instanceof Error ? error.stack || error.message : String(error));
  process.exitCode = 1;
}
