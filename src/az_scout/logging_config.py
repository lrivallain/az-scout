"""Unified logging configuration for az-scout.

Provides coloured, category-aware logging that mirrors uvicorn's output
style.  The module is imported early by both the FastAPI app and the CLI
so that all loggers (core, plugins, uvicorn, httpx, mcp) share the same
handler and format.
"""

import logging
import os

# ---------------------------------------------------------------------------
# Category filter – injects a ``category`` field into every log record
# ---------------------------------------------------------------------------


class _CategoryFilter(logging.Filter):
    """Inject a ``category`` field into every log record.

    * Loggers under ``az_scout.*``       → ``core``
    * Loggers under ``az_scout_<plugin>.*`` → ``plugin:<plugin>``
    * Loggers under ``uvicorn.*``         → ``server``
    * Loggers under ``httpx.*``           → ``http``
    * Everything else                     → ``ext``
    """

    def filter(self, record: logging.LogRecord) -> bool:
        name = record.name
        if name.startswith("az_scout_"):
            # e.g. "az_scout_batch_sku.routes" → plugin name "batch_sku"
            suffix = name[len("az_scout_") :]
            plugin_name = suffix.split(".")[0]
            record.category = f"plugin:{plugin_name}"
        elif name.startswith("az_scout"):
            record.category = "core"
        elif name.startswith("uvicorn"):
            record.category = "server"
        elif name.startswith("httpx"):
            record.category = "http"
        elif name.startswith("mcp"):
            record.category = "mcp"
        else:
            record.category = "ext"
        return True


# Shared handler & filter – reused by setup_plugin_logger()
_log_handler: logging.Handler | None = None
_log_filter: _CategoryFilter = _CategoryFilter()


def _setup_logging(level: int | None = None) -> None:
    """Configure the root ``az_scout`` logger with uvicorn-style colours.

    *level* overrides the default.  When *level* is ``None`` the function
    reads the ``AZ_SCOUT_LOG_LEVEL`` environment variable (``DEBUG``,
    ``INFO``, ``WARNING``, …) so that uvicorn reload workers inherit the
    log level set by the CLI.
    """
    global _log_handler  # noqa: PLW0603

    from uvicorn.logging import DefaultFormatter

    if level is None:
        level = getattr(logging, os.environ.get("AZ_SCOUT_LOG_LEVEL", "WARNING"))

    handler = logging.StreamHandler()
    handler.setFormatter(
        DefaultFormatter(
            fmt="%(levelprefix)s [%(category)s] %(name)s - %(message)s",
            use_colors=True,
        )
    )
    handler.addFilter(_log_filter)
    _log_handler = handler

    app_logger = logging.getLogger("az_scout")
    app_logger.handlers = [handler]
    app_logger.setLevel(level)
    app_logger.propagate = False

    # Unify uvicorn, httpx and mcp loggers under the same format
    for third_party in ("uvicorn", "uvicorn.error", "uvicorn.access", "httpx", "mcp"):
        tp_logger = logging.getLogger(third_party)
        tp_logger.handlers = [handler]
        tp_logger.propagate = False
    # uvicorn.access stays at INFO (request lines); uvicorn.error follows app level
    logging.getLogger("uvicorn").setLevel(level)
    logging.getLogger("uvicorn.error").setLevel(level)
    logging.getLogger("uvicorn.access").setLevel(logging.INFO)
    logging.getLogger("httpx").setLevel(logging.WARNING)
    logging.getLogger("mcp").setLevel(logging.INFO)

    # Silence noisy third-party loggers
    logging.getLogger("azure").setLevel(logging.WARNING)

    # Remove any stray handlers on the root logger (e.g. rich, basicConfig)
    # so that plugin loggers with propagate=False don't get duplicated.
    root = logging.getLogger()
    root.handlers.clear()
    root.setLevel(logging.WARNING)


def setup_plugin_logger(plugin_name: str) -> None:
    """Configure the ``az_scout_<plugin_name>`` logger to share the core format.

    Called automatically during plugin registration.  Plugin authors do not
    need to call this themselves — use :func:`az_scout.plugin_api.get_plugin_logger`
    to obtain a correctly-namespaced logger.
    """
    if _log_handler is None:
        return  # logging not yet initialised
    module_name = f"az_scout_{plugin_name.replace('-', '_')}"
    plugin_logger = logging.getLogger(module_name)
    if _log_handler not in plugin_logger.handlers:
        plugin_logger.handlers = [_log_handler]
    plugin_logger.setLevel(logging.getLogger("az_scout").level)
    plugin_logger.propagate = False
