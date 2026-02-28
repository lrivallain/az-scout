"""Plugin manager – validate, install, and uninstall az-scout plugins.

Only public GitHub repositories are supported.  Installation pins to a
resolved commit SHA so that builds are reproducible.  Plugins are installed
into a dedicated venv (`.venv-plugins`) to isolate them from the main
application environment.

Business logic lives here; FastAPI route handlers are thin wrappers.
"""

import json
import logging
import os
import platform
import re
import subprocess
import tempfile
import tomllib
from dataclasses import asdict, dataclass, field
from datetime import UTC, datetime
from pathlib import Path
from typing import Any

import requests

logger = logging.getLogger(__name__)

# ---------------------------------------------------------------------------
# Constants
# ---------------------------------------------------------------------------

_GITHUB_RAW_BASE = "https://raw.githubusercontent.com"
_GITHUB_API_BASE = "https://api.github.com"
_GITHUB_URL_RE = re.compile(
    r"^https://github\.com/(?P<owner>[A-Za-z0-9_.-]+)/(?P<repo>[A-Za-z0-9_.-]+)/?$"
)
_SHA_RE = re.compile(r"^[0-9a-f]{40}$")

_DATA_DIR = Path("data") / "plugins"
_INSTALLED_FILE = _DATA_DIR / "installed.json"
_AUDIT_FILE = _DATA_DIR / "audit.jsonl"
_VENV_DIR = Path(".venv-plugins")


# ---------------------------------------------------------------------------
# Data classes
# ---------------------------------------------------------------------------


@dataclass
class GitHubRepo:
    owner: str
    repo: str


@dataclass
class PluginValidationResult:
    ok: bool
    owner: str
    repo: str
    repo_url: str
    ref: str
    resolved_sha: str | None = None
    distribution_name: str | None = None
    entry_points: dict[str, str] = field(default_factory=dict)
    warnings: list[str] = field(default_factory=list)
    errors: list[str] = field(default_factory=list)


@dataclass
class InstalledPluginRecord:
    distribution_name: str
    repo_url: str
    ref: str
    resolved_sha: str
    entry_points: dict[str, str]
    installed_at: str  # ISO-8601
    actor: str
    # Update-related fields (optional for backward compatibility)
    last_checked_at: str | None = None
    latest_ref: str | None = None
    latest_sha: str | None = None
    update_available: bool | None = None


# ---------------------------------------------------------------------------
# URL / ref helpers
# ---------------------------------------------------------------------------


def parse_github_repo_url(repo_url: str) -> GitHubRepo | None:
    """Parse a ``https://github.com/<owner>/<repo>`` URL.

    Returns ``None`` when the URL does not match the expected pattern.
    """
    m = _GITHUB_URL_RE.match(repo_url.strip().rstrip("/"))
    if m:
        return GitHubRepo(owner=m.group("owner"), repo=m.group("repo"))
    return None


def is_commit_sha(ref: str) -> bool:
    """Return ``True`` if *ref* looks like a 40-hex-char commit SHA."""
    return bool(_SHA_RE.match(ref))


# ---------------------------------------------------------------------------
# GitHub helpers
# ---------------------------------------------------------------------------


def fetch_raw_file(owner: str, repo: str, ref: str, path: str) -> str:
    """Fetch a raw file from a GitHub repository at the given *ref*."""
    url = f"{_GITHUB_RAW_BASE}/{owner}/{repo}/{ref}/{path}"
    resp = requests.get(url, timeout=15)
    resp.raise_for_status()
    return resp.text


def resolve_ref_to_sha(owner: str, repo: str, ref: str) -> str:
    """Resolve a tag or branch ref to its underlying commit SHA.

    If *ref* is already a 40-char hex SHA it is returned as-is.
    For tags, annotated tags are followed until a commit object is found.
    """
    if is_commit_sha(ref):
        return ref

    # Try tag first
    url = f"{_GITHUB_API_BASE}/repos/{owner}/{repo}/git/ref/tags/{ref}"
    resp = requests.get(url, timeout=15)

    if resp.status_code == 404:
        # Fallback: try as a branch (heads)
        url = f"{_GITHUB_API_BASE}/repos/{owner}/{repo}/git/ref/heads/{ref}"
        resp = requests.get(url, timeout=15)

    resp.raise_for_status()
    data: dict[str, Any] = resp.json()
    obj = data.get("object", {})
    sha: str = obj.get("sha", "")
    obj_type: str = obj.get("type", "")

    # Follow annotated tag → commit
    while obj_type == "tag":
        tag_url: str = obj.get("url", "")
        if not tag_url:
            break
        tag_resp = requests.get(tag_url, timeout=15)
        tag_resp.raise_for_status()
        tag_data: dict[str, Any] = tag_resp.json()
        obj = tag_data.get("object", {})
        sha = obj.get("sha", "")
        obj_type = obj.get("type", "")

    if not sha:
        msg = f"Could not resolve ref '{ref}' to a commit SHA"
        raise ValueError(msg)
    return sha


# ---------------------------------------------------------------------------
# pyproject.toml parsing & validation
# ---------------------------------------------------------------------------


def parse_pyproject_toml(toml_text: str) -> dict[str, Any]:
    """Parse a TOML string and return the resulting dict."""
    return tomllib.loads(toml_text)


def validate_plugin_repo(repo_url: str, ref: str) -> PluginValidationResult:
    """Validate that a GitHub repository is a conforming az-scout plugin.

    This fetches and inspects ``pyproject.toml`` without executing any code.
    """
    gh = parse_github_repo_url(repo_url)
    if gh is None:
        return PluginValidationResult(
            ok=False,
            owner="",
            repo="",
            repo_url=repo_url,
            ref=ref,
            errors=["Invalid GitHub URL. Expected https://github.com/<owner>/<repo>"],
        )

    result = PluginValidationResult(
        ok=False,
        owner=gh.owner,
        repo=gh.repo,
        repo_url=repo_url,
        ref=ref,
    )

    # Resolve ref → SHA
    try:
        result.resolved_sha = resolve_ref_to_sha(gh.owner, gh.repo, ref)
    except Exception as exc:
        result.errors.append(f"Cannot resolve ref '{ref}': {exc}")
        return result

    # Fetch pyproject.toml
    try:
        toml_text = fetch_raw_file(gh.owner, gh.repo, result.resolved_sha, "pyproject.toml")
    except requests.HTTPError as exc:
        result.errors.append(f"Cannot fetch pyproject.toml: {exc}")
        return result

    # Parse
    try:
        data = parse_pyproject_toml(toml_text)
    except Exception as exc:
        result.errors.append(f"Invalid pyproject.toml: {exc}")
        return result

    project = data.get("project", {})

    # distribution name
    dist_name = project.get("name")
    if not dist_name:
        result.errors.append("Missing project.name in pyproject.toml")
    else:
        result.distribution_name = str(dist_name)

    # entry points
    eps = project.get("entry-points", {}).get("az_scout.plugins", {})
    if not eps:
        result.errors.append('No [project.entry-points."az_scout.plugins"] section found')
    else:
        for key, value in eps.items():
            val = str(value)
            if ":" not in val:
                result.errors.append(
                    f"Entry point '{key}' must use 'module:object' format, got '{val}'"
                )
            else:
                result.entry_points[str(key)] = val

    # dependencies – check az-scout is listed
    deps = [str(d).lower() for d in project.get("dependencies", [])]
    dep_names = [re.split(r"[<>=!~\[;@ ]", d)[0].strip() for d in deps]
    if "az-scout" not in dep_names:
        result.warnings.append(
            "project.dependencies does not include 'az-scout' – plugin may fail at runtime"
        )

    # requires-python
    req_python = project.get("requires-python", "")
    if req_python:
        if "3.11" not in str(req_python) and "3.1" not in str(req_python):
            result.warnings.append(
                f"requires-python '{req_python}' may not be compatible with >=3.11"
            )
    else:
        result.warnings.append("No requires-python specified")

    if not result.errors:
        result.ok = True

    return result


# ---------------------------------------------------------------------------
# Virtual environment management
# ---------------------------------------------------------------------------


def ensure_plugins_venv() -> Path:
    """Create the ``.venv-plugins`` virtual environment if it does not exist.

    Returns the path to the venv directory.
    """
    if not _VENV_DIR.exists():
        logger.info("Creating plugin venv at %s", _VENV_DIR)
        subprocess.run(  # noqa: S603
            ["uv", "venv", str(_VENV_DIR)],
            check=True,
            capture_output=True,
        )
    return _VENV_DIR


def _venv_env(venv_path: Path) -> dict[str, str]:
    """Return an environment dict configured for the plugin venv."""
    env = os.environ.copy()
    env["VIRTUAL_ENV"] = str(venv_path)
    if platform.system() == "Windows":
        bin_dir = str(venv_path / "Scripts")
    else:
        bin_dir = str(venv_path / "bin")
    env["PATH"] = bin_dir + os.pathsep + env.get("PATH", "")
    return env


def run_uv_in_venv(args: list[str]) -> subprocess.CompletedProcess[str]:
    """Run a ``uv`` command inside the plugin venv."""
    venv_path = ensure_plugins_venv()
    env = _venv_env(venv_path)
    cmd = ["uv", *args]
    logger.info("Running in plugin venv: %s", " ".join(cmd))
    return subprocess.run(  # noqa: S603
        cmd,
        capture_output=True,
        text=True,
        env=env,
        check=False,
    )


# ---------------------------------------------------------------------------
# Persistence
# ---------------------------------------------------------------------------


def _ensure_data_dir() -> None:
    _DATA_DIR.mkdir(parents=True, exist_ok=True)


def _record_from_dict(data: dict[str, Any]) -> InstalledPluginRecord:
    """Create an ``InstalledPluginRecord`` from a dict, tolerating missing fields."""
    known_fields = {
        "distribution_name",
        "repo_url",
        "ref",
        "resolved_sha",
        "entry_points",
        "installed_at",
        "actor",
        "last_checked_at",
        "latest_ref",
        "latest_sha",
        "update_available",
    }
    filtered = {k: v for k, v in data.items() if k in known_fields}
    return InstalledPluginRecord(**filtered)


def load_installed() -> list[InstalledPluginRecord]:
    """Load the list of UI-installed plugins from ``installed.json``."""
    if not _INSTALLED_FILE.exists():
        return []
    try:
        raw = json.loads(_INSTALLED_FILE.read_text(encoding="utf-8"))
        return [_record_from_dict(r) for r in raw]
    except Exception:
        logger.exception("Failed to read %s", _INSTALLED_FILE)
        return []


def save_installed(records: list[InstalledPluginRecord]) -> None:
    """Atomically write the installed plugins list."""
    _ensure_data_dir()
    data = [asdict(r) for r in records]
    fd, tmp_path = tempfile.mkstemp(dir=str(_DATA_DIR), prefix=".installed-", suffix=".tmp")
    try:
        with os.fdopen(fd, "w", encoding="utf-8") as f:
            json.dump(data, f, indent=2)
        Path(tmp_path).replace(_INSTALLED_FILE)
    except Exception:
        Path(tmp_path).unlink(missing_ok=True)
        raise


def append_audit(event: dict[str, Any]) -> None:
    """Append an audit entry to the JSONL audit log."""
    _ensure_data_dir()
    event["timestamp"] = datetime.now(UTC).isoformat()
    with _AUDIT_FILE.open("a", encoding="utf-8") as f:
        f.write(json.dumps(event, default=str) + "\n")


# ---------------------------------------------------------------------------
# Install / Uninstall operations
# ---------------------------------------------------------------------------


def install_plugin(
    repo_url: str,
    ref: str,
    actor: str,
    client_ip: str,
    user_agent: str,
) -> tuple[bool, list[str], list[str]]:
    """Validate and install a plugin from a GitHub repository.

    Returns ``(ok, warnings, errors)``.
    """
    validation = validate_plugin_repo(repo_url, ref)
    if not validation.ok:
        _audit_event(
            "install",
            actor,
            client_ip,
            user_agent,
            repo_url=repo_url,
            ref=ref,
            resolved_sha=validation.resolved_sha,
            distribution_name=validation.distribution_name,
            success=False,
            detail="; ".join(validation.errors),
        )
        return False, validation.warnings, validation.errors

    sha = validation.resolved_sha or ref
    clean_url = repo_url.rstrip("/")
    if not clean_url.endswith(".git"):
        clean_url += ".git"
    git_url = f"git+{clean_url}@{sha}"

    result = run_uv_in_venv(["pip", "install", git_url])
    if result.returncode != 0:
        err_msg = f"uv pip install failed: {result.stderr.strip()}"
        validation.errors.append(err_msg)
        _audit_event(
            "install",
            actor,
            client_ip,
            user_agent,
            repo_url=repo_url,
            ref=ref,
            resolved_sha=sha,
            distribution_name=validation.distribution_name,
            success=False,
            detail=err_msg,
        )
        return False, validation.warnings, validation.errors

    # Update installed.json
    records = load_installed()
    dist_name = validation.distribution_name or ""
    # Remove previous entry for the same distribution (upgrade scenario)
    records = [r for r in records if r.distribution_name != dist_name]
    records.append(
        InstalledPluginRecord(
            distribution_name=dist_name,
            repo_url=repo_url,
            ref=ref,
            resolved_sha=sha,
            entry_points=validation.entry_points,
            installed_at=datetime.now(UTC).isoformat(),
            actor=actor,
        )
    )
    save_installed(records)

    _audit_event(
        "install",
        actor,
        client_ip,
        user_agent,
        repo_url=repo_url,
        ref=ref,
        resolved_sha=sha,
        distribution_name=dist_name,
        success=True,
        detail="installed successfully",
    )

    return True, validation.warnings, []


# ---------------------------------------------------------------------------
# GitHub latest-version helpers
# ---------------------------------------------------------------------------


def fetch_latest_ref(owner: str, repo: str) -> tuple[str, str]:
    """Determine the latest version ref and its resolved commit SHA.

    Strategy:
      1. Try ``GET /repos/{owner}/{repo}/releases/latest`` → ``tag_name``.
      2. If no release, try ``GET /repos/{owner}/{repo}/tags?per_page=1``.
      3. Resolve the ref → 40-char commit SHA.

    Returns ``(latest_ref, latest_sha)``.
    Raises ``ValueError`` when no release or tag is found.
    """
    # 1. Try latest release
    release_url = f"{_GITHUB_API_BASE}/repos/{owner}/{repo}/releases/latest"
    release_resp = requests.get(release_url, timeout=15)
    if release_resp.status_code == 200:
        tag_name: str = release_resp.json().get("tag_name", "")
        if tag_name:
            sha = resolve_ref_to_sha(owner, repo, tag_name)
            return tag_name, sha

    # 2. Fallback to latest tag
    tags_url = f"{_GITHUB_API_BASE}/repos/{owner}/{repo}/tags?per_page=1"
    tags_resp = requests.get(tags_url, timeout=15)
    if tags_resp.status_code == 200:
        tags: list[dict[str, Any]] = tags_resp.json()
        if tags:
            tag_name = tags[0].get("name", "")
            if tag_name:
                sha = resolve_ref_to_sha(owner, repo, tag_name)
                return tag_name, sha

    msg = f"No releases or tags found for {owner}/{repo}"
    raise ValueError(msg)


# ---------------------------------------------------------------------------
# Check for updates
# ---------------------------------------------------------------------------


def check_updates(
    actor: str,
    client_ip: str,
    user_agent: str,
) -> list[dict[str, Any]]:
    """Check all installed plugins for available updates.

    Returns a list of dicts with update status per plugin.
    """
    records = load_installed()
    results: list[dict[str, Any]] = []
    now = datetime.now(UTC).isoformat()

    for record in records:
        info: dict[str, Any] = {
            "distribution_name": record.distribution_name,
            "repo_url": record.repo_url,
            "installed_ref": record.ref,
            "resolved_sha": record.resolved_sha,
            "latest_ref": None,
            "latest_sha": None,
            "update_available": False,
            "error": None,
        }

        gh = parse_github_repo_url(record.repo_url)
        if gh is None:
            info["error"] = "Invalid GitHub URL"
            results.append(info)
            continue

        try:
            latest_ref, latest_sha = fetch_latest_ref(gh.owner, gh.repo)
            info["latest_ref"] = latest_ref
            info["latest_sha"] = latest_sha
            info["update_available"] = latest_sha != record.resolved_sha

            # Update the record in-place for persistence
            record.last_checked_at = now
            record.latest_ref = latest_ref
            record.latest_sha = latest_sha
            record.update_available = latest_sha != record.resolved_sha
        except Exception as exc:
            info["error"] = str(exc)
            record.last_checked_at = now

        results.append(info)

    # Persist updated records
    save_installed(records)

    _audit_event(
        "check_updates",
        actor,
        client_ip,
        user_agent,
        success=True,
        detail=f"Checked {len(records)} plugin(s)",
    )

    return results


# ---------------------------------------------------------------------------
# Update operations
# ---------------------------------------------------------------------------


def update_plugin(
    distribution_name: str,
    actor: str,
    client_ip: str,
    user_agent: str,
) -> tuple[bool, list[str]]:
    """Update a single plugin to the latest GitHub release/tag.

    Returns ``(ok, errors)``.
    """
    errors: list[str] = []
    records = load_installed()
    record = next((r for r in records if r.distribution_name == distribution_name), None)

    if record is None:
        errors.append(f"Plugin '{distribution_name}' not found in installed list")
        _audit_event(
            "update",
            actor,
            client_ip,
            user_agent,
            distribution_name=distribution_name,
            success=False,
            detail=errors[0],
        )
        return False, errors

    gh = parse_github_repo_url(record.repo_url)
    if gh is None:
        errors.append("Invalid GitHub URL in installed record")
        _audit_event(
            "update",
            actor,
            client_ip,
            user_agent,
            distribution_name=distribution_name,
            repo_url=record.repo_url,
            success=False,
            detail=errors[0],
        )
        return False, errors

    # Resolve latest version
    try:
        latest_ref, latest_sha = fetch_latest_ref(gh.owner, gh.repo)
    except Exception as exc:
        errors.append(f"Cannot determine latest version: {exc}")
        _audit_event(
            "update",
            actor,
            client_ip,
            user_agent,
            distribution_name=distribution_name,
            repo_url=record.repo_url,
            ref=record.ref,
            resolved_sha=record.resolved_sha,
            success=False,
            detail=errors[0],
        )
        return False, errors

    if latest_sha == record.resolved_sha:
        errors.append("Already up to date")
        return False, errors

    # Install the new version
    clean_url = record.repo_url.rstrip("/")
    if not clean_url.endswith(".git"):
        clean_url += ".git"
    git_url = f"git+{clean_url}@{latest_sha}"

    result = run_uv_in_venv(["pip", "install", "--upgrade", git_url])
    if result.returncode != 0:
        err_msg = f"uv pip install --upgrade failed: {result.stderr.strip()}"
        errors.append(err_msg)
        _audit_event(
            "update",
            actor,
            client_ip,
            user_agent,
            repo_url=record.repo_url,
            ref=record.ref,
            resolved_sha=record.resolved_sha,
            distribution_name=distribution_name,
            success=False,
            detail=err_msg,
        )
        return False, errors

    # Update the record
    now = datetime.now(UTC).isoformat()
    record.ref = latest_ref
    record.resolved_sha = latest_sha
    record.installed_at = now
    record.actor = actor
    record.last_checked_at = now
    record.latest_ref = latest_ref
    record.latest_sha = latest_sha
    record.update_available = False
    save_installed(records)

    _audit_event(
        "update",
        actor,
        client_ip,
        user_agent,
        repo_url=record.repo_url,
        ref=latest_ref,
        resolved_sha=latest_sha,
        distribution_name=distribution_name,
        success=True,
        detail=f"Updated from {record.ref} to {latest_ref}",
    )

    return True, []


def update_all_plugins(
    actor: str,
    client_ip: str,
    user_agent: str,
) -> tuple[int, int, list[dict[str, Any]]]:
    """Update all installed plugins that have available updates.

    Returns ``(updated_count, failed_count, details)``.
    """
    records = load_installed()
    updated = 0
    failed = 0
    details: list[dict[str, Any]] = []

    for record in records:
        gh = parse_github_repo_url(record.repo_url)
        if gh is None:
            details.append(
                {
                    "distribution_name": record.distribution_name,
                    "ok": False,
                    "error": "Invalid GitHub URL",
                }
            )
            failed += 1
            continue

        try:
            latest_ref, latest_sha = fetch_latest_ref(gh.owner, gh.repo)
        except Exception as exc:
            details.append(
                {
                    "distribution_name": record.distribution_name,
                    "ok": False,
                    "error": str(exc),
                }
            )
            failed += 1
            continue

        if latest_sha == record.resolved_sha:
            details.append(
                {
                    "distribution_name": record.distribution_name,
                    "ok": True,
                    "skipped": True,
                    "reason": "Already up to date",
                }
            )
            continue

        ok, errors = update_plugin(
            record.distribution_name,
            actor,
            client_ip,
            user_agent,
        )
        if ok:
            updated += 1
            details.append(
                {
                    "distribution_name": record.distribution_name,
                    "ok": True,
                    "updated_to": latest_ref,
                }
            )
        else:
            failed += 1
            details.append(
                {
                    "distribution_name": record.distribution_name,
                    "ok": False,
                    "error": "; ".join(errors),
                }
            )

    _audit_event(
        "update_all",
        actor,
        client_ip,
        user_agent,
        success=failed == 0,
        detail=f"Updated {updated}, failed {failed} of {len(records)} plugin(s)",
    )

    return updated, failed, details


def uninstall_plugin(
    distribution_name: str,
    actor: str,
    client_ip: str,
    user_agent: str,
) -> tuple[bool, list[str]]:
    """Uninstall a plugin by its distribution name.

    Returns ``(ok, errors)``.
    """
    errors: list[str] = []
    records = load_installed()
    record = next((r for r in records if r.distribution_name == distribution_name), None)

    if record is None:
        errors.append(f"Plugin '{distribution_name}' not found in installed list")
        _audit_event(
            "uninstall",
            actor,
            client_ip,
            user_agent,
            distribution_name=distribution_name,
            success=False,
            detail=errors[0],
        )
        return False, errors

    result = run_uv_in_venv(["pip", "uninstall", distribution_name])
    if result.returncode != 0:
        err_msg = f"uv pip uninstall failed: {result.stderr.strip()}"
        errors.append(err_msg)
        _audit_event(
            "uninstall",
            actor,
            client_ip,
            user_agent,
            distribution_name=distribution_name,
            success=False,
            detail=err_msg,
        )
        return False, errors

    # Update installed.json
    records = [r for r in records if r.distribution_name != distribution_name]
    save_installed(records)

    _audit_event(
        "uninstall",
        actor,
        client_ip,
        user_agent,
        distribution_name=distribution_name,
        repo_url=record.repo_url,
        ref=record.ref,
        resolved_sha=record.resolved_sha,
        success=True,
        detail="uninstalled successfully",
    )

    return True, []


# ---------------------------------------------------------------------------
# Audit helper
# ---------------------------------------------------------------------------


def _audit_event(
    action: str,
    actor: str,
    client_ip: str,
    user_agent: str,
    *,
    repo_url: str = "",
    ref: str = "",
    resolved_sha: str | None = None,
    distribution_name: str | None = None,
    success: bool,
    detail: str = "",
) -> None:
    append_audit(
        {
            "action": action,
            "actor": actor,
            "client_ip": client_ip,
            "user_agent": user_agent,
            "repo_url": repo_url,
            "ref": ref,
            "resolved_sha": resolved_sha,
            "distribution_name": distribution_name,
            "success": success,
            "detail": detail,
        }
    )
