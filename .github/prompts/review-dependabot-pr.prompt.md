---
description: Review and validate a Dependabot PR — pull, install, smoke-test affected areas, summarize findings, approve or request changes.
---

Review a Dependabot PR by executing each step. The PR number or URL is provided by the user.

## 1. Read the PR

- `mcp_github_pull_request_read` for the PR.
- Note the dependency name, old version → new version, ecosystem (pip, github-actions, docker).
- Read the dependency's CHANGELOG / release notes for the version range bumped.
- Identify whether it's a **patch**, **minor**, or **major** bump.

## 2. Classify risk

| Risk | Indicators |
|------|-----------|
| **Low** | Patch bump of a runtime dep with no API surface change |
| **Medium** | Minor bump, or any bump of an auth / crypto / network library |
| **High** | Major bump, removed APIs, security advisory referenced |

Auth-adjacent or crypto deps to flag at minimum **Medium**:
`cryptography`, `pyjwt`, `azure-identity`, `msal`, `requests`, `urllib3`,
`fastapi`, `starlette`, `uvicorn`.

## 3. Check out and install

```bash
gh pr checkout <number>
uv sync --frozen
```

If the lockfile changed, also run `uv lock --check`.

## 4. Run the quality gate

```bash
uv run ruff check src/ tests/
uv run ruff format --check src/ tests/
uv run mypy src/
uv run pytest
```

All four must pass. Report any failure with a stack trace excerpt.

## 5. Targeted smoke tests by dependency category

Pick the slice that matches the bumped library:

- **`cryptography`, `pyjwt`** — session cookie HMAC + OBO token decode:
  - Run `uv run pytest tests/test_obo.py -q`
  - If OBO env vars are set locally, exercise `/auth/login/start` → `/auth/callback` once.
- **`azure-identity`, `msal`, `requests`, `urllib3`** — ARM call path:
  - Run `uv run pytest tests/test_azure_api.py -q`
  - Hit one read-only endpoint manually (e.g. `/api/regions?subscriptionId=…`).
- **`fastapi`, `starlette`, `uvicorn`** — startup + middleware:
  - Run the workspace task `Backend: run`, hit `/` and `/healthz`, watch logs for warnings.
- **`mcp[cli]`** — MCP server:
  - `uv run az-scout mcp --stdio` and issue a `tools/list` request via your MCP client.
- **GitHub Actions / Docker bumps** — read the workflow / Dockerfile diff and confirm CI is green on the PR.

## 6. Decide and act

- All checks pass + low risk → approve via `mcp_github_pull_request_review_write` with `event: APPROVE`.
- Medium/high risk → leave a `COMMENT` review summarizing what you tested and what was *not* covered, then ask the maintainer to confirm before merge.
- Anything failed → `REQUEST_CHANGES` with the failure excerpt and a suggested next step (pin, skip, or report upstream).

## 7. Cleanup

```bash
git checkout main && git branch -D <pr-branch>
```
