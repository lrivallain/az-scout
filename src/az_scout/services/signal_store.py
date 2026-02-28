"""Backward-compatible re-export – canonical module is ``signals.signal_store``."""

from az_scout.services.signals.signal_store import (  # noqa: F401
    SignalRow,
    get_signal_count,
    get_signals,
    purge_old_signals,
    record_signal,
    record_signals_batch,
    reset_test_db,
    use_memory_db,
)
