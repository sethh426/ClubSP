"""Bounded background scheduler for Meta-Sentra source discovery.

Disabled unless CLUBSP_META_AUTODISCOVERY=1. The scheduler only discovers and
quarantines candidates; it can never approve or activate a source.
"""
from __future__ import annotations

import os
import threading


MIN_INTERVAL_SECONDS = 3600
DEFAULT_INTERVAL_SECONDS = 21600
MAX_QUERIES_PER_CYCLE = 12


def scheduler_config(environ=None):
    env = os.environ if environ is None else environ
    enabled = str(env.get("CLUBSP_META_AUTODISCOVERY", "")).strip().lower() in {
        "1", "true", "yes", "on"
    }
    raw_interval = str(env.get("CLUBSP_META_DISCOVERY_INTERVAL_SECONDS", DEFAULT_INTERVAL_SECONDS)).strip()
    raw_queries = str(env.get("CLUBSP_META_DISCOVERY_MAX_QUERIES", 6)).strip()
    try:
        interval = int(raw_interval)
        max_queries = int(raw_queries)
    except ValueError as exc:
        raise ValueError("Meta-Sentra scheduler settings must be integers") from exc
    if interval < MIN_INTERVAL_SECONDS:
        raise ValueError("Meta-Sentra auto-discovery interval cannot be less than one hour")
    if not 1 <= max_queries <= MAX_QUERIES_PER_CYCLE:
        raise ValueError("Meta-Sentra max queries must be between 1 and 12")
    return {
        "enabled": enabled,
        "interval_seconds": interval,
        "max_queries": max_queries,
    }


class MetaSentraScheduler:
    def __init__(self, application, *, interval_seconds, max_queries):
        self.application = application
        self.interval_seconds = interval_seconds
        self.max_queries = max_queries
        self._stop = threading.Event()
        self._thread = None

    def start(self):
        if self._thread and self._thread.is_alive():
            return
        self._thread = threading.Thread(
            target=self._run,
            name="clubsp-meta-sentra-discovery",
            daemon=True,
        )
        self._thread.start()

    def stop(self):
        self._stop.set()

    def _run(self):
        # Delay the first run so process startup never blocks on external discovery.
        while not self._stop.wait(self.interval_seconds):
            try:
                self.application.meta_discovery_cycle({
                    "max_queries": self.max_queries,
                    "providers": ["data_gov", "arcgis_online", "apify_store"],
                })
            except Exception:
                # Individual provider failures are persisted by the discovery cycle.
                # A scheduler-level failure must never kill the application process.
                continue
