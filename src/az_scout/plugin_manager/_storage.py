"""Persistence layer for installed plugins, audit log, and recommended plugins."""

from __future__ import annotations

import json
import logging
import os
import tempfile
import time
from dataclasses import asdict
from datetime import UTC, datetime
from pathlib import Path
from typing import Any

from az_scout.plugin_manager._models import InstalledPluginRecord, RecommendedPlugin

logger = logging.getLogger(__name__)


def _default_data_dir() -> Path:
    """Return the data directory, respecting the ``AZ_SCOUT_DATA_DIR`` env var."""
    env_override = os.environ.get("AZ_SCOUT_DATA_DIR")
    if env_override:
        return Path(env_override)
    return Path.home() / ".local" / "share" / "az-scout"


_DATA_DIR = _default_data_dir() / "plugins"
_INSTALLED_FILE = _DATA_DIR / "installed.json"
_AUDIT_FILE = _DATA_DIR / "audit.jsonl"
# Plugin packages directory.  Use a persistent user-local path by default
# so packages survive reboots.  In containers, AZ_SCOUT_PACKAGES_DIR can
# override this to /tmp (needed when the data volume is on Azure Files/SMB
# which does not support chmod/hardlinks).
_PACKAGES_DIR = Path(os.environ.get("AZ_SCOUT_PACKAGES_DIR", str(_default_data_dir() / "packages")))
_UV_CACHE_DIR = Path(tempfile.gettempdir()) / "az-scout-uv-cache"

_RECOMMENDED_FILE = Path(__file__).resolve().parent.parent / "recommended_plugins.json"

# Remote plugin catalog
_CATALOG_URL = "https://plugin-catalog.az-scout.com/catalog.json"
_CATALOG_CACHE_TTL = 3600  # 1 hour
_catalog_cache: tuple[float, list[dict[str, Any]]] | None = None


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
        "source",
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


def _fetch_remote_catalog() -> list[dict[str, Any]]:
    """Fetch the plugin catalog from the remote URL with caching."""
    global _catalog_cache  # noqa: PLW0603
    now = time.monotonic()
    if _catalog_cache and now - _catalog_cache[0] < _CATALOG_CACHE_TTL:
        return _catalog_cache[1]

    import requests

    try:
        resp = requests.get(_CATALOG_URL, timeout=10)
        resp.raise_for_status()
        catalog: list[dict[str, Any]] = resp.json()
        _catalog_cache = (now, catalog)
        logger.info("Fetched plugin catalog: %d plugins from %s", len(catalog), _CATALOG_URL)
        return catalog
    except Exception:
        logger.warning("Failed to fetch remote plugin catalog from %s", _CATALOG_URL)
        if _catalog_cache:
            return _catalog_cache[1]
        return []


def load_recommended_plugins() -> list[dict[str, Any]]:
    """Load the plugin catalog from the remote catalog service.

    Fetches from ``plugin-catalog.az-scout.com`` with 1-hour caching.
    """
    raw = _fetch_remote_catalog()

    installed_names = {r.distribution_name for r in load_installed()}

    results: list[dict[str, Any]] = []
    for entry in raw:
        rec = RecommendedPlugin(
            name=entry.get("name", ""),
            description=entry.get("description", ""),
            source=entry.get("source", "pypi"),
            url=entry.get("url", ""),
            version=entry.get("version", ""),
        )
        results.append(
            {
                **asdict(rec),
                "installed": rec.name in installed_names,
            }
        )
    return results


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
