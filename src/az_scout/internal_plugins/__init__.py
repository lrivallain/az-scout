"""Internal plugins – built-in features structured as plugins.

Internal plugins ship inside the core ``az_scout`` package and follow the
same :class:`~az_scout.plugin_api.AzScoutPlugin` protocol as external
plugins.  The difference is registration: their routes mount at ``/api``
(preserving backward-compatible URLs) and their tabs appear before external
plugin tabs in the UI.
"""

from __future__ import annotations

import logging

from az_scout.plugin_api import AzScoutPlugin

logger = logging.getLogger(__name__)


def discover_internal_plugins() -> list[AzScoutPlugin]:
    """Return instances of all internal plugins shipped with the core package."""
    plugins: list[AzScoutPlugin] = []
    try:
        from az_scout.internal_plugins.topology import plugin as topology_plugin

        plugins.append(topology_plugin)
        logger.info("Loaded internal plugin: %s v%s", topology_plugin.name, topology_plugin.version)
    except Exception:
        logger.exception("Failed to load internal plugin: topology")
    try:
        from az_scout.internal_plugins.planner import plugin as planner_plugin

        plugins.append(planner_plugin)
        logger.info("Loaded internal plugin: %s v%s", planner_plugin.name, planner_plugin.version)
    except Exception:
        logger.exception("Failed to load internal plugin: planner")
    return plugins
