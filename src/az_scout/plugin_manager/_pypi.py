"""PyPI helpers for plugin validation and installation."""

from __future__ import annotations

import logging
import re
from typing import Any

import requests

from az_scout.plugin_manager._models import PluginValidationResult

logger = logging.getLogger(__name__)

_PYPI_API_BASE = "https://pypi.org/pypi"
_PYPI_PACKAGE_RE = re.compile(r"^[a-zA-Z0-9]([a-zA-Z0-9._-]*[a-zA-Z0-9])?$")


def is_pypi_source(source: str) -> bool:
    """Return ``True`` when *source* is a PyPI package name (not a URL)."""
    return not source.startswith("http") and bool(_PYPI_PACKAGE_RE.match(source.strip()))


def fetch_pypi_metadata(package_name: str, version: str = "") -> dict[str, Any]:
    """Fetch package metadata from the PyPI JSON API."""
    if version:
        url = f"{_PYPI_API_BASE}/{package_name}/{version}/json"
    else:
        url = f"{_PYPI_API_BASE}/{package_name}/json"
    resp = requests.get(url, timeout=15)
    resp.raise_for_status()
    data: dict[str, Any] = resp.json()
    return data


def validate_pypi_plugin(package_name: str, version: str = "") -> PluginValidationResult:
    """Validate that a PyPI package is a conforming az-scout plugin."""
    result = PluginValidationResult(
        ok=False,
        owner="",
        repo="",
        repo_url="",
        ref=version,
        source="pypi",
        distribution_name=package_name,
    )

    if not _PYPI_PACKAGE_RE.match(package_name):
        result.errors.append(
            f"Invalid package name '{package_name}'. "
            "Must contain only letters, digits, '.', '-', or '_'."
        )
        return result

    try:
        data = fetch_pypi_metadata(package_name, version)
    except requests.HTTPError as exc:
        if exc.response is not None and exc.response.status_code == 404:
            if version:
                result.errors.append(
                    f"Version '{version}' of package '{package_name}' not found on PyPI"
                )
            else:
                result.errors.append(f"Package '{package_name}' not found on PyPI")
        else:
            result.errors.append(f"PyPI API error: {exc}")
        return result
    except Exception as exc:
        result.errors.append(f"Cannot reach PyPI: {exc}")
        return result

    info: dict[str, Any] = data.get("info", {})
    resolved_version: str = info.get("version", version)
    result.ref = resolved_version
    result.version = resolved_version

    normalized_name = package_name.lower().replace("_", "-").replace(".", "-")
    if not normalized_name.startswith("az-scout-"):
        result.warnings.append(
            f"Package '{package_name}' does not follow the 'az-scout-*' naming convention"
        )

    requires_dist: list[str] = info.get("requires_dist") or []
    dep_names = [re.split(r"[<>=!~\[;@ ]", d)[0].strip().lower() for d in requires_dist]
    if "az-scout" not in dep_names:
        result.warnings.append(
            "Package dependencies do not include 'az-scout' — plugin may fail at runtime"
        )

    project_urls: dict[str, str] = info.get("project_urls") or {}
    if project_urls:
        homepage = project_urls.get("Homepage", "")
        if homepage:
            result.repo_url = homepage

    if not result.errors:
        result.ok = True

    return result


def fetch_pypi_latest_version(package_name: str) -> str:
    """Return the latest version string for a PyPI package."""
    try:
        data = fetch_pypi_metadata(package_name)
    except requests.HTTPError:
        msg = f"Package '{package_name}' not found on PyPI"
        raise ValueError(msg)  # noqa: B904
    version: str = data.get("info", {}).get("version", "")
    if not version:
        msg = f"Cannot determine latest version for '{package_name}'"
        raise ValueError(msg)
    return version
