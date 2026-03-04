"""GitHub API helpers for plugin validation and installation."""

from __future__ import annotations

import logging
import re
from typing import Any

import requests

from az_scout.plugin_manager._models import GitHubRepo, PluginValidationResult

logger = logging.getLogger(__name__)

_GITHUB_RAW_BASE = "https://raw.githubusercontent.com"
_GITHUB_API_BASE = "https://api.github.com"
_GITHUB_URL_RE = re.compile(
    r"^https://github\.com/(?P<owner>[A-Za-z0-9_.-]+)/(?P<repo>[A-Za-z0-9_.-]+)/?$"
)
_SHA_RE = re.compile(r"^[0-9a-f]{40}$")


def parse_github_repo_url(repo_url: str) -> GitHubRepo | None:
    """Parse a ``https://github.com/<owner>/<repo>`` URL."""
    m = _GITHUB_URL_RE.match(repo_url.strip().rstrip("/"))
    if m:
        return GitHubRepo(owner=m.group("owner"), repo=m.group("repo"))
    return None


def is_commit_sha(ref: str) -> bool:
    """Return ``True`` if *ref* looks like a 40-hex-char commit SHA."""
    return bool(_SHA_RE.match(ref))


def fetch_raw_file(owner: str, repo: str, ref: str, path: str) -> str:
    """Fetch a raw file from a GitHub repository at the given *ref*."""
    url = f"{_GITHUB_RAW_BASE}/{owner}/{repo}/{ref}/{path}"
    resp = requests.get(url, timeout=15)
    resp.raise_for_status()
    return resp.text


def resolve_ref_to_sha(owner: str, repo: str, ref: str) -> str:
    """Resolve a tag or branch ref to its underlying commit SHA."""
    if is_commit_sha(ref):
        return ref

    url = f"{_GITHUB_API_BASE}/repos/{owner}/{repo}/git/ref/tags/{ref}"
    resp = requests.get(url, timeout=15)

    if resp.status_code == 404:
        url = f"{_GITHUB_API_BASE}/repos/{owner}/{repo}/git/ref/heads/{ref}"
        resp = requests.get(url, timeout=15)

    resp.raise_for_status()
    data: dict[str, Any] = resp.json()
    obj = data.get("object", {})
    sha: str = obj.get("sha", "")
    obj_type: str = obj.get("type", "")

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


def fetch_latest_ref(owner: str, repo: str) -> tuple[str, str]:
    """Determine the latest version ref and its resolved commit SHA.

    Returns ``(latest_ref, latest_sha)``.
    """
    release_url = f"{_GITHUB_API_BASE}/repos/{owner}/{repo}/releases/latest"
    release_resp = requests.get(release_url, timeout=15)
    if release_resp.status_code == 200:
        tag_name: str = release_resp.json().get("tag_name", "")
        if tag_name:
            sha = resolve_ref_to_sha(owner, repo, tag_name)
            return tag_name, sha

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


def parse_pyproject_toml(toml_text: str) -> dict[str, Any]:
    """Parse a TOML string and return the resulting dict."""
    import tomllib

    return tomllib.loads(toml_text)


def validate_plugin_repo(repo_url: str, ref: str = "") -> PluginValidationResult:
    """Validate that a GitHub repository is a conforming az-scout plugin."""
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

    if not ref:
        try:
            ref, _ = fetch_latest_ref(gh.owner, gh.repo)
        except Exception as exc:
            return PluginValidationResult(
                ok=False,
                owner=gh.owner,
                repo=gh.repo,
                repo_url=repo_url,
                ref="",
                errors=[f"Cannot determine latest version: {exc}"],
            )

    result = PluginValidationResult(
        ok=False,
        owner=gh.owner,
        repo=gh.repo,
        repo_url=repo_url,
        ref=ref,
    )

    try:
        result.resolved_sha = resolve_ref_to_sha(gh.owner, gh.repo, ref)
    except Exception as exc:
        result.errors.append(f"Cannot resolve ref '{ref}': {exc}")
        return result

    try:
        toml_text = fetch_raw_file(gh.owner, gh.repo, result.resolved_sha, "pyproject.toml")
    except requests.HTTPError as exc:
        result.errors.append(f"Cannot fetch pyproject.toml: {exc}")
        return result

    try:
        data = parse_pyproject_toml(toml_text)
    except Exception as exc:
        result.errors.append(f"Invalid pyproject.toml: {exc}")
        return result

    project = data.get("project", {})

    dist_name = project.get("name")
    if not dist_name:
        result.errors.append("Missing project.name in pyproject.toml")
    else:
        result.distribution_name = str(dist_name)

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

    deps = [str(d).lower() for d in project.get("dependencies", [])]
    dep_names = [re.split(r"[<>=!~\[;@ ]", d)[0].strip() for d in deps]
    if "az-scout" not in dep_names:
        result.warnings.append(
            "project.dependencies does not include 'az-scout' – plugin may fail at runtime"
        )

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
