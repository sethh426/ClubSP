import pytest

from app.meta_sentry_scheduler import MetaSentraScheduler, scheduler_config
from app.service import Application


def test_meta_scheduler_disabled_by_default_and_bounded():
    config = scheduler_config({})
    assert config["enabled"] is False
    assert config["health_enabled"] is True
    assert config["interval_seconds"] >= 3600
    assert 1 <= config["max_queries"] <= 12


def test_meta_scheduler_can_be_enabled():
    config = scheduler_config({
        "CLUBSP_META_AUTODISCOVERY": "true",
        "CLUBSP_META_DISCOVERY_INTERVAL_SECONDS": "7200",
        "CLUBSP_META_DISCOVERY_MAX_QUERIES": "4",
    })
    assert config == {"enabled": True, "interval_seconds": 7200, "max_queries": 4,
                      "health_enabled": True, "health_interval_seconds": 3600,
                      "max_health_sources": 10}


def test_meta_scheduler_refuses_aggressive_polling():
    with pytest.raises(ValueError, match="one hour"):
        scheduler_config({
            "CLUBSP_META_AUTODISCOVERY": "1",
            "CLUBSP_META_DISCOVERY_INTERVAL_SECONDS": "60",
        })


def test_scheduler_runs_health_even_when_discovery_is_disabled(tmp_path, monkeypatch):
    app = Application(tmp_path / "clubsp.db")
    calls = []
    monkeypatch.setattr(app, "meta_health_check", lambda data: calls.append(data) or {"checked": 0})
    scheduler = MetaSentraScheduler(app, interval_seconds=7200, max_queries=4,
                                   discovery_enabled=False, clock=lambda: 1000)
    assert scheduler.run_once() == {"health": {"checked": 0}}
    assert calls == [{"max_sources": 10}]
    assert app.meta_sentra_state()["scheduler_jobs"][0]["next_due"] == 4600


def test_scheduler_due_time_and_lease_survive_restart(tmp_path, monkeypatch):
    path = tmp_path / "clubsp.db"
    app = Application(path)
    clock = [1000]
    calls = []
    monkeypatch.setattr(app, "meta_discovery_cycle", lambda data: calls.append(data) or {"automatic_activation": False})
    scheduler = MetaSentraScheduler(app, interval_seconds=7200, max_queries=4,
                                   health_enabled=False, clock=lambda: clock[0])
    scheduler.run_once()
    assert len(calls) == 1
    restarted = Application(path)
    monkeypatch.setattr(restarted, "meta_discovery_cycle", lambda data: calls.append(data) or {})
    other = MetaSentraScheduler(restarted, interval_seconds=7200, max_queries=4,
                               health_enabled=False, clock=lambda: clock[0])
    assert other.run_once() == {}
    clock[0] = 8200
    other.run_once()
    assert len(calls) == 2
    assert scheduler._claim("health") is True
    assert other._claim("health") is False


def test_scheduler_failure_is_visible_and_redacts_exception_details(tmp_path, monkeypatch):
    app = Application(tmp_path / "clubsp.db")
    def fail(data):
        raise RuntimeError("synthetic private error material")
    monkeypatch.setattr(app, "meta_health_check", fail)
    scheduler = MetaSentraScheduler(app, interval_seconds=7200, max_queries=4,
                                   discovery_enabled=False, clock=lambda: 1000)
    assert scheduler.run_once()["health"]["status"] == "failed"
    state = Application(app.database.path).meta_sentra_state()
    job = state["scheduler_jobs"][0]
    assert "RuntimeError" in job["last_error"] and "private" not in job["last_error"]
    assert job["lease_until"] == 0


def test_scheduler_never_calls_review_or_activation(tmp_path, monkeypatch):
    app = Application(tmp_path / "clubsp.db")
    calls = []
    monkeypatch.setattr(app, "meta_health_check", lambda data: {})
    monkeypatch.setattr(app, "meta_discovery_cycle", lambda data: calls.append(data) or {})
    monkeypatch.delenv("DATAGOV_API_KEY", raising=False)
    def forbidden(data):
        pytest.fail("scheduler must never approve or activate sources")
    monkeypatch.setattr(app, "meta_review", forbidden)
    monkeypatch.setattr(app, "meta_activate", forbidden)
    scheduler = MetaSentraScheduler(app, interval_seconds=7200, max_queries=4, clock=lambda: 1000)
    scheduler.run_once()
    assert calls[0]["max_queries"] == 4
    assert calls[0]["providers"] == ["arcgis_online", "apify_store"]


def test_health_scheduler_cannot_be_configured_to_poll_aggressively():
    with pytest.raises(ValueError, match="one hour"):
        scheduler_config({"CLUBSP_META_HEALTH_INTERVAL_SECONDS": "60"})
