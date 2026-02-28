"""Backward-compatible re-export – canonical module is ``signals.signal_collector``."""

from az_scout.services.signals.signal_collector import (  # noqa: F401
    _backoff_delay,
    clear_cache,
    collect_sku_signal,
    register_collection_target,
    start_collector,
    stop_collector,
)
