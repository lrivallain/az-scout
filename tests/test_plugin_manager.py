"""Tests for the plugin manager (business logic + API routes)."""

import json
import os
import textwrap
from pathlib import Path
from unittest.mock import MagicMock, patch

import pytest

from az_scout import plugin_manager
from az_scout.plugin_manager import (
    GitHubRepo,
    InstalledPluginRecord,
    PluginValidationResult,
    _default_data_dir,
    fetch_latest_ref,
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

        assert resp.status_code == 200
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

        with patch("az_scout.plugin_manager.requests.get", side_effect=side_effect):
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

        with patch("az_scout.plugin_manager.requests.get", side_effect=side_effect):
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
            patch("az_scout.plugin_manager.requests.get", side_effect=side_effect),
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

        with patch.object(plugin_manager, "_INSTALLED_FILE", installed_file):
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

        with patch.object(plugin_manager, "_INSTALLED_FILE", installed_file):
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
            patch.object(plugin_manager, "_INSTALLED_FILE", installed_file),
            patch.object(plugin_manager, "_DATA_DIR", tmp_path),
            patch.object(plugin_manager, "_AUDIT_FILE", audit_file),
            patch(
                "az_scout.plugin_manager.fetch_latest_ref",
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
            patch.object(plugin_manager, "_INSTALLED_FILE", installed_file),
            patch.object(plugin_manager, "_DATA_DIR", tmp_path),
            patch.object(plugin_manager, "_AUDIT_FILE", audit_file),
            patch(
                "az_scout.plugin_manager.fetch_latest_ref",
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
            patch.object(plugin_manager, "_INSTALLED_FILE", installed_file),
            patch.object(plugin_manager, "_DATA_DIR", tmp_path),
            patch.object(plugin_manager, "_AUDIT_FILE", audit_file),
            patch(
                "az_scout.plugin_manager.fetch_latest_ref",
                return_value=("v2.0.0", SAMPLE_SHA_2),
            ),
            patch("az_scout.plugin_manager.run_uv_in_venv", return_value=mock_uv),
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
            patch.object(plugin_manager, "_INSTALLED_FILE", installed_file),
            patch.object(plugin_manager, "_DATA_DIR", tmp_path),
            patch.object(plugin_manager, "_AUDIT_FILE", audit_file),
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
            patch.object(plugin_manager, "_INSTALLED_FILE", installed_file),
            patch.object(plugin_manager, "_DATA_DIR", tmp_path),
            patch.object(plugin_manager, "_AUDIT_FILE", audit_file),
            patch(
                "az_scout.plugin_manager.fetch_latest_ref",
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
            patch.object(plugin_manager, "_INSTALLED_FILE", installed_file),
            patch.object(plugin_manager, "_DATA_DIR", tmp_path),
            patch.object(plugin_manager, "_AUDIT_FILE", audit_file),
            patch(
                "az_scout.plugin_manager.fetch_latest_ref",
                return_value=("v2.0.0", SAMPLE_SHA_2),
            ),
            patch("az_scout.plugin_manager.run_uv_in_venv", return_value=mock_uv),
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
            patch.object(plugin_manager, "_INSTALLED_FILE", installed_file),
            patch.object(plugin_manager, "_DATA_DIR", tmp_path),
            patch.object(plugin_manager, "_AUDIT_FILE", audit_file),
            patch(
                "az_scout.plugin_manager.fetch_latest_ref",
                side_effect=mock_fetch_latest,
            ),
            patch("az_scout.plugin_manager.run_uv_in_venv", return_value=mock_uv),
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
        with patch(
            "az_scout.routes.plugin_manager.update_plugin",
            return_value=(True, []),
        ):
            resp = client.post(
                "/api/plugins/update",
                json={"distribution_name": "az-scout-example"},
            )

        assert resp.status_code == 200
        data = resp.json()
        assert data["ok"] is True
        assert data["restart_required"] is True

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
        with patch(
            "az_scout.routes.plugin_manager.update_all_plugins",
            return_value=(
                2,
                0,
                [
                    {"distribution_name": "a", "ok": True},
                    {"distribution_name": "b", "ok": True},
                ],
            ),
        ):
            resp = client.post("/api/plugins/update-all")

        assert resp.status_code == 200
        data = resp.json()
        assert data["ok"] is True
        assert data["updated"] == 2
        assert data["restart_required"] is True

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
