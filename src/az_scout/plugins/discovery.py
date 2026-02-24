"""Plugin discovery via Python entry points.

Plugins register themselves under the ``az_scout.plugins`` entry-point group
in their ``pyproject.toml``::

    [project.entry-points."az_scout.plugins"]
    my_plugin = "my_package:MyPlugin"

Each entry point may resolve to:

* An **instance** that satisfies :class:`~az_scout.plugins.api.AzScoutPlugin`.
* A **callable** (factory) that, when called with no arguments, returns such
  an instance.

Discovery validates the minimal manifest (``plugin_id``, ``name``,
``version``) and sorts plugins by ``priority`` (ascending – lower is first).
"""

import logging
import sys
from importlib.metadata import entry_points

from az_scout.plugins.api import AzScoutPlugin

logger = logging.getLogger(__name__)

ENTRY_POINT_GROUP = "az_scout.plugins"


def discover_plugins() -> list[AzScoutPlugin]:
    """Load and validate all registered az-scout plugins.

    Returns a list of plugin instances sorted by ``priority`` (ascending).
    Plugins that fail to load or validate are logged and skipped.
    """
    eps = entry_points(group=ENTRY_POINT_GROUP)
    plugins: list[AzScoutPlugin] = []

    for ep in eps:
        try:
            obj = ep.load()
            # If the entry point resolves to a callable (class or factory),
            # invoke it to get the plugin instance.
            if callable(obj) and not isinstance(obj, AzScoutPlugin):
                obj = obj()

            # Validate minimal manifest
            _validate_manifest(obj, ep.name)
            plugins.append(obj)
            logger.info("Loaded plugin %s v%s", obj.plugin_id, obj.version)
        except Exception:
            logger.exception("Failed to load plugin entry-point '%s'", ep.name)

    # Sort by priority (ascending – lower = loaded first)
    plugins.sort(key=lambda p: p.priority)
    return plugins


def _validate_manifest(obj: object, ep_name: str) -> None:
    """Raise if *obj* lacks the required manifest attributes."""
    for attr in ("plugin_id", "name", "version"):
        if not hasattr(obj, attr):
            msg = (
                f"Entry-point '{ep_name}' resolved to {type(obj).__name__} "
                f"which lacks required attribute '{attr}'"
            )
            raise TypeError(msg)

    # Ensure priority has a sensible default
    if not hasattr(obj, "priority"):
        object.__setattr__(obj, "priority", 100)  # type: ignore[arg-type]

    if sys.version_info >= (3, 12):  # pragma: no cover – runtime_checkable is strict in 3.12+
        return

    # Older Pythons: rely on duck-typing validated above.
