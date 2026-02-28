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


def load_installed() -> list[InstalledPluginRecord]:
    """Load the list of UI-installed plugins from ``installed.json``."""
    if not _INSTALLED_FILE.exists():
        return []
    try:
        raw = json.loads(_INSTALLED_FILE.read_text(encoding="utf-8"))
        return [InstalledPluginRecord(**r) for r in raw]
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
    git_url = f"git+{repo_url}.git@{sha}"

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
