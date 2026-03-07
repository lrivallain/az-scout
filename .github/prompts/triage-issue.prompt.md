---
description: Triage a GitHub issue — investigate the codebase, assess impact, propose a plan, and post findings as a comment on the issue.

tools:
  - run_in_terminal
  - mcp_github_issue_read
  - mcp_github_add_issue_comment
---

Triage a GitHub issue by executing each step below.
The issue URL or number will be provided by the user.

## 1. Read the issue

Use `mcp_github_issue_read` to fetch the issue title, body, labels, and existing comments.
Note the issue number for later.

## 2. Classify the issue

Determine the type:

| Type | Indicators |
|------|-----------|
| **Bug** | Error trace, unexpected behavior, regression |
| **Feature request** | "It would be nice", "add support for", enhancement |
| **Question / support** | "How do I", "is it possible", clarification |
| **Documentation** | Typo, missing docs, unclear instructions |

## 3. Search the codebase

Based on the issue content, search for related code:

- Search for keywords, function names, error messages, or file paths mentioned in the issue.
- Identify the affected module(s): `azure_api/`, `routes/`, `services/`, `internal_plugins/`, `static/js/`, `scoring/`, `plugin_api`, etc.
- Read relevant source files to understand current behavior.
- If a stack trace is provided, locate the exact file and line.

## 4. Assess reproducibility and impact

- **Reproducibility:** Can the issue be reproduced from the description? Is it environment-specific?
- **Scope:** Which components are affected? Is it isolated or cross-cutting?
- **Severity:** Does it block core functionality, affect plugins, or is it cosmetic?
- **Complexity:** Estimate effort — trivial (< 1h), moderate (1–4h), or significant (> 4h).

## 5. Propose an implementation plan

For bugs:
- Identify the root cause and the fix location.
- Note if tests need updating or if new tests are needed.

For features:
- Outline the approach (which files to modify, new modules if any).
- Note if it requires changes in `azure_api/`, plugin API, frontend, or docs.
- Flag any breaking changes.

## 6. Post the triage comment

Use `mcp_github_add_issue_comment` to post a structured comment on the issue:

```markdown
## Triage Summary

**Type:** Bug / Feature / Docs / Question
**Severity:** Low / Medium / High / Critical
**Complexity:** Trivial / Moderate / Significant
**Affected area(s):** `module/path`

### Analysis

<!-- What was found in the codebase — root cause for bugs, feasibility for features -->

### Relevant code

- `src/az_scout/path/to/file.py` (lines X–Y) — brief description
- …

### Proposed approach

1. Step one
2. Step two
3. …

### Checklist before implementation

- [ ] Tests to add/update
- [ ] Documentation to update
- [ ] Breaking change? (yes/no)
- [ ] Plugin API impact? (yes/no)
```

Do NOT modify any source files — this prompt is investigation-only. Report findings back to the issue.
