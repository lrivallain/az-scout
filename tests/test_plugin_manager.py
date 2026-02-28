"""Tests for the plugin manager (business logic + API routes)."""

import json
import textwrap
from pathlib import Path
from unittest.mock import MagicMock, patch

from az_scout import plugin_manager
from az_scout.plugin_manager import (
    GitHubRepo,
    InstalledPluginRecord,
    PluginValidationResult,
    is_commit_sha,
    load_installed,
    parse_github_repo_url,
    save_installed,
    validate_plugin_repo,
)

# ---------------------------------------------------------------------------
# Sample data
# ---------------------------------------------------------------------------

VALID_PYPROJECT = textwrap.dedent("""\
    [build-system]
    requires = ["hatchling"]
    build-backend = "hatchling.build"

    [project]
    name = "az-scout-example"
    version = "0.1.0"
    requires-python = ">=3.11"
    dependencies = ["az-scout", "fastapi"]

    [project.entry-points."az_scout.plugins"]
    example = "az_scout_example:plugin"
""")

PYPROJECT_MISSING_EP = textwrap.dedent("""\
    [project]
    name = "az-scout-bad"
    version = "0.1.0"
    requires-python = ">=3.11"
    dependencies = ["az-scout"]
""")

PYPROJECT_BAD_EP_FORMAT = textwrap.dedent("""\
    [project]
    name = "az-scout-bad"
    version = "0.1.0"
    requires-python = ">=3.11"
    dependencies = ["az-scout"]

    [project.entry-points."az_scout.plugins"]
    bad = "no_colon_here"
""")

SAMPLE_SHA = "a" * 40


# ---------------------------------------------------------------------------
# Unit tests – parse / helpers
# ---------------------------------------------------------------------------


class TestParseGitHubRepoUrl:
    def test_valid_url(self) -> None:
        result = parse_github_repo_url("https://github.com/owner/repo")
        assert result == GitHubRepo(owner="owner", repo="repo")

    def test_valid_url_trailing_slash(self) -> None:
        result = parse_github_repo_url("https://github.com/owner/repo/")
        assert result == GitHubRepo(owner="owner", repo="repo")

    def test_invalid_url_wrong_domain(self) -> None:
        assert parse_github_repo_url("https://gitlab.com/owner/repo") is None

    def test_invalid_url_missing_repo(self) -> None:
        assert parse_github_repo_url("https://github.com/owner") is None

    def test_invalid_url_random_string(self) -> None:
        assert parse_github_repo_url("not-a-url") is None


class TestIsCommitSha:
    def test_valid_sha(self) -> None:
        assert is_commit_sha("a" * 40) is True

    def test_short_sha(self) -> None:
        assert is_commit_sha("a" * 7) is False

    def test_uppercase(self) -> None:
        assert is_commit_sha("A" * 40) is False

    def test_tag(self) -> None:
        assert is_commit_sha("v1.0.0") is False


# ---------------------------------------------------------------------------
# Validation tests (mocked GitHub)
# ---------------------------------------------------------------------------


class TestValidatePluginRepo:
    def test_invalid_url(self) -> None:
        result = validate_plugin_repo("https://gitlab.com/x/y", "v1.0")
        assert not result.ok
        assert any("Invalid GitHub URL" in e for e in result.errors)

    def test_valid_repo(self) -> None:
        mock_ref_resp = MagicMock()
        mock_ref_resp.status_code = 200
        mock_ref_resp.raise_for_status = MagicMock()
        mock_ref_resp.json.return_value = {
            "object": {"sha": SAMPLE_SHA, "type": "commit"},
        }

        mock_raw_resp = MagicMock()
        mock_raw_resp.raise_for_status = MagicMock()
        mock_raw_resp.text = VALID_PYPROJECT

        def side_effect(url: str, **_: object) -> MagicMock:
            if "raw.githubusercontent.com" in url:
                return mock_raw_resp
            return mock_ref_resp

        with patch("az_scout.plugin_manager.requests.get", side_effect=side_effect):
            result = validate_plugin_repo("https://github.com/owner/repo", "v1.0.0")

        assert result.ok
        assert result.distribution_name == "az-scout-example"
        assert result.resolved_sha == SAMPLE_SHA
        assert "example" in result.entry_points
        assert result.entry_points["example"] == "az_scout_example:plugin"

    def test_missing_entry_points(self) -> None:
        mock_ref_resp = MagicMock()
        mock_ref_resp.status_code = 200
        mock_ref_resp.raise_for_status = MagicMock()
        mock_ref_resp.json.return_value = {
            "object": {"sha": SAMPLE_SHA, "type": "commit"},
        }

        mock_raw_resp = MagicMock()
        mock_raw_resp.raise_for_status = MagicMock()
        mock_raw_resp.text = PYPROJECT_MISSING_EP

        def side_effect(url: str, **_: object) -> MagicMock:
            if "raw.githubusercontent.com" in url:
                return mock_raw_resp
            return mock_ref_resp

        with patch("az_scout.plugin_manager.requests.get", side_effect=side_effect):
            result = validate_plugin_repo("https://github.com/owner/repo", SAMPLE_SHA)

        assert not result.ok
        assert any("entry-points" in e for e in result.errors)

    def test_bad_entry_point_format(self) -> None:
        mock_ref_resp = MagicMock()
        mock_ref_resp.status_code = 200
        mock_ref_resp.raise_for_status = MagicMock()
        mock_ref_resp.json.return_value = {
            "object": {"sha": SAMPLE_SHA, "type": "commit"},
        }

        mock_raw_resp = MagicMock()
        mock_raw_resp.raise_for_status = MagicMock()
        mock_raw_resp.text = PYPROJECT_BAD_EP_FORMAT

        def side_effect(url: str, **_: object) -> MagicMock:
            if "raw.githubusercontent.com" in url:
                return mock_raw_resp
            return mock_ref_resp

        with patch("az_scout.plugin_manager.requests.get", side_effect=side_effect):
            result = validate_plugin_repo("https://github.com/owner/repo", SAMPLE_SHA)

        assert not result.ok
        assert any("module:object" in e for e in result.errors)


# ---------------------------------------------------------------------------
# Persistence tests (tmp_path)
# ---------------------------------------------------------------------------


class TestPersistence:
    def test_load_empty(self, tmp_path: Path) -> None:
        with patch.object(plugin_manager, "_INSTALLED_FILE", tmp_path / "none.json"):
            assert load_installed() == []

    def test_save_and_load(self, tmp_path: Path) -> None:
        installed_file = tmp_path / "installed.json"
        data_dir = tmp_path
        record = InstalledPluginRecord(
            distribution_name="az-scout-example",
            repo_url="https://github.com/owner/repo",
            ref="v1.0.0",
            resolved_sha=SAMPLE_SHA,
            entry_points={"example": "mod:obj"},
            installed_at="2026-02-28T00:00:00+00:00",
            actor="tester",
        )
        with (
            patch.object(plugin_manager, "_INSTALLED_FILE", installed_file),
            patch.object(plugin_manager, "_DATA_DIR", data_dir),
        ):
            save_installed([record])
            loaded = load_installed()

        assert len(loaded) == 1
        assert loaded[0].distribution_name == "az-scout-example"
        assert loaded[0].resolved_sha == SAMPLE_SHA

    def test_audit_appends(self, tmp_path: Path) -> None:
        audit_file = tmp_path / "audit.jsonl"
        with (
            patch.object(plugin_manager, "_AUDIT_FILE", audit_file),
            patch.object(plugin_manager, "_DATA_DIR", tmp_path),
        ):
            plugin_manager.append_audit({"action": "test1"})
            plugin_manager.append_audit({"action": "test2"})

        lines = audit_file.read_text(encoding="utf-8").strip().split("\n")
        assert len(lines) == 2
        assert json.loads(lines[0])["action"] == "test1"
        assert json.loads(lines[1])["action"] == "test2"
        # Each line has a timestamp
        assert "timestamp" in json.loads(lines[0])


# ---------------------------------------------------------------------------
# Route tests
# ---------------------------------------------------------------------------


class TestPluginRoutes:
    def test_list_plugins(self, client) -> None:  # type: ignore[no-untyped-def]
        with (
            patch.object(plugin_manager, "load_installed", return_value=[]),
        ):
            resp = client.get("/api/plugins")

        assert resp.status_code == 200
        data = resp.json()
        assert "installed" in data
        assert "loaded" in data

    def test_validate_valid(self, client) -> None:  # type: ignore[no-untyped-def]
        result = PluginValidationResult(
            ok=True,
            owner="owner",
            repo="repo",
            repo_url="https://github.com/owner/repo",
            ref="v1.0.0",
            resolved_sha=SAMPLE_SHA,
            distribution_name="az-scout-example",
            entry_points={"example": "mod:obj"},
        )

        with patch(
            "az_scout.routes.plugin_manager.validate_plugin_repo",
            return_value=result,
        ):
            resp = client.post(
                "/api/plugins/validate",
                json={"repo_url": "https://github.com/owner/repo", "ref": "v1.0.0"},
            )

        assert resp.status_code == 200
        data = resp.json()
        assert data["ok"] is True
        assert data["distribution_name"] == "az-scout-example"

    def test_validate_invalid_url(self, client) -> None:  # type: ignore[no-untyped-def]
        result = PluginValidationResult(
            ok=False,
            owner="",
            repo="",
            repo_url="https://gitlab.com/x/y",
            ref="v1",
            errors=["Invalid GitHub URL"],
        )
        with patch(
            "az_scout.routes.plugin_manager.validate_plugin_repo",
            return_value=result,
        ):
            resp = client.post(
                "/api/plugins/validate",
                json={"repo_url": "https://gitlab.com/x/y", "ref": "v1"},
            )

        assert resp.status_code == 200
        data = resp.json()
        assert not data["ok"]
        assert len(data["errors"]) > 0

    def test_install_success(self, client) -> None:  # type: ignore[no-untyped-def]
        with patch(
            "az_scout.routes.plugin_manager.install_plugin",
            return_value=(True, [], []),
        ):
            resp = client.post(
                "/api/plugins/install",
                json={"repo_url": "https://github.com/owner/repo", "ref": "v1.0.0"},
            )

        assert resp.status_code == 200
        data = resp.json()
        assert data["ok"] is True
        assert data["restart_required"] is True

    def test_install_failure(self, client) -> None:  # type: ignore[no-untyped-def]
        with patch(
            "az_scout.routes.plugin_manager.install_plugin",
            return_value=(False, [], ["install failed"]),
        ):
            resp = client.post(
                "/api/plugins/install",
                json={"repo_url": "https://github.com/owner/repo", "ref": "bad"},
            )

        assert resp.status_code == 400
        data = resp.json()
        assert data["ok"] is False
        assert "install failed" in data["errors"]

    def test_uninstall_success(self, client) -> None:  # type: ignore[no-untyped-def]
        with patch(
            "az_scout.routes.plugin_manager.uninstall_plugin",
            return_value=(True, []),
        ):
            resp = client.post(
                "/api/plugins/uninstall",
                json={"distribution_name": "az-scout-example"},
            )

        assert resp.status_code == 200
        data = resp.json()
        assert data["ok"] is True
        assert data["restart_required"] is True

    def test_uninstall_failure(self, client) -> None:  # type: ignore[no-untyped-def]
        with patch(
            "az_scout.routes.plugin_manager.uninstall_plugin",
            return_value=(False, ["not found"]),
        ):
            resp = client.post(
                "/api/plugins/uninstall",
                json={"distribution_name": "nonexistent"},
            )

        assert resp.status_code == 400
        data = resp.json()
        assert data["ok"] is False
        assert "not found" in data["errors"]
