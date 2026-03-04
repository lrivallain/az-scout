"""pip/uv wrapper for installing and uninstalling plugins."""

from __future__ import annotations

import logging
import os
import shutil
import subprocess
import sys

import az_scout.plugin_manager._storage as _storage

logger = logging.getLogger(__name__)


def _find_uv() -> str | None:
    """Return the path to the ``uv`` executable, or ``None`` if not found."""
    return shutil.which("uv")


def _pip_env() -> dict[str, str]:
    """Return an environment dict for pip/uv subprocess calls."""
    env = os.environ.copy()
    env["UV_CACHE_DIR"] = str(_storage._UV_CACHE_DIR)
    env["UV_LINK_MODE"] = "copy"
    return env


def run_pip(args: list[str]) -> subprocess.CompletedProcess[str]:
    """Run a ``pip`` command that installs/uninstalls into the plugin packages dir.

    Uses ``uv pip`` when available, otherwise falls back to ``python -m pip``.
    """
    _storage._PACKAGES_DIR.mkdir(parents=True, exist_ok=True)
    env = _pip_env()
    uv = _find_uv()
    sub_args = list(args[1:])  # drop leading "pip"

    if uv:
        cmd: list[str] = [uv, "pip", *sub_args, "--target", str(_storage._PACKAGES_DIR)]
    else:
        if sub_args and sub_args[0] == "uninstall" and "-y" not in sub_args:
            sub_args.insert(1, "-y")
        cmd = [sys.executable, "-m", "pip", *sub_args, "--target", str(_storage._PACKAGES_DIR)]

    logger.info("Running plugin pip: %s", " ".join(cmd))
    return subprocess.run(  # noqa: S603
        cmd,
        capture_output=True,
        text=True,
        env=env,
        check=False,
    )
