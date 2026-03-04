"""Plugin manager – validate, install, and uninstall az-scout plugins.

Plugins can be installed from two sources:

* **GitHub** – public repositories, pinned to a resolved commit SHA for
  reproducible builds.
* **PyPI** – standard Python packages, pinned to a specific version.

This package re-exports all public names so that existing
``from az_scout.plugin_manager import X`` imports keep working.
"""

import requests as requests  # noqa: F401  # re-export for mock patching

__all__ = [
    "requests",
    # GitHub
    "fetch_latest_ref",
    "fetch_raw_file",
    "is_commit_sha",
    "parse_github_repo_url",
    "parse_pyproject_toml",
    "resolve_ref_to_sha",
    "validate_plugin_repo",
    # Installer
    "run_pip",
    # Models
    "GitHubRepo",
    "InstalledPluginRecord",
    "PluginValidationResult",
    "RecommendedPlugin",
    # Operations
    "_is_plugin_installed",
    "check_updates",
    "install_plugin",
    "install_pypi_plugin",
    "reconcile_installed_plugins",
    "uninstall_plugin",
    "update_all_plugins",
    "update_plugin",
    # PyPI
    "fetch_pypi_latest_version",
    "fetch_pypi_metadata",
    "is_pypi_source",
    "validate_pypi_plugin",
    # Storage
    "_AUDIT_FILE",
    "_DATA_DIR",
    "_INSTALLED_FILE",
    "_PACKAGES_DIR",
    "_RECOMMENDED_FILE",
    "_UV_CACHE_DIR",
    "_audit_event",
    "_default_data_dir",
    "_ensure_data_dir",
    "_record_from_dict",
    "append_audit",
    "load_installed",
    "load_recommended_plugins",
    "save_installed",
]

# GitHub helpers
from az_scout.plugin_manager._github import (  # noqa: F401
    fetch_latest_ref,
    fetch_raw_file,
    is_commit_sha,
    parse_github_repo_url,
    parse_pyproject_toml,
    resolve_ref_to_sha,
    validate_plugin_repo,
)

# Installer (pip/uv wrapper)
from az_scout.plugin_manager._installer import (  # noqa: F401
    run_pip,
)
from az_scout.plugin_manager._models import (  # noqa: F401
    GitHubRepo,
    InstalledPluginRecord,
    PluginValidationResult,
    RecommendedPlugin,
)

# High-level operations
from az_scout.plugin_manager._operations import (  # noqa: F401
    _is_plugin_installed,
    check_updates,
    install_plugin,
    install_pypi_plugin,
    reconcile_installed_plugins,
    uninstall_plugin,
    update_all_plugins,
    update_plugin,
)

# PyPI helpers
from az_scout.plugin_manager._pypi import (  # noqa: F401
    fetch_pypi_latest_version,
    fetch_pypi_metadata,
    is_pypi_source,
    validate_pypi_plugin,
)

# Storage / persistence
from az_scout.plugin_manager._storage import (  # noqa: F401
    _AUDIT_FILE,
    _DATA_DIR,
    _INSTALLED_FILE,
    _PACKAGES_DIR,
    _RECOMMENDED_FILE,
    _UV_CACHE_DIR,
    _audit_event,
    _default_data_dir,
    _ensure_data_dir,
    _record_from_dict,
    append_audit,
    load_installed,
    load_recommended_plugins,
    save_installed,
)
