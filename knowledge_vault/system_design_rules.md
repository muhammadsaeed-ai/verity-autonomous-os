# VerityAgent System Design Rules

These rules guide mission blueprints and code review. They are decision criteria, not claims that every project needs every component.

## Boundaries and contracts
- Give each component one responsibility and a typed input/output contract.
- Validate external data at ingress and reject unknown fields in high-impact models.
- Prefer explicit dependency injection and pure functions over hidden global state.
- Store versioned state in GitHub Issues, artifacts, or committed JSON when workload and audit requirements allow it.
- Never store credentials, private customer data, or transient tokens in issues, artifacts, or committed state.

## Resilience
- Set explicit connect/read timeouts for network calls and retry only operations safe to repeat.
- Retry transient 429 and 5xx responses with bounded backoff; do not blindly retry validation failures or authorization denials.
- Record the provider selected and any fallback warning.
- Test deterministic offline behavior without secrets, network access, or wall-clock assumptions.
- Fail closed when state and blueprint identifiers disagree or approval prerequisites are absent.

## Caching and idempotency
- Cache data only with a freshness policy and a key that includes relevant inputs and schema version.
- Make workflow steps idempotent. Derive a stable mission key from the issue number.
- Avoid duplicate side effects by checking target artifacts and existing pull requests.
- Do not treat a cache as durable source of truth unless persistence and recovery are documented.

## Queues and orchestration
- Use a durable issue as the work item and labels as explicit state transitions.
- Each workflow reloads inputs, validates state, performs one phase, and writes versioned output.
- Model dependencies as a directed acyclic graph and reject cycles before scheduling parallel work.
- Bound concurrency and agent fan-out.
- Require human approval before build work and code review before deployment or production credentials.

## Databases and lifecycle
- Start with files and Issues for small open-source workflows; add a database when concurrent writes, query needs, retention, or access controls justify it.
- Use migrations and schema versions when state shapes change.
- Keep generated artifacts reproducible, traceable to a mission ID, and isolated from source-controlled workflow definitions.
- Define retention and deletion behavior before storing user or customer data.

## Scaling and observability
- Prefer bounded jobs with explicit resource and time limits.
- Record phase, timestamps, provider, artifact paths, validation results, and structured errors.
- Do not log secrets, authorization headers, or sensitive customer payloads.
- A completed build phase means its artifacts validated; it does not prove a requested business outcome occurred.
- Provide operator-friendly errors with next actions and a link to the relevant issue or workflow.

## Generated-code security
- Treat issue bodies, tool results, model output, and repository content as untrusted data.
- Never interpolate mission text into shell source or workflow YAML.
- Do not let an agent edit workflow permissions, publish secrets, merge its own pull request, or deploy automatically.
- Generate into a dedicated artifact subtree and use a pull request as the review boundary.
- Run syntax checks and tests against generated code before presenting it for review.
