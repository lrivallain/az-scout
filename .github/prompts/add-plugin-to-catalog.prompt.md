---
description: Add a new plugin to the known-plugins list and recommended catalog.

tools:
  - run_in_terminal
  - mcp_github_get_file_contents
---

Add a new external plugin to the az-scout plugin catalog.
The user will provide the plugin name, GitHub URL, and optionally its PyPI status.

## 1. Gather plugin information

Collect:

- **Plugin name** (package name, e.g. `az-scout-plugin-foo`)
- **GitHub URL** (e.g. `https://github.com/owner/az-scout-plugin-foo`)
- **Short description** (one line)
- **Source:** `pypi` if published on PyPI, otherwise `github`
- **PyPI URL** (if applicable): check with `https://pypi.org/project/<name>/`

If only a GitHub URL is provided, use `mcp_github_get_file_contents` to read the plugin's
`pyproject.toml` and `README.md` to extract the package name and description.

Verify the plugin is a valid az-scout plugin by checking that its `pyproject.toml` declares
an `az_scout.plugins` entry point.

## 2. Update known-plugins table

Edit `docs/_includes/known-plugins.md` — this file is included by:

- `docs/plugins/index.md`
- `docs/plugins/catalog.md`
- `docs/features.md`
- `docs/index.md`

Add a new row in the table, maintaining alphabetical order by plugin name:

```markdown
| [az-scout-plugin-foo](https://github.com/owner/az-scout-plugin-foo) | Short description |
```

## 3. Update recommended plugins JSON

Edit `src/az_scout/recommended_plugins.json` — this powers the Plugin Manager UI's
"Recommended" section.

Add a new entry to the JSON array:

For PyPI-published plugins:
```json
{
  "name": "az-scout-plugin-foo",
  "description": "Short description.",
  "source": "pypi"
}
```

For GitHub-only plugins (not on PyPI):
```json
{
  "name": "az-scout-plugin-foo",
  "description": "Short description.",
  "source": "github",
  "url": "https://github.com/owner/az-scout-plugin-foo"
}
```

## 5. Verify

Run a quick check to ensure no JSON syntax errors:

```bash
python3 -c "import json; json.load(open('src/az_scout/recommended_plugins.json'))"
```

And verify the markdown table renders correctly by inspecting `docs/_includes/known-plugins.md`.

## 6. Summary

Report what was updated:

- `docs/_includes/known-plugins.md` — new table row
- `src/az_scout/recommended_plugins.json` — new catalog entry

Update `CHANGELOG.md` under `## Unreleased` with the new plugin addition.
