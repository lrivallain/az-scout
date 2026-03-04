"""High-level plugin operations – install, uninstall, update, reconcile."""

from __future__ import annotations

import importlib.metadata
import logging
from datetime import UTC, datetime
from typing import Any

from az_scout.plugin_manager._github import (
    fetch_latest_ref,
    parse_github_repo_url,
    validate_plugin_repo,
)
from az_scout.plugin_manager._installer import run_pip
from az_scout.plugin_manager._models import InstalledPluginRecord
from az_scout.plugin_manager._pypi import (
    fetch_pypi_latest_version,
    validate_pypi_plugin,
)
from az_scout.plugin_manager._storage import (
    _PACKAGES_DIR,
    _audit_event,
    append_audit,
    load_installed,
    save_installed,
)

logger = logging.getLogger(__name__)


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

    resolved_ref = validation.ref
    sha = validation.resolved_sha or resolved_ref
    clean_url = repo_url.rstrip("/")
    if not clean_url.endswith(".git"):
        clean_url += ".git"
    git_url = f"git+{clean_url}@{sha}"

    result = run_pip(["pip", "install", git_url])
    if result.returncode != 0:
        err_msg = f"pip install failed: {result.stderr.strip()}"
        validation.errors.append(err_msg)
        _audit_event(
            "install",
            actor,
            client_ip,
            user_agent,
            repo_url=repo_url,
            ref=resolved_ref,
            resolved_sha=sha,
            distribution_name=validation.distribution_name,
            success=False,
            detail=err_msg,
        )
        return False, validation.warnings, validation.errors

    records = load_installed()
    dist_name = validation.distribution_name or ""
    records = [r for r in records if r.distribution_name != dist_name]
    records.append(
        InstalledPluginRecord(
            distribution_name=dist_name,
            repo_url=repo_url,
            ref=resolved_ref,
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
        ref=resolved_ref,
        resolved_sha=sha,
        distribution_name=dist_name,
        success=True,
        detail="installed successfully",
    )

    return True, validation.warnings, []


def install_pypi_plugin(
    package_name: str,
    version: str,
    actor: str,
    client_ip: str,
    user_agent: str,
) -> tuple[bool, list[str], list[str]]:
    """Validate and install a plugin from PyPI.

    Returns ``(ok, warnings, errors)``.
    """
    validation = validate_pypi_plugin(package_name, version)
    if not validation.ok:
        _audit_event(
            "install",
            actor,
            client_ip,
            user_agent,
            distribution_name=package_name,
            ref=version,
            success=False,
            detail="; ".join(validation.errors),
        )
        return False, validation.warnings, validation.errors

    resolved_version = validation.version or validation.ref or version
    pip_spec = f"{package_name}=={resolved_version}" if resolved_version else package_name

    pip_result = run_pip(["pip", "install", pip_spec])
    if pip_result.returncode != 0:
        err_msg = f"pip install failed: {pip_result.stderr.strip()}"
        validation.errors.append(err_msg)
        _audit_event(
            "install",
            actor,
            client_ip,
            user_agent,
            distribution_name=package_name,
            ref=resolved_version,
            success=False,
            detail=err_msg,
        )
        return False, validation.warnings, validation.errors

    records = load_installed()
    records = [r for r in records if r.distribution_name != package_name]
    records.append(
        InstalledPluginRecord(
            distribution_name=package_name,
            repo_url=validation.repo_url,
            ref=resolved_version,
            resolved_sha="",
            entry_points=validation.entry_points,
            installed_at=datetime.now(UTC).isoformat(),
            actor=actor,
            source="pypi",
        )
    )
    save_installed(records)

    _audit_event(
        "install",
        actor,
        client_ip,
        user_agent,
        distribution_name=package_name,
        ref=resolved_version,
        success=True,
        detail="installed from PyPI",
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

    result = run_pip(["pip", "uninstall", distribution_name])
    if result.returncode != 0:
        err_msg = f"pip uninstall failed: {result.stderr.strip()}"
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


def check_updates(
    actor: str,
    client_ip: str,
    user_agent: str,
) -> list[dict[str, Any]]:
    """Check all installed plugins for available updates."""
    records = load_installed()
    results: list[dict[str, Any]] = []
    now = datetime.now(UTC).isoformat()

    for record in records:
        info: dict[str, Any] = {
            "distribution_name": record.distribution_name,
            "source": record.source,
            "repo_url": record.repo_url,
            "installed_ref": record.ref,
            "resolved_sha": record.resolved_sha,
            "latest_ref": None,
            "latest_sha": None,
            "update_available": False,
            "error": None,
        }

        if record.source == "pypi":
            try:
                latest_version = fetch_pypi_latest_version(record.distribution_name)
                info["latest_ref"] = latest_version
                info["update_available"] = latest_version != record.ref
                record.last_checked_at = now
                record.latest_ref = latest_version
                record.latest_sha = None
                record.update_available = latest_version != record.ref
            except Exception as exc:
                info["error"] = str(exc)
                record.last_checked_at = now
        else:
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
                record.last_checked_at = now
                record.latest_ref = latest_ref
                record.latest_sha = latest_sha
                record.update_available = latest_sha != record.resolved_sha
            except Exception as exc:
                info["error"] = str(exc)
                record.last_checked_at = now

        results.append(info)

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


def update_plugin(
    distribution_name: str,
    actor: str,
    client_ip: str,
    user_agent: str,
) -> tuple[bool, list[str]]:
    """Update a single plugin to the latest version.

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

    if record.source == "pypi":
        return _update_pypi_plugin(record, records, actor, client_ip, user_agent)

    return _update_github_plugin(record, records, actor, client_ip, user_agent)


def _update_github_plugin(
    record: InstalledPluginRecord,
    records: list[InstalledPluginRecord],
    actor: str,
    client_ip: str,
    user_agent: str,
) -> tuple[bool, list[str]]:
    """Update a GitHub-sourced plugin to the latest release/tag."""
    errors: list[str] = []
    distribution_name = record.distribution_name

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

    clean_url = record.repo_url.rstrip("/")
    if not clean_url.endswith(".git"):
        clean_url += ".git"
    git_url = f"git+{clean_url}@{latest_sha}"

    result = run_pip(["pip", "install", "--upgrade", git_url])
    if result.returncode != 0:
        err_msg = f"pip install --upgrade failed: {result.stderr.strip()}"
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

    now = datetime.now(UTC).isoformat()
    old_ref = record.ref
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
        detail=f"Updated from {old_ref} to {latest_ref}",
    )

    return True, []


def _update_pypi_plugin(
    record: InstalledPluginRecord,
    records: list[InstalledPluginRecord],
    actor: str,
    client_ip: str,
    user_agent: str,
) -> tuple[bool, list[str]]:
    """Update a PyPI-sourced plugin to the latest version."""
    errors: list[str] = []
    distribution_name = record.distribution_name

    try:
        latest_version = fetch_pypi_latest_version(distribution_name)
    except Exception as exc:
        errors.append(f"Cannot determine latest version: {exc}")
        _audit_event(
            "update",
            actor,
            client_ip,
            user_agent,
            distribution_name=distribution_name,
            ref=record.ref,
            success=False,
            detail=errors[0],
        )
        return False, errors

    if latest_version == record.ref:
        errors.append("Already up to date")
        return False, errors

    pip_spec = f"{distribution_name}=={latest_version}"
    result = run_pip(["pip", "install", "--upgrade", pip_spec])
    if result.returncode != 0:
        err_msg = f"pip install --upgrade failed: {result.stderr.strip()}"
        errors.append(err_msg)
        _audit_event(
            "update",
            actor,
            client_ip,
            user_agent,
            distribution_name=distribution_name,
            ref=record.ref,
            success=False,
            detail=err_msg,
        )
        return False, errors

    old_ref = record.ref
    now = datetime.now(UTC).isoformat()
    record.ref = latest_version
    record.resolved_sha = ""
    record.installed_at = now
    record.actor = actor
    record.last_checked_at = now
    record.latest_ref = latest_version
    record.latest_sha = None
    record.update_available = False
    save_installed(records)

    _audit_event(
        "update",
        actor,
        client_ip,
        user_agent,
        distribution_name=distribution_name,
        ref=latest_version,
        success=True,
        detail=f"Updated from {old_ref} to {latest_version} (PyPI)",
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
        if record.source == "pypi":
            try:
                latest_version = fetch_pypi_latest_version(record.distribution_name)
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

            if latest_version == record.ref:
                details.append(
                    {
                        "distribution_name": record.distribution_name,
                        "ok": True,
                        "skipped": True,
                        "reason": "Already up to date",
                    }
                )
                continue
            latest_ref_display = latest_version
        else:
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
            latest_ref_display = latest_ref

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
                    "updated_to": latest_ref_display,
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


def _is_plugin_installed(distribution_name: str) -> bool:
    """Check whether a plugin distribution is present in the packages directory."""
    if not _PACKAGES_DIR.exists():
        return False
    str_dirs = [str(_PACKAGES_DIR)]
    for dist in importlib.metadata.distributions(path=str_dirs):
        if dist.name == distribution_name:
            return True
    return False


def reconcile_installed_plugins() -> list[dict[str, str | bool]]:
    """Re-install plugins listed in ``installed.json`` but missing from packages."""
    records = load_installed()
    if not records:
        return []

    results: list[dict[str, str | bool]] = []
    for record in records:
        if _is_plugin_installed(record.distribution_name):
            logger.debug(
                "Plugin '%s' already present in packages dir — skipping",
                record.distribution_name,
            )
            results.append(
                {
                    "distribution_name": record.distribution_name,
                    "reinstalled": False,
                    "error": "",
                }
            )
            continue

        if record.source == "pypi":
            pip_spec = (
                f"{record.distribution_name}=={record.ref}"
                if record.ref
                else record.distribution_name
            )
            logger.info(
                "Reconciling plugin '%s' — reinstalling from PyPI (%s)",
                record.distribution_name,
                pip_spec,
            )
            result = run_pip(["pip", "install", pip_spec])
        else:
            clean_url = record.repo_url.rstrip("/")
            if not clean_url.endswith(".git"):
                clean_url += ".git"
            git_url = f"git+{clean_url}@{record.resolved_sha}"
            logger.info(
                "Reconciling plugin '%s' — reinstalling from %s",
                record.distribution_name,
                git_url,
            )
            result = run_pip(["pip", "install", git_url])

        err = result.stderr.strip() if result.returncode != 0 else ""
        if result.returncode == 0:
            results.append(
                {
                    "distribution_name": record.distribution_name,
                    "reinstalled": True,
                    "error": "",
                }
            )
            logger.info("Reconciled plugin '%s' successfully", record.distribution_name)
        else:
            results.append(
                {
                    "distribution_name": record.distribution_name,
                    "reinstalled": False,
                    "error": err,
                }
            )
            logger.error(
                "Failed to reconcile plugin '%s': %s",
                record.distribution_name,
                err,
            )

        append_audit(
            {
                "action": "reconcile",
                "distribution_name": record.distribution_name,
                "repo_url": record.repo_url,
                "resolved_sha": record.resolved_sha,
                "success": result.returncode == 0,
                "detail": "reinstalled" if result.returncode == 0 else err,
            }
        )

    reinstalled = sum(1 for r in results if r["reinstalled"])
    if reinstalled:
        logger.info("Reconciled %d plugin(s) at startup", reinstalled)
    return results
