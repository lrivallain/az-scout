"""Tests for the plugin manager (business logic + API routes)."""

import json
import os
import sys
import textwrap
from pathlib import Path
from unittest.mock import MagicMock, patch

import pytest
import requests

from az_scout import plugin_manager
from az_scout.plugin_manager import (
    GitHubRepo,
    InstalledPluginRecord,
    PluginValidationResult,
    _default_data_dir,
    fetch_latest_ref,
    fetch_pypi_latest_version,
    fetch_pypi_metadata,
    install_pypi_plugin,
    is_commit_sha,
    is_pypi_source,
    load_installed,
    parse_github_repo_url,
    reconcile_installed_plugins,
    save_installed,
    validate_plugin_repo,
    validate_pypi_plugin,
)
from az_scout.plugin_manager import _storage as _pm_storage

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
SAMPLE_SHA_2 = "b" * 40


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

        with patch("az_scout.plugin_manager._github.requests.get", side_effect=side_effect):
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

        with patch("az_scout.plugin_manager._github.requests.get", side_effect=side_effect):
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

        with patch("az_scout.plugin_manager._github.requests.get", side_effect=side_effect):
            result = validate_plugin_repo("https://github.com/owner/repo", SAMPLE_SHA)

        assert not result.ok
        assert any("module:object" in e for e in result.errors)


# ---------------------------------------------------------------------------
# Persistence tests (tmp_path)
# ---------------------------------------------------------------------------


class TestPersistence:
    def test_load_empty(self, tmp_path: Path) -> None:
        with patch.object(_pm_storage, "_INSTALLED_FILE", tmp_path / "none.json"):
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
            patch.object(_pm_storage, "_INSTALLED_FILE", installed_file),
            patch.object(_pm_storage, "_DATA_DIR", data_dir),
        ):
            save_installed([record])
            loaded = load_installed()

        assert len(loaded) == 1
        assert loaded[0].distribution_name == "az-scout-example"
        assert loaded[0].resolved_sha == SAMPLE_SHA

    def test_audit_appends(self, tmp_path: Path) -> None:
        audit_file = tmp_path / "audit.jsonl"
        with (
            patch.object(_pm_storage, "_AUDIT_FILE", audit_file),
            patch.object(_pm_storage, "_DATA_DIR", tmp_path),
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
            patch.object(_pm_storage, "load_installed", return_value=[]),
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
        with (
            patch(
                "az_scout.routes.plugin_manager.install_plugin",
                return_value=(True, [], []),
            ),
            patch("az_scout.routes.reload_plugins"),
        ):
            resp = client.post(
                "/api/plugins/install",
                json={"repo_url": "https://github.com/owner/repo", "ref": "v1.0.0"},
            )

        assert resp.status_code == 200
        data = resp.json()
        assert data["ok"] is True

    def test_install_failure(self, client) -> None:  # type: ignore[no-untyped-def]
        with patch(
            "az_scout.routes.plugin_manager.install_plugin",
            return_value=(False, [], ["install failed"]),
        ):
            resp = client.post(
                "/api/plugins/install",
                json={"repo_url": "https://github.com/owner/repo", "ref": "bad"},
            )

        assert resp.status_code == 200
        data = resp.json()
        assert data["ok"] is False
        assert "install failed" in data["errors"]

    def test_uninstall_success(self, client) -> None:  # type: ignore[no-untyped-def]
        with (
            patch(
                "az_scout.routes.plugin_manager.uninstall_plugin",
                return_value=(True, []),
            ),
            patch("az_scout.routes.reload_plugins"),
        ):
            resp = client.post(
                "/api/plugins/uninstall",
                json={"distribution_name": "az-scout-example"},
            )

        assert resp.status_code == 200
        data = resp.json()
        assert data["ok"] is True

    def test_uninstall_failure(self, client) -> None:  # type: ignore[no-untyped-def]
        with patch(
            "az_scout.routes.plugin_manager.uninstall_plugin",
            return_value=(False, ["not found"]),
        ):
            resp = client.post(
                "/api/plugins/uninstall",
                json={"distribution_name": "nonexistent"},
            )

        assert resp.status_code == 200
        data = resp.json()
        assert data["ok"] is False
        assert "not found" in data["errors"]


# ---------------------------------------------------------------------------
# fetch_latest_ref tests (mocked GitHub)
# ---------------------------------------------------------------------------


class TestFetchLatestRef:
    def test_latest_release(self) -> None:
        mock_release_resp = MagicMock()
        mock_release_resp.status_code = 200
        mock_release_resp.json.return_value = {"tag_name": "v2.0.0"}

        mock_ref_resp = MagicMock()
        mock_ref_resp.status_code = 200
        mock_ref_resp.raise_for_status = MagicMock()
        mock_ref_resp.json.return_value = {
            "object": {"sha": SAMPLE_SHA_2, "type": "commit"},
        }

        def side_effect(url: str, **_: object) -> MagicMock:
            if "/releases/latest" in url:
                return mock_release_resp
            return mock_ref_resp

        with patch("az_scout.plugin_manager._github.requests.get", side_effect=side_effect):
            ref, sha = fetch_latest_ref("owner", "repo")

        assert ref == "v2.0.0"
        assert sha == SAMPLE_SHA_2

    def test_fallback_to_tags(self) -> None:
        mock_release_resp = MagicMock()
        mock_release_resp.status_code = 404

        mock_tags_resp = MagicMock()
        mock_tags_resp.status_code = 200
        mock_tags_resp.json.return_value = [{"name": "v1.5.0"}]

        mock_ref_resp = MagicMock()
        mock_ref_resp.status_code = 200
        mock_ref_resp.raise_for_status = MagicMock()
        mock_ref_resp.json.return_value = {
            "object": {"sha": SAMPLE_SHA_2, "type": "commit"},
        }

        def side_effect(url: str, **_: object) -> MagicMock:
            if "/releases/latest" in url:
                return mock_release_resp
            if "/tags?" in url:
                return mock_tags_resp
            return mock_ref_resp

        with patch("az_scout.plugin_manager._github.requests.get", side_effect=side_effect):
            ref, sha = fetch_latest_ref("owner", "repo")

        assert ref == "v1.5.0"
        assert sha == SAMPLE_SHA_2

    def test_no_releases_or_tags(self) -> None:
        mock_release_resp = MagicMock()
        mock_release_resp.status_code = 404

        mock_tags_resp = MagicMock()
        mock_tags_resp.status_code = 200
        mock_tags_resp.json.return_value = []

        def side_effect(url: str, **_: object) -> MagicMock:
            if "/releases/latest" in url:
                return mock_release_resp
            return mock_tags_resp

        with (
            patch("az_scout.plugin_manager._github.requests.get", side_effect=side_effect),
            pytest.raises(ValueError, match="No releases or tags"),
        ):
            fetch_latest_ref("owner", "repo")


# ---------------------------------------------------------------------------
# Backward compatibility
# ---------------------------------------------------------------------------


class TestBackwardCompatibility:
    def test_load_without_update_fields(self, tmp_path: Path) -> None:
        """Old installed.json without update fields should load fine."""
        installed_file = tmp_path / "installed.json"
        old_data = [
            {
                "distribution_name": "az-scout-example",
                "repo_url": "https://github.com/owner/repo",
                "ref": "v1.0.0",
                "resolved_sha": SAMPLE_SHA,
                "entry_points": {"example": "mod:obj"},
                "installed_at": "2026-02-28T00:00:00+00:00",
                "actor": "tester",
            }
        ]
        installed_file.write_text(json.dumps(old_data), encoding="utf-8")

        with patch.object(_pm_storage, "_INSTALLED_FILE", installed_file):
            loaded = load_installed()

        assert len(loaded) == 1
        assert loaded[0].last_checked_at is None
        assert loaded[0].latest_ref is None
        assert loaded[0].latest_sha is None
        assert loaded[0].update_available is None

    def test_load_with_update_fields(self, tmp_path: Path) -> None:
        """New installed.json with update fields should load correctly."""
        installed_file = tmp_path / "installed.json"
        data = [
            {
                "distribution_name": "az-scout-example",
                "repo_url": "https://github.com/owner/repo",
                "ref": "v1.0.0",
                "resolved_sha": SAMPLE_SHA,
                "entry_points": {"example": "mod:obj"},
                "installed_at": "2026-02-28T00:00:00+00:00",
                "actor": "tester",
                "last_checked_at": "2026-02-28T12:00:00+00:00",
                "latest_ref": "v2.0.0",
                "latest_sha": SAMPLE_SHA_2,
                "update_available": True,
            }
        ]
        installed_file.write_text(json.dumps(data), encoding="utf-8")

        with patch.object(_pm_storage, "_INSTALLED_FILE", installed_file):
            loaded = load_installed()

        assert len(loaded) == 1
        assert loaded[0].latest_ref == "v2.0.0"
        assert loaded[0].latest_sha == SAMPLE_SHA_2
        assert loaded[0].update_available is True


# ---------------------------------------------------------------------------
# Data directory configuration
# ---------------------------------------------------------------------------


class TestDefaultDataDir:
    def test_env_var_override(self, tmp_path: Path) -> None:
        """AZ_SCOUT_DATA_DIR env var should override the default data directory."""
        custom_dir = str(tmp_path / "custom-data")
        with patch.dict(os.environ, {"AZ_SCOUT_DATA_DIR": custom_dir}):
            result = _default_data_dir()
        assert result == Path(custom_dir)

    def test_default_is_home_local_share(self) -> None:
        """Without env var, default should be ~/.local/share/az-scout."""
        env = {k: v for k, v in os.environ.items() if k != "AZ_SCOUT_DATA_DIR"}
        with patch.dict(os.environ, env, clear=True):
            result = _default_data_dir()
        assert result == Path.home() / ".local" / "share" / "az-scout"


# ---------------------------------------------------------------------------
# Check updates tests
# ---------------------------------------------------------------------------


class TestCheckUpdates:
    def test_check_updates_with_update(self, tmp_path: Path) -> None:
        installed_file = tmp_path / "installed.json"
        audit_file = tmp_path / "audit.jsonl"
        record = InstalledPluginRecord(
            distribution_name="az-scout-example",
            repo_url="https://github.com/owner/repo",
            ref="v1.0.0",
            resolved_sha=SAMPLE_SHA,
            entry_points={"example": "mod:obj"},
            installed_at="2026-02-28T00:00:00+00:00",
            actor="tester",
        )
        installed_file.write_text(
            json.dumps(
                [
                    {
                        "distribution_name": record.distribution_name,
                        "repo_url": record.repo_url,
                        "ref": record.ref,
                        "resolved_sha": record.resolved_sha,
                        "entry_points": record.entry_points,
                        "installed_at": record.installed_at,
                        "actor": record.actor,
                    }
                ]
            ),
            encoding="utf-8",
        )

        with (
            patch.object(_pm_storage, "_INSTALLED_FILE", installed_file),
            patch.object(_pm_storage, "_DATA_DIR", tmp_path),
            patch.object(_pm_storage, "_AUDIT_FILE", audit_file),
            patch(
                "az_scout.plugin_manager._operations.fetch_latest_ref",
                return_value=("v2.0.0", SAMPLE_SHA_2),
            ),
        ):
            results = plugin_manager.check_updates("actor", "127.0.0.1", "test-agent")

        assert len(results) == 1
        assert results[0]["update_available"] is True
        assert results[0]["latest_ref"] == "v2.0.0"
        assert results[0]["latest_sha"] == SAMPLE_SHA_2

    def test_check_updates_up_to_date(self, tmp_path: Path) -> None:
        installed_file = tmp_path / "installed.json"
        audit_file = tmp_path / "audit.jsonl"
        data = [
            {
                "distribution_name": "az-scout-example",
                "repo_url": "https://github.com/owner/repo",
                "ref": "v1.0.0",
                "resolved_sha": SAMPLE_SHA,
                "entry_points": {"example": "mod:obj"},
                "installed_at": "2026-02-28T00:00:00+00:00",
                "actor": "tester",
            }
        ]
        installed_file.write_text(json.dumps(data), encoding="utf-8")

        with (
            patch.object(_pm_storage, "_INSTALLED_FILE", installed_file),
            patch.object(_pm_storage, "_DATA_DIR", tmp_path),
            patch.object(_pm_storage, "_AUDIT_FILE", audit_file),
            patch(
                "az_scout.plugin_manager._operations.fetch_latest_ref",
                return_value=("v1.0.0", SAMPLE_SHA),
            ),
        ):
            results = plugin_manager.check_updates("actor", "127.0.0.1", "test-agent")

        assert len(results) == 1
        assert results[0]["update_available"] is False


# ---------------------------------------------------------------------------
# Update tests
# ---------------------------------------------------------------------------


class TestUpdatePlugin:
    def test_update_success(self, tmp_path: Path) -> None:
        installed_file = tmp_path / "installed.json"
        audit_file = tmp_path / "audit.jsonl"
        data = [
            {
                "distribution_name": "az-scout-example",
                "repo_url": "https://github.com/owner/repo",
                "ref": "v1.0.0",
                "resolved_sha": SAMPLE_SHA,
                "entry_points": {"example": "mod:obj"},
                "installed_at": "2026-02-28T00:00:00+00:00",
                "actor": "tester",
            }
        ]
        installed_file.write_text(json.dumps(data), encoding="utf-8")

        mock_uv = MagicMock()
        mock_uv.returncode = 0
        mock_uv.stderr = ""

        with (
            patch.object(_pm_storage, "_INSTALLED_FILE", installed_file),
            patch.object(_pm_storage, "_DATA_DIR", tmp_path),
            patch.object(_pm_storage, "_AUDIT_FILE", audit_file),
            patch(
                "az_scout.plugin_manager._operations.fetch_latest_ref",
                return_value=("v2.0.0", SAMPLE_SHA_2),
            ),
            patch("az_scout.plugin_manager._operations.run_pip", return_value=mock_uv),
        ):
            ok, errors = plugin_manager.update_plugin(
                "az-scout-example", "actor", "127.0.0.1", "test-agent"
            )

        assert ok is True
        assert errors == []

        # Verify installed.json was updated
        loaded = json.loads(installed_file.read_text(encoding="utf-8"))
        assert loaded[0]["resolved_sha"] == SAMPLE_SHA_2
        assert loaded[0]["ref"] == "v2.0.0"
        assert loaded[0]["update_available"] is False

    def test_update_not_found(self, tmp_path: Path) -> None:
        installed_file = tmp_path / "installed.json"
        audit_file = tmp_path / "audit.jsonl"
        installed_file.write_text("[]", encoding="utf-8")

        with (
            patch.object(_pm_storage, "_INSTALLED_FILE", installed_file),
            patch.object(_pm_storage, "_DATA_DIR", tmp_path),
            patch.object(_pm_storage, "_AUDIT_FILE", audit_file),
        ):
            ok, errors = plugin_manager.update_plugin(
                "nonexistent", "actor", "127.0.0.1", "test-agent"
            )

        assert ok is False
        assert any("not found" in e for e in errors)

    def test_update_already_up_to_date(self, tmp_path: Path) -> None:
        installed_file = tmp_path / "installed.json"
        audit_file = tmp_path / "audit.jsonl"
        data = [
            {
                "distribution_name": "az-scout-example",
                "repo_url": "https://github.com/owner/repo",
                "ref": "v1.0.0",
                "resolved_sha": SAMPLE_SHA,
                "entry_points": {"example": "mod:obj"},
                "installed_at": "2026-02-28T00:00:00+00:00",
                "actor": "tester",
            }
        ]
        installed_file.write_text(json.dumps(data), encoding="utf-8")

        with (
            patch.object(_pm_storage, "_INSTALLED_FILE", installed_file),
            patch.object(_pm_storage, "_DATA_DIR", tmp_path),
            patch.object(_pm_storage, "_AUDIT_FILE", audit_file),
            patch(
                "az_scout.plugin_manager._operations.fetch_latest_ref",
                return_value=("v1.0.0", SAMPLE_SHA),
            ),
        ):
            ok, errors = plugin_manager.update_plugin(
                "az-scout-example", "actor", "127.0.0.1", "test-agent"
            )

        assert ok is False
        assert any("up to date" in e.lower() for e in errors)

    def test_update_uv_failure(self, tmp_path: Path) -> None:
        installed_file = tmp_path / "installed.json"
        audit_file = tmp_path / "audit.jsonl"
        data = [
            {
                "distribution_name": "az-scout-example",
                "repo_url": "https://github.com/owner/repo",
                "ref": "v1.0.0",
                "resolved_sha": SAMPLE_SHA,
                "entry_points": {"example": "mod:obj"},
                "installed_at": "2026-02-28T00:00:00+00:00",
                "actor": "tester",
            }
        ]
        installed_file.write_text(json.dumps(data), encoding="utf-8")

        mock_uv = MagicMock()
        mock_uv.returncode = 1
        mock_uv.stderr = "pip error"

        with (
            patch.object(_pm_storage, "_INSTALLED_FILE", installed_file),
            patch.object(_pm_storage, "_DATA_DIR", tmp_path),
            patch.object(_pm_storage, "_AUDIT_FILE", audit_file),
            patch(
                "az_scout.plugin_manager._operations.fetch_latest_ref",
                return_value=("v2.0.0", SAMPLE_SHA_2),
            ),
            patch("az_scout.plugin_manager._operations.run_pip", return_value=mock_uv),
        ):
            ok, errors = plugin_manager.update_plugin(
                "az-scout-example", "actor", "127.0.0.1", "test-agent"
            )

        assert ok is False
        assert any("pip" in e.lower() for e in errors)


class TestUpdateAllPlugins:
    def test_update_all_mixed(self, tmp_path: Path) -> None:
        installed_file = tmp_path / "installed.json"
        audit_file = tmp_path / "audit.jsonl"
        data = [
            {
                "distribution_name": "az-scout-a",
                "repo_url": "https://github.com/owner/a",
                "ref": "v1.0.0",
                "resolved_sha": SAMPLE_SHA,
                "entry_points": {"a": "mod:obj"},
                "installed_at": "2026-02-28T00:00:00+00:00",
                "actor": "tester",
            },
            {
                "distribution_name": "az-scout-b",
                "repo_url": "https://github.com/owner/b",
                "ref": "v1.0.0",
                "resolved_sha": SAMPLE_SHA_2,
                "entry_points": {"b": "mod:obj"},
                "installed_at": "2026-02-28T00:00:00+00:00",
                "actor": "tester",
            },
        ]
        installed_file.write_text(json.dumps(data), encoding="utf-8")

        def mock_fetch_latest(owner: str, repo: str) -> tuple[str, str]:
            if repo == "a":
                return ("v2.0.0", SAMPLE_SHA_2)
            return ("v1.0.0", SAMPLE_SHA_2)

        mock_uv = MagicMock()
        mock_uv.returncode = 0
        mock_uv.stderr = ""

        with (
            patch.object(_pm_storage, "_INSTALLED_FILE", installed_file),
            patch.object(_pm_storage, "_DATA_DIR", tmp_path),
            patch.object(_pm_storage, "_AUDIT_FILE", audit_file),
            patch(
                "az_scout.plugin_manager._operations.fetch_latest_ref",
                side_effect=mock_fetch_latest,
            ),
            patch("az_scout.plugin_manager._operations.run_pip", return_value=mock_uv),
        ):
            updated, failed, details = plugin_manager.update_all_plugins(
                "actor", "127.0.0.1", "test-agent"
            )

        assert updated == 1
        assert failed == 0
        assert len(details) == 2


# ---------------------------------------------------------------------------
# Update route tests
# ---------------------------------------------------------------------------


class TestUpdateRoutes:
    def test_check_updates(self, client) -> None:  # type: ignore[no-untyped-def]
        with patch(
            "az_scout.routes.plugin_manager.check_updates",
            return_value=[
                {
                    "distribution_name": "az-scout-example",
                    "update_available": True,
                    "latest_ref": "v2.0.0",
                    "latest_sha": SAMPLE_SHA_2,
                }
            ],
        ):
            resp = client.get("/api/plugins/updates")

        assert resp.status_code == 200
        data = resp.json()
        assert "plugins" in data
        assert data["plugins"][0]["update_available"] is True

    def test_update_single_success(self, client) -> None:  # type: ignore[no-untyped-def]
        with (
            patch(
                "az_scout.routes.plugin_manager.update_plugin",
                return_value=(True, []),
            ),
            patch("az_scout.routes.reload_plugins"),
        ):
            resp = client.post(
                "/api/plugins/update",
                json={"distribution_name": "az-scout-example"},
            )

        assert resp.status_code == 200
        data = resp.json()
        assert data["ok"] is True

    def test_update_single_failure(self, client) -> None:  # type: ignore[no-untyped-def]
        with patch(
            "az_scout.routes.plugin_manager.update_plugin",
            return_value=(False, ["already up to date"]),
        ):
            resp = client.post(
                "/api/plugins/update",
                json={"distribution_name": "az-scout-example"},
            )

        assert resp.status_code == 200
        data = resp.json()
        assert data["ok"] is False

    def test_update_all(self, client) -> None:  # type: ignore[no-untyped-def]
        with (
            patch(
                "az_scout.routes.plugin_manager.update_all_plugins",
                return_value=(
                    2,
                    0,
                    [
                        {"distribution_name": "a", "ok": True},
                        {"distribution_name": "b", "ok": True},
                    ],
                ),
            ),
            patch("az_scout.routes.reload_plugins"),
        ):
            resp = client.post("/api/plugins/update-all")

        assert resp.status_code == 200
        data = resp.json()
        assert data["ok"] is True
        assert data["updated"] == 2

    def test_update_all_with_failures(self, client) -> None:  # type: ignore[no-untyped-def]
        with patch(
            "az_scout.routes.plugin_manager.update_all_plugins",
            return_value=(
                1,
                1,
                [
                    {"distribution_name": "a", "ok": True},
                    {"distribution_name": "b", "ok": False, "error": "failed"},
                ],
            ),
        ):
            resp = client.post("/api/plugins/update-all")

        assert resp.status_code == 200
        data = resp.json()
        assert data["ok"] is False
        assert data["updated"] == 1
        assert data["failed"] == 1


# ---------------------------------------------------------------------------
# pip --target tests
# ---------------------------------------------------------------------------


class TestRunPip:
    def test_uses_uv_when_available(self, tmp_path: Path) -> None:
        """When uv is found, run_pip uses uv pip with --target."""
        pkg_dir = tmp_path / "plugin-packages"
        mock_proc = MagicMock()
        mock_proc.returncode = 0

        with (
            patch.object(_pm_storage, "_PACKAGES_DIR", pkg_dir),
            patch("az_scout.plugin_manager._installer._find_uv", return_value="/usr/bin/uv"),
            patch(
                "az_scout.plugin_manager._installer.subprocess.run", return_value=mock_proc
            ) as mock_run,
        ):
            plugin_manager.run_pip(["pip", "install", "some-pkg"])

        cmd = mock_run.call_args[0][0]
        assert cmd[0] == "/usr/bin/uv"
        assert cmd[1] == "pip"
        assert "install" in cmd
        assert "--target" in cmd
        assert str(pkg_dir) in cmd

    def test_uses_python_pip_fallback(self, tmp_path: Path) -> None:
        """When uv is not found, run_pip uses python -m pip with --target."""
        pkg_dir = tmp_path / "plugin-packages"
        mock_proc = MagicMock()
        mock_proc.returncode = 0

        with (
            patch.object(_pm_storage, "_PACKAGES_DIR", pkg_dir),
            patch("az_scout.plugin_manager._installer._find_uv", return_value=None),
            patch(
                "az_scout.plugin_manager._installer.subprocess.run", return_value=mock_proc
            ) as mock_run,
        ):
            plugin_manager.run_pip(["pip", "install", "some-pkg"])

        cmd = mock_run.call_args[0][0]
        assert cmd[0] == sys.executable
        assert cmd[1] == "-m"
        assert cmd[2] == "pip"
        assert "install" in cmd
        assert "--target" in cmd
        assert str(pkg_dir) in cmd

    def test_pip_fallback_uninstall_adds_y_flag(self, tmp_path: Path) -> None:
        """Fallback pip uninstall automatically adds -y for non-interactive mode."""
        pkg_dir = tmp_path / "plugin-packages"
        mock_proc = MagicMock()
        mock_proc.returncode = 0

        with (
            patch.object(_pm_storage, "_PACKAGES_DIR", pkg_dir),
            patch("az_scout.plugin_manager._installer._find_uv", return_value=None),
            patch(
                "az_scout.plugin_manager._installer.subprocess.run", return_value=mock_proc
            ) as mock_run,
        ):
            plugin_manager.run_pip(["pip", "uninstall", "some-pkg"])

        cmd = mock_run.call_args[0][0]
        assert "-y" in cmd
        assert "some-pkg" in cmd
        assert "--target" in cmd

    def test_creates_packages_dir(self, tmp_path: Path) -> None:
        """run_pip creates the packages directory if it does not exist."""
        pkg_dir = tmp_path / "plugin-packages"
        mock_proc = MagicMock()
        mock_proc.returncode = 0

        with (
            patch.object(_pm_storage, "_PACKAGES_DIR", pkg_dir),
            patch("az_scout.plugin_manager._installer._find_uv", return_value="/usr/bin/uv"),
            patch("az_scout.plugin_manager._installer.subprocess.run", return_value=mock_proc),
        ):
            plugin_manager.run_pip(["pip", "install", "some-pkg"])

        assert pkg_dir.exists()


# ---------------------------------------------------------------------------
# Reconciliation tests
# ---------------------------------------------------------------------------


class TestReconcileInstalledPlugins:
    def _make_installed_json(self, tmp_path: Path, records: list[dict]) -> Path:  # type: ignore[type-arg]
        installed_file = tmp_path / "installed.json"
        installed_file.write_text(json.dumps(records), encoding="utf-8")
        return installed_file

    def _sample_record(self, name: str = "az-scout-example") -> dict:  # type: ignore[type-arg]
        return {
            "distribution_name": name,
            "repo_url": "https://github.com/owner/repo",
            "ref": "v1.0.0",
            "resolved_sha": SAMPLE_SHA,
            "entry_points": {"example": "mod:obj"},
            "installed_at": "2026-02-28T00:00:00+00:00",
            "actor": "tester",
        }

    def test_no_installed_file(self, tmp_path: Path) -> None:
        """When installed.json does not exist, reconcile returns empty list."""
        with patch.object(_pm_storage, "_INSTALLED_FILE", tmp_path / "none.json"):
            results = reconcile_installed_plugins()
        assert results == []

    def test_all_plugins_present(self, tmp_path: Path) -> None:
        """When all plugins are already installed, no reinstall happens."""
        installed_file = self._make_installed_json(tmp_path, [self._sample_record()])
        with (
            patch.object(_pm_storage, "_INSTALLED_FILE", installed_file),
            patch(
                "az_scout.plugin_manager._operations._is_plugin_installed",
                return_value=True,
            ),
        ):
            results = reconcile_installed_plugins()

        assert len(results) == 1
        assert results[0]["reinstalled"] is False
        assert results[0]["error"] == ""

    def test_missing_plugin_reinstalled(self, tmp_path: Path) -> None:
        """A plugin missing from packages dir is reinstalled from pinned SHA."""
        installed_file = self._make_installed_json(tmp_path, [self._sample_record()])
        audit_file = tmp_path / "audit.jsonl"
        mock_pip = MagicMock()
        mock_pip.returncode = 0
        mock_pip.stderr = ""

        with (
            patch.object(_pm_storage, "_INSTALLED_FILE", installed_file),
            patch.object(_pm_storage, "_AUDIT_FILE", audit_file),
            patch.object(_pm_storage, "_DATA_DIR", tmp_path),
            patch(
                "az_scout.plugin_manager._operations._is_plugin_installed",
                return_value=False,
            ),
            patch(
                "az_scout.plugin_manager._operations.run_pip",
                return_value=mock_pip,
            ) as mock_run,
        ):
            results = reconcile_installed_plugins()

        assert len(results) == 1
        assert results[0]["reinstalled"] is True
        assert results[0]["error"] == ""
        # Verify it installed from the correct pinned SHA
        args = mock_run.call_args[0][0]
        assert "pip" in args
        assert "install" in args
        assert SAMPLE_SHA in args[-1]

    def test_reinstall_failure_recorded(self, tmp_path: Path) -> None:
        """When reinstall fails, the error is captured but other plugins proceed."""
        records = [
            self._sample_record("az-scout-a"),
            self._sample_record("az-scout-b"),
        ]
        records[1]["resolved_sha"] = SAMPLE_SHA_2
        installed_file = self._make_installed_json(tmp_path, records)
        audit_file = tmp_path / "audit.jsonl"

        mock_fail = MagicMock()
        mock_fail.returncode = 1
        mock_fail.stderr = "network error"

        mock_ok = MagicMock()
        mock_ok.returncode = 0
        mock_ok.stderr = ""

        call_count = 0

        def run_side_effect(args: list[str]) -> MagicMock:
            nonlocal call_count
            call_count += 1
            return mock_fail if call_count == 1 else mock_ok

        with (
            patch.object(_pm_storage, "_INSTALLED_FILE", installed_file),
            patch.object(_pm_storage, "_AUDIT_FILE", audit_file),
            patch.object(_pm_storage, "_DATA_DIR", tmp_path),
            patch(
                "az_scout.plugin_manager._operations._is_plugin_installed",
                side_effect=lambda name: False,
            ),
            patch(
                "az_scout.plugin_manager._operations.run_pip",
                side_effect=run_side_effect,
            ),
        ):
            results = reconcile_installed_plugins()

        assert len(results) == 2
        # First plugin failed
        assert results[0]["reinstalled"] is False
        assert "network error" in str(results[0]["error"])
        # Second plugin succeeded
        assert results[1]["reinstalled"] is True
        assert results[1]["error"] == ""


# ---------------------------------------------------------------------------
# PyPI source detection
# ---------------------------------------------------------------------------


class TestIsPypiSource:
    def test_pypi_package_name(self) -> None:
        assert is_pypi_source("az-scout-example") is True

    def test_pypi_package_with_dots(self) -> None:
        assert is_pypi_source("az.scout.example") is True

    def test_pypi_package_with_underscores(self) -> None:
        assert is_pypi_source("az_scout_example") is True

    def test_github_url(self) -> None:
        assert is_pypi_source("https://github.com/owner/repo") is False

    def test_http_url(self) -> None:
        assert is_pypi_source("http://example.com/pkg") is False

    def test_empty_string(self) -> None:
        assert is_pypi_source("") is False

    def test_single_char(self) -> None:
        assert is_pypi_source("a") is True

    def test_invalid_chars(self) -> None:
        assert is_pypi_source("pkg name!") is False


# ---------------------------------------------------------------------------
# PyPI metadata / validation tests
# ---------------------------------------------------------------------------


SAMPLE_PYPI_RESPONSE: dict = {
    "info": {
        "name": "az-scout-example",
        "version": "0.2.0",
        "requires_dist": ["az-scout>=2025.1", "fastapi>=0.100"],
        "project_urls": {"Homepage": "https://github.com/owner/az-scout-example"},
    },
    "releases": {
        "0.1.0": [],
        "0.2.0": [],
    },
}


class TestFetchPypiMetadata:
    def test_latest(self) -> None:
        mock_resp = MagicMock()
        mock_resp.json.return_value = SAMPLE_PYPI_RESPONSE
        mock_resp.raise_for_status = MagicMock()
        with patch(
            ("az_scout.plugin_manager._pypi.requests.get"), return_value=mock_resp
        ) as mock_get:
            data = fetch_pypi_metadata("az-scout-example")

        assert data == SAMPLE_PYPI_RESPONSE
        mock_get.assert_called_once()
        assert "/az-scout-example/json" in mock_get.call_args[0][0]

    def test_specific_version(self) -> None:
        mock_resp = MagicMock()
        mock_resp.json.return_value = SAMPLE_PYPI_RESPONSE
        mock_resp.raise_for_status = MagicMock()
        with patch(
            ("az_scout.plugin_manager._pypi.requests.get"), return_value=mock_resp
        ) as mock_get:
            fetch_pypi_metadata("az-scout-example", "0.1.0")

        assert "/az-scout-example/0.1.0/json" in mock_get.call_args[0][0]


class TestValidatePypiPlugin:
    def test_valid_package(self) -> None:
        mock_resp = MagicMock()
        mock_resp.json.return_value = SAMPLE_PYPI_RESPONSE
        mock_resp.raise_for_status = MagicMock()
        with patch(("az_scout.plugin_manager._pypi.requests.get"), return_value=mock_resp):
            result = validate_pypi_plugin("az-scout-example")

        assert result.ok
        assert result.source == "pypi"
        assert result.distribution_name == "az-scout-example"
        assert result.version == "0.2.0"
        assert result.ref == "0.2.0"
        assert result.repo_url == "https://github.com/owner/az-scout-example"
        assert len(result.warnings) == 0

    def test_valid_with_version(self) -> None:
        mock_resp = MagicMock()
        mock_resp.json.return_value = {
            "info": {
                "name": "az-scout-example",
                "version": "0.1.0",
                "requires_dist": ["az-scout"],
                "project_urls": {},
            },
        }
        mock_resp.raise_for_status = MagicMock()
        with patch(("az_scout.plugin_manager._pypi.requests.get"), return_value=mock_resp):
            result = validate_pypi_plugin("az-scout-example", "0.1.0")

        assert result.ok
        assert result.version == "0.1.0"

    def test_package_not_found(self) -> None:
        mock_resp = MagicMock()
        mock_resp.status_code = 404
        http_err = requests.HTTPError(response=mock_resp)
        mock_resp.raise_for_status.side_effect = http_err
        with patch(("az_scout.plugin_manager._pypi.requests.get"), return_value=mock_resp):
            result = validate_pypi_plugin("nonexistent-pkg")

        assert not result.ok
        assert any("not found on PyPI" in e for e in result.errors)

    def test_version_not_found(self) -> None:
        mock_resp = MagicMock()
        mock_resp.status_code = 404
        http_err = requests.HTTPError(response=mock_resp)
        mock_resp.raise_for_status.side_effect = http_err
        with patch(("az_scout.plugin_manager._pypi.requests.get"), return_value=mock_resp):
            result = validate_pypi_plugin("az-scout-example", "99.99.99")

        assert not result.ok
        assert any("99.99.99" in e for e in result.errors)

    def test_naming_warning(self) -> None:
        mock_resp = MagicMock()
        mock_resp.json.return_value = {
            "info": {
                "name": "some-random-pkg",
                "version": "1.0.0",
                "requires_dist": ["az-scout"],
                "project_urls": {},
            },
        }
        mock_resp.raise_for_status = MagicMock()
        with patch(("az_scout.plugin_manager._pypi.requests.get"), return_value=mock_resp):
            result = validate_pypi_plugin("some-random-pkg")

        assert result.ok
        assert any("naming convention" in w for w in result.warnings)

    def test_missing_dependency_warning(self) -> None:
        mock_resp = MagicMock()
        mock_resp.json.return_value = {
            "info": {
                "name": "az-scout-example",
                "version": "1.0.0",
                "requires_dist": ["requests"],
                "project_urls": {},
            },
        }
        mock_resp.raise_for_status = MagicMock()
        with patch(("az_scout.plugin_manager._pypi.requests.get"), return_value=mock_resp):
            result = validate_pypi_plugin("az-scout-example")

        assert result.ok
        assert any("az-scout" in w for w in result.warnings)

    def test_invalid_name(self) -> None:
        result = validate_pypi_plugin("invalid name!")
        assert not result.ok
        assert any("Invalid package name" in e for e in result.errors)


class TestFetchPypiLatestVersion:
    def test_success(self) -> None:
        mock_resp = MagicMock()
        mock_resp.json.return_value = SAMPLE_PYPI_RESPONSE
        mock_resp.raise_for_status = MagicMock()
        with patch(("az_scout.plugin_manager._pypi.requests.get"), return_value=mock_resp):
            version = fetch_pypi_latest_version("az-scout-example")

        assert version == "0.2.0"

    def test_not_found(self) -> None:
        mock_resp = MagicMock()
        mock_resp.status_code = 404
        http_err = requests.HTTPError(response=mock_resp)
        mock_resp.raise_for_status.side_effect = http_err
        with (
            patch(("az_scout.plugin_manager._pypi.requests.get"), return_value=mock_resp),
            pytest.raises(ValueError, match="not found"),
        ):
            fetch_pypi_latest_version("nonexistent")


# ---------------------------------------------------------------------------
# PyPI install tests
# ---------------------------------------------------------------------------


class TestInstallPypiPlugin:
    def test_success(self, tmp_path: Path) -> None:
        installed_file = tmp_path / "installed.json"
        audit_file = tmp_path / "audit.jsonl"
        installed_file.write_text("[]", encoding="utf-8")

        mock_resp = MagicMock()
        mock_resp.json.return_value = SAMPLE_PYPI_RESPONSE
        mock_resp.raise_for_status = MagicMock()

        mock_pip = MagicMock()
        mock_pip.returncode = 0
        mock_pip.stderr = ""

        with (
            patch.object(_pm_storage, "_INSTALLED_FILE", installed_file),
            patch.object(_pm_storage, "_DATA_DIR", tmp_path),
            patch.object(_pm_storage, "_AUDIT_FILE", audit_file),
            patch(("az_scout.plugin_manager._pypi.requests.get"), return_value=mock_resp),
            patch("az_scout.plugin_manager._operations.run_pip", return_value=mock_pip),
        ):
            ok, warnings, errors = install_pypi_plugin(
                "az-scout-example", "", "actor", "127.0.0.1", "test-agent"
            )

        assert ok is True
        assert errors == []

        loaded = json.loads(installed_file.read_text(encoding="utf-8"))
        assert len(loaded) == 1
        assert loaded[0]["distribution_name"] == "az-scout-example"
        assert loaded[0]["source"] == "pypi"
        assert loaded[0]["ref"] == "0.2.0"
        assert loaded[0]["resolved_sha"] == ""

    def test_validation_failure(self, tmp_path: Path) -> None:
        installed_file = tmp_path / "installed.json"
        audit_file = tmp_path / "audit.jsonl"
        installed_file.write_text("[]", encoding="utf-8")

        mock_resp = MagicMock()
        mock_resp.status_code = 404
        http_err = requests.HTTPError(response=mock_resp)
        mock_resp.raise_for_status.side_effect = http_err

        with (
            patch.object(_pm_storage, "_INSTALLED_FILE", installed_file),
            patch.object(_pm_storage, "_DATA_DIR", tmp_path),
            patch.object(_pm_storage, "_AUDIT_FILE", audit_file),
            patch(("az_scout.plugin_manager._pypi.requests.get"), return_value=mock_resp),
        ):
            ok, warnings, errors = install_pypi_plugin(
                "nonexistent", "", "actor", "127.0.0.1", "test-agent"
            )

        assert ok is False
        assert len(errors) > 0

    def test_pip_failure(self, tmp_path: Path) -> None:
        installed_file = tmp_path / "installed.json"
        audit_file = tmp_path / "audit.jsonl"
        installed_file.write_text("[]", encoding="utf-8")

        mock_resp = MagicMock()
        mock_resp.json.return_value = SAMPLE_PYPI_RESPONSE
        mock_resp.raise_for_status = MagicMock()

        mock_pip = MagicMock()
        mock_pip.returncode = 1
        mock_pip.stderr = "pip error"

        with (
            patch.object(_pm_storage, "_INSTALLED_FILE", installed_file),
            patch.object(_pm_storage, "_DATA_DIR", tmp_path),
            patch.object(_pm_storage, "_AUDIT_FILE", audit_file),
            patch(("az_scout.plugin_manager._pypi.requests.get"), return_value=mock_resp),
            patch("az_scout.plugin_manager._operations.run_pip", return_value=mock_pip),
        ):
            ok, warnings, errors = install_pypi_plugin(
                "az-scout-example", "", "actor", "127.0.0.1", "test-agent"
            )

        assert ok is False
        assert any("pip" in e.lower() for e in errors)


# ---------------------------------------------------------------------------
# PyPI check updates
# ---------------------------------------------------------------------------


class TestCheckUpdatesPypi:
    def test_update_available(self, tmp_path: Path) -> None:
        installed_file = tmp_path / "installed.json"
        audit_file = tmp_path / "audit.jsonl"
        data = [
            {
                "distribution_name": "az-scout-example",
                "repo_url": "",
                "ref": "0.1.0",
                "resolved_sha": "",
                "entry_points": {},
                "installed_at": "2026-02-28T00:00:00+00:00",
                "actor": "tester",
                "source": "pypi",
            }
        ]
        installed_file.write_text(json.dumps(data), encoding="utf-8")

        with (
            patch.object(_pm_storage, "_INSTALLED_FILE", installed_file),
            patch.object(_pm_storage, "_DATA_DIR", tmp_path),
            patch.object(_pm_storage, "_AUDIT_FILE", audit_file),
            patch(
                "az_scout.plugin_manager._operations.fetch_pypi_latest_version",
                return_value="0.2.0",
            ),
        ):
            results = plugin_manager.check_updates("actor", "127.0.0.1", "test-agent")

        assert len(results) == 1
        assert results[0]["update_available"] is True
        assert results[0]["latest_ref"] == "0.2.0"
        assert results[0]["source"] == "pypi"

    def test_up_to_date(self, tmp_path: Path) -> None:
        installed_file = tmp_path / "installed.json"
        audit_file = tmp_path / "audit.jsonl"
        data = [
            {
                "distribution_name": "az-scout-example",
                "repo_url": "",
                "ref": "0.2.0",
                "resolved_sha": "",
                "entry_points": {},
                "installed_at": "2026-02-28T00:00:00+00:00",
                "actor": "tester",
                "source": "pypi",
            }
        ]
        installed_file.write_text(json.dumps(data), encoding="utf-8")

        with (
            patch.object(_pm_storage, "_INSTALLED_FILE", installed_file),
            patch.object(_pm_storage, "_DATA_DIR", tmp_path),
            patch.object(_pm_storage, "_AUDIT_FILE", audit_file),
            patch(
                "az_scout.plugin_manager._operations.fetch_pypi_latest_version",
                return_value="0.2.0",
            ),
        ):
            results = plugin_manager.check_updates("actor", "127.0.0.1", "test-agent")

        assert len(results) == 1
        assert results[0]["update_available"] is False


# ---------------------------------------------------------------------------
# PyPI update tests
# ---------------------------------------------------------------------------


class TestUpdatePypiPlugin:
    def test_update_success(self, tmp_path: Path) -> None:
        installed_file = tmp_path / "installed.json"
        audit_file = tmp_path / "audit.jsonl"
        data = [
            {
                "distribution_name": "az-scout-example",
                "repo_url": "",
                "ref": "0.1.0",
                "resolved_sha": "",
                "entry_points": {},
                "installed_at": "2026-02-28T00:00:00+00:00",
                "actor": "tester",
                "source": "pypi",
            }
        ]
        installed_file.write_text(json.dumps(data), encoding="utf-8")

        mock_pip = MagicMock()
        mock_pip.returncode = 0
        mock_pip.stderr = ""

        with (
            patch.object(_pm_storage, "_INSTALLED_FILE", installed_file),
            patch.object(_pm_storage, "_DATA_DIR", tmp_path),
            patch.object(_pm_storage, "_AUDIT_FILE", audit_file),
            patch(
                "az_scout.plugin_manager._operations.fetch_pypi_latest_version",
                return_value="0.2.0",
            ),
            patch("az_scout.plugin_manager._operations.run_pip", return_value=mock_pip),
        ):
            ok, errors = plugin_manager.update_plugin(
                "az-scout-example", "actor", "127.0.0.1", "test-agent"
            )

        assert ok is True
        assert errors == []

        loaded = json.loads(installed_file.read_text(encoding="utf-8"))
        assert loaded[0]["ref"] == "0.2.0"
        assert loaded[0]["source"] == "pypi"

    def test_already_up_to_date(self, tmp_path: Path) -> None:
        installed_file = tmp_path / "installed.json"
        audit_file = tmp_path / "audit.jsonl"
        data = [
            {
                "distribution_name": "az-scout-example",
                "repo_url": "",
                "ref": "0.2.0",
                "resolved_sha": "",
                "entry_points": {},
                "installed_at": "2026-02-28T00:00:00+00:00",
                "actor": "tester",
                "source": "pypi",
            }
        ]
        installed_file.write_text(json.dumps(data), encoding="utf-8")

        with (
            patch.object(_pm_storage, "_INSTALLED_FILE", installed_file),
            patch.object(_pm_storage, "_DATA_DIR", tmp_path),
            patch.object(_pm_storage, "_AUDIT_FILE", audit_file),
            patch(
                "az_scout.plugin_manager._operations.fetch_pypi_latest_version",
                return_value="0.2.0",
            ),
        ):
            ok, errors = plugin_manager.update_plugin(
                "az-scout-example", "actor", "127.0.0.1", "test-agent"
            )

        assert ok is False
        assert any("up to date" in e.lower() for e in errors)


# ---------------------------------------------------------------------------
# PyPI reconciliation
# ---------------------------------------------------------------------------


class TestReconcilePypiPlugin:
    def test_pypi_plugin_reinstalled(self, tmp_path: Path) -> None:
        """A PyPI plugin missing from packages is reinstalled with pinned version."""
        installed_file = tmp_path / "installed.json"
        audit_file = tmp_path / "audit.jsonl"
        data = [
            {
                "distribution_name": "az-scout-example",
                "repo_url": "",
                "ref": "0.2.0",
                "resolved_sha": "",
                "entry_points": {},
                "installed_at": "2026-02-28T00:00:00+00:00",
                "actor": "tester",
                "source": "pypi",
            }
        ]
        installed_file.write_text(json.dumps(data), encoding="utf-8")

        mock_pip = MagicMock()
        mock_pip.returncode = 0
        mock_pip.stderr = ""

        with (
            patch.object(_pm_storage, "_INSTALLED_FILE", installed_file),
            patch.object(_pm_storage, "_DATA_DIR", tmp_path),
            patch.object(_pm_storage, "_AUDIT_FILE", audit_file),
            patch("az_scout.plugin_manager._operations._is_plugin_installed", return_value=False),
            patch("az_scout.plugin_manager._operations.run_pip", return_value=mock_pip) as mock_run,
        ):
            results = reconcile_installed_plugins()

        assert len(results) == 1
        assert results[0]["reinstalled"] is True
        args = mock_run.call_args[0][0]
        assert "install" in args
        # Should use pip_spec like "az-scout-example==0.2.0"
        assert "az-scout-example==0.2.0" in args[-1]


# ---------------------------------------------------------------------------
# PyPI route tests
# ---------------------------------------------------------------------------


class TestPypiRoutes:
    def test_validate_pypi(self, client) -> None:  # type: ignore[no-untyped-def]
        result = PluginValidationResult(
            ok=True,
            owner="",
            repo="",
            repo_url="",
            ref="0.2.0",
            source="pypi",
            distribution_name="az-scout-example",
            version="0.2.0",
        )
        with patch(
            "az_scout.routes.plugin_manager.validate_pypi_plugin",
            return_value=result,
        ):
            resp = client.post(
                "/api/plugins/validate",
                json={"repo_url": "az-scout-example"},
            )

        assert resp.status_code == 200
        data = resp.json()
        assert data["ok"] is True
        assert data["source"] == "pypi"
        assert data["version"] == "0.2.0"

    def test_validate_github_without_ref_auto_resolves(self, client) -> None:  # type: ignore[no-untyped-def]
        """GitHub sources without ref auto-resolve to latest."""
        result = PluginValidationResult(
            ok=True,
            owner="owner",
            repo="repo",
            repo_url="https://github.com/owner/repo",
            ref="v2.0.0",
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
                json={"repo_url": "https://github.com/owner/repo"},
            )

        assert resp.status_code == 200
        data = resp.json()
        assert data["ok"] is True
        assert data["ref"] == "v2.0.0"

    def test_install_pypi(self, client) -> None:  # type: ignore[no-untyped-def]
        with (
            patch(
                "az_scout.routes.plugin_manager.install_pypi_plugin",
                return_value=(True, [], []),
            ),
            patch("az_scout.routes.reload_plugins"),
        ):
            resp = client.post(
                "/api/plugins/install",
                json={"repo_url": "az-scout-example"},
            )

        assert resp.status_code == 200
        data = resp.json()
        assert data["ok"] is True

    def test_install_github_without_ref_auto_resolves(self, client) -> None:  # type: ignore[no-untyped-def]
        """GitHub installs without ref auto-resolve to latest."""
        with (
            patch(
                "az_scout.routes.plugin_manager.install_plugin",
                return_value=(True, [], []),
            ),
            patch("az_scout.routes.reload_plugins"),
        ):
            resp = client.post(
                "/api/plugins/install",
                json={"repo_url": "https://github.com/owner/repo"},
            )

        assert resp.status_code == 200
        data = resp.json()
        assert data["ok"] is True


# ---------------------------------------------------------------------------
# Backward compatibility – source field
# ---------------------------------------------------------------------------


class TestSourceBackwardCompat:
    def test_load_without_source_defaults_to_github(self, tmp_path: Path) -> None:
        """Old installed.json without 'source' field should default to github."""
        installed_file = tmp_path / "installed.json"
        data = [
            {
                "distribution_name": "az-scout-example",
                "repo_url": "https://github.com/owner/repo",
                "ref": "v1.0.0",
                "resolved_sha": SAMPLE_SHA,
                "entry_points": {"example": "mod:obj"},
                "installed_at": "2026-02-28T00:00:00+00:00",
                "actor": "tester",
            }
        ]
        installed_file.write_text(json.dumps(data), encoding="utf-8")

        with patch.object(_pm_storage, "_INSTALLED_FILE", installed_file):
            loaded = load_installed()

        assert len(loaded) == 1
        assert loaded[0].source == "github"

    def test_load_with_pypi_source(self, tmp_path: Path) -> None:
        """installed.json with source=pypi should load correctly."""
        installed_file = tmp_path / "installed.json"
        data = [
            {
                "distribution_name": "az-scout-example",
                "repo_url": "",
                "ref": "0.2.0",
                "resolved_sha": "",
                "entry_points": {},
                "installed_at": "2026-02-28T00:00:00+00:00",
                "actor": "tester",
                "source": "pypi",
            }
        ]
        installed_file.write_text(json.dumps(data), encoding="utf-8")

        with patch.object(_pm_storage, "_INSTALLED_FILE", installed_file):
            loaded = load_installed()

        assert len(loaded) == 1
        assert loaded[0].source == "pypi"
        assert loaded[0].ref == "0.2.0"


# ---------------------------------------------------------------------------
# Recommended plugins
# ---------------------------------------------------------------------------


class TestLoadRecommendedPlugins:
    def test_load_with_no_installed(self, tmp_path: Path) -> None:
        """Recommended plugins should all show installed=False when nothing is installed."""
        rec_file = tmp_path / "recommended_plugins.json"
        rec_file.write_text(
            json.dumps(
                [
                    {
                        "name": "az-scout-plugin-foo",
                        "description": "Foo plugin",
                        "source": "pypi",
                    },
                    {
                        "name": "az-scout-plugin-bar",
                        "description": "Bar plugin",
                        "source": "github",
                        "url": "https://github.com/owner/bar",
                    },
                ]
            ),
            encoding="utf-8",
        )
        installed_file = tmp_path / "installed.json"
        installed_file.write_text("[]", encoding="utf-8")

        with (
            patch.object(_pm_storage, "_RECOMMENDED_FILE", rec_file),
            patch.object(_pm_storage, "_INSTALLED_FILE", installed_file),
        ):
            result = plugin_manager.load_recommended_plugins()

        assert len(result) == 2
        assert result[0]["name"] == "az-scout-plugin-foo"
        assert result[0]["installed"] is False
        assert result[1]["name"] == "az-scout-plugin-bar"
        assert result[1]["source"] == "github"
        assert result[1]["installed"] is False

    def test_installed_plugin_marked(self, tmp_path: Path) -> None:
        """Plugins that are already installed should be marked installed=True."""
        rec_file = tmp_path / "recommended_plugins.json"
        rec_file.write_text(
            json.dumps(
                [
                    {
                        "name": "az-scout-plugin-foo",
                        "description": "Foo",
                        "source": "pypi",
                    },
                ]
            ),
            encoding="utf-8",
        )
        installed_file = tmp_path / "installed.json"
        installed_file.write_text(
            json.dumps(
                [
                    {
                        "distribution_name": "az-scout-plugin-foo",
                        "repo_url": "",
                        "ref": "1.0.0",
                        "resolved_sha": "",
                        "entry_points": {},
                        "installed_at": "2026-01-01T00:00:00+00:00",
                        "actor": "tester",
                        "source": "pypi",
                    }
                ]
            ),
            encoding="utf-8",
        )

        with (
            patch.object(_pm_storage, "_RECOMMENDED_FILE", rec_file),
            patch.object(_pm_storage, "_INSTALLED_FILE", installed_file),
        ):
            result = plugin_manager.load_recommended_plugins()

        assert len(result) == 1
        assert result[0]["installed"] is True

    def test_missing_file_returns_empty(self, tmp_path: Path) -> None:
        """When the recommended file does not exist, return an empty list."""
        missing = tmp_path / "does_not_exist.json"
        with patch.object(_pm_storage, "_RECOMMENDED_FILE", missing):
            result = plugin_manager.load_recommended_plugins()
        assert result == []

    def test_optional_version_field(self, tmp_path: Path) -> None:
        """Entries with a version field should be propagated."""
        rec_file = tmp_path / "recommended_plugins.json"
        rec_file.write_text(
            json.dumps(
                [
                    {
                        "name": "az-scout-plugin-pinned",
                        "description": "Pinned plugin",
                        "source": "pypi",
                        "version": "2.0.0",
                    },
                ]
            ),
            encoding="utf-8",
        )
        installed_file = tmp_path / "installed.json"
        installed_file.write_text("[]", encoding="utf-8")

        with (
            patch.object(_pm_storage, "_RECOMMENDED_FILE", rec_file),
            patch.object(_pm_storage, "_INSTALLED_FILE", installed_file),
        ):
            result = plugin_manager.load_recommended_plugins()

        assert result[0]["version"] == "2.0.0"


class TestRecommendedRoute:
    def test_list_recommended(self, client) -> None:  # type: ignore[no-untyped-def]
        """GET /api/plugins/recommended should return the recommended list."""
        mock_data = [
            {
                "name": "az-scout-plugin-foo",
                "description": "Foo",
                "source": "pypi",
                "url": "",
                "version": "",
                "installed": False,
            }
        ]
        with patch.object(plugin_manager, "load_recommended_plugins", return_value=mock_data):
            resp = client.get("/api/plugins/recommended")

        assert resp.status_code == 200
        data = resp.json()
        assert "plugins" in data
        assert len(data["plugins"]) == 1
        assert data["plugins"][0]["name"] == "az-scout-plugin-foo"
        assert data["plugins"][0]["installed"] is False
