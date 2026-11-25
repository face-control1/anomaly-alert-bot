"""Общее состояние фонового сканера для health-check и мониторинга."""
from __future__ import annotations

import threading
import time

_lock = threading.Lock()
_state = {
    "started_at": time.time(),
    "last_scan_at": None,
    "last_success_at": None,
    "ticker_count": 0,
    "scan_duration_seconds": None,
    "consecutive_failures": 0,
    "last_error": None,
}


def update_health(**values) -> None:
    with _lock:
        _state.update(values)


def get_health() -> dict:
    with _lock:
        return dict(_state)
