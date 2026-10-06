"""Durable, bounded Meta-Sentra discovery and health scheduling.

Scheduling never approves or activates sources. SQLite leases prevent duplicate
cycles across application processes; timing and errors survive process restarts.
"""
from __future__ import annotations

from datetime import datetime, timezone
import logging
import os
import threading
import time


MIN_INTERVAL_SECONDS = 3600
DEFAULT_INTERVAL_SECONDS = 21600
MAX_QUERIES_PER_CYCLE = 12


def _enabled(value):
    return str(value).strip().lower() in {"1", "true", "yes", "on"}


def scheduler_config(environ=None):
    env = os.environ if environ is None else environ
    try:
        interval = int(str(env.get("CLUBSP_META_DISCOVERY_INTERVAL_SECONDS", DEFAULT_INTERVAL_SECONDS)).strip())
        max_queries = int(str(env.get("CLUBSP_META_DISCOVERY_MAX_QUERIES", 6)).strip())
        health_interval = int(str(env.get("CLUBSP_META_HEALTH_INTERVAL_SECONDS", 3600)).strip())
        max_sources = int(str(env.get("CLUBSP_META_HEALTH_MAX_SOURCES", 10)).strip())
    except ValueError as exc:
        raise ValueError("Meta-Sentra scheduler settings must be integers") from exc
    if interval < MIN_INTERVAL_SECONDS or health_interval < MIN_INTERVAL_SECONDS:
        raise ValueError("Meta-Sentra scheduler intervals cannot be less than one hour")
    if not 1 <= max_queries <= MAX_QUERIES_PER_CYCLE:
        raise ValueError("Meta-Sentra max queries must be between 1 and 12")
    if not 1 <= max_sources <= 50:
        raise ValueError("Meta-Sentra max health sources must be between 1 and 50")
    return {
        "enabled": _enabled(env.get("CLUBSP_META_AUTODISCOVERY", "")),
        "interval_seconds": interval, "max_queries": max_queries,
        "health_enabled": _enabled(env.get("CLUBSP_META_HEALTH_MONITORING", "1")),
        "health_interval_seconds": health_interval, "max_health_sources": max_sources,
    }


class MetaSentraScheduler:
    def __init__(self, application, *, interval_seconds, max_queries,
                 discovery_enabled=True, health_enabled=True,
                 health_interval_seconds=3600, max_health_sources=10, clock=None):
        if interval_seconds < MIN_INTERVAL_SECONDS or health_interval_seconds < MIN_INTERVAL_SECONDS:
            raise ValueError("Meta-Sentra scheduler intervals cannot be less than one hour")
        if type(max_queries) is not int or not 1 <= max_queries <= MAX_QUERIES_PER_CYCLE:
            raise ValueError("Meta-Sentra max queries must be between 1 and 12")
        if type(max_health_sources) is not int or not 1 <= max_health_sources <= 50:
            raise ValueError("Meta-Sentra max health sources must be between 1 and 50")
        self.application = application
        self.interval_seconds, self.max_queries = interval_seconds, max_queries
        self.discovery_enabled, self.health_enabled = discovery_enabled, health_enabled
        self.health_interval_seconds, self.max_health_sources = health_interval_seconds, max_health_sources
        self.clock = clock or time.time
        self._stop, self._thread = threading.Event(), None

    def start(self):
        if self._thread and self._thread.is_alive():
            return
        self._stop.clear()
        self._thread = threading.Thread(target=self._run, name="clubsp-meta-sentra", daemon=True)
        self._thread.start()

    def stop(self):
        self._stop.set()
        if self._thread and self._thread is not threading.current_thread():
            self._thread.join(timeout=1)

    def _claim(self, name):
        now = self.clock()
        with self.application.database.session(write=True) as (connection, _):
            connection.execute("INSERT OR IGNORE INTO meta_scheduler_jobs(name) VALUES(?)", (name,))
            row = connection.execute("SELECT * FROM meta_scheduler_jobs WHERE name=?", (name,)).fetchone()
            if row["next_due"] > now or row["lease_until"] > now:
                return False
            connection.execute("""
                UPDATE meta_scheduler_jobs SET lease_until=?,last_started_at=?,last_error='' WHERE name=?
            """, (now + 3600, datetime.now(timezone.utc).isoformat(), name))
        return True

    def _finish(self, name, interval, error):
        with self.application.database.session(write=True) as (connection, _):
            connection.execute("""
                UPDATE meta_scheduler_jobs SET next_due=?,lease_until=0,last_finished_at=?,last_error=? WHERE name=?
            """, (self.clock() + interval, datetime.now(timezone.utc).isoformat(), error, name))

    def run_once(self):
        results = {}
        jobs = []
        if self.health_enabled:
            jobs.append(("health", self.health_interval_seconds,
                         lambda: self.application.meta_health_check({"max_sources": self.max_health_sources})))
        if self.discovery_enabled:
            providers = ["arcgis_online", "apify_store"]
            key = os.environ.get("DATAGOV_API_KEY", "").strip()
            if key and key != "DEMO_KEY":
                providers.insert(0, "data_gov")
            jobs.append(("discovery", self.interval_seconds,
                         lambda: self.application.meta_discovery_cycle({"max_queries": self.max_queries, "providers": providers})))
        for name, interval, action in jobs:
            if self._stop.is_set() or not self._claim(name):
                continue
            error = ""
            try:
                results[name] = action()
            except Exception as exc:
                # Do not silently lose a failed cycle or persist exception URLs/secrets.
                error = f"Meta-Sentra {name} cycle failed ({type(exc).__name__})"
                logging.warning(error)
                results[name] = {"status": "failed", "error": error}
            finally:
                self._finish(name, interval, error)
        return results

    def _run(self):
        while not self._stop.is_set():
            try:
                self.run_once()
            except Exception as exc:
                # Storage failures cannot be saved to that same unavailable storage.
                logging.warning("Meta-Sentra scheduler storage failed (%s)", type(exc).__name__)
            self._stop.wait(30)
