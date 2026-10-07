from datetime import datetime, timedelta, timezone
import json
import threading
from urllib.error import HTTPError
from urllib.request import Request, urlopen

import pytest

from app.acquisition_automation import AcquisitionAutomationScheduler, changes, next_morning
from app.server import create_server
from app.service import Application
from tests.test_acquisition_briefs import app, transport


def settings(**extra):
    return {"enabled": True, "confirm_external_request": True,
            "client_name": "Synthetic Client", "representative_company": "Synthetic Representative", **extra}


def test_disabled_default_and_restart(app):
    assert app.acquisition_automation_cycle() is None
    assert not app.acquisition_automation_state()["enabled"]
    app.save_acquisition_automation(settings())
    restarted = Application(app.database.path)
    assert restarted.acquisition_automation_state()["policy"]["client_name"] == "Synthetic Client"


def test_background_worker_runs_without_meta_scheduler(app, monkeypatch):
    called = threading.Event()
    monkeypatch.setenv("CLUBSP_META_AUTODISCOVERY", "false")
    monkeypatch.setenv("CLUBSP_META_HEALTH_MONITORING", "false")
    app.acquisition_automation_cycle = lambda: called.set()
    scheduler = AcquisitionAutomationScheduler(app)
    scheduler.start()
    assert called.wait(2)
    scheduler.stop()
    assert not scheduler.thread.is_alive()


@pytest.mark.parametrize("data", [{"enabled": 1}, {"enabled": True}, settings(criteria={"max_price": False}),
                                  settings(generate_handoffs="yes"), settings(client_name="bad\x00"),
                                  settings(send=True), settings(representative_company=7)])
def test_invalid_settings_do_not_activate(app, data):
    with pytest.raises(ValueError):
        app.save_acquisition_automation(data)
    assert not app.acquisition_automation_state()["enabled"]


def test_real_collectors_daily_draft_cache_and_quota(app):
    calls = []
    app._sentra_transport = transport(calls)
    app.save_acquisition_automation(settings())
    first = app.acquisition_automation_cycle()
    assert first["status"] == "completed"
    assert first["result"]["handoff_status"] == "draft_not_sent"
    assert len(calls) == 2
    assert app.acquisition_automation_cycle() is None
    packet = app.representative_handoff_history()["handoffs"][0]
    assert packet["external_actions"] is False
    assert len(packet["snapshot"]["cards"]) == 1
    before = app.acquisition_automation_state()["next_due"]
    app.save_acquisition_automation(settings())
    assert app.acquisition_automation_state()["next_due"] == before
    app.save_acquisition_automation({"enabled": False})
    app.save_acquisition_automation(settings())
    again = app.acquisition_automation_cycle()
    assert again["result"]["reused"]
    assert again["result"]["handoff_id"] == packet["id"]
    assert len(calls) == 2


def test_missing_parties_do_not_block_shortlist(app):
    app._sentra_transport = transport([])
    app.save_acquisition_automation(settings(client_name="", representative_company=""))
    result = app.acquisition_automation_cycle()
    assert result["result"]["brief_id"]
    assert result["result"]["handoff_status"] == "needs_client_and_representative"
    assert not app.representative_handoff_history()["handoffs"]


def test_next_day_builds_fresh_evidence_and_disabled_drafts(app):
    calls = []
    app._sentra_transport = transport(calls)
    app.save_acquisition_automation(settings(generate_handoffs=False))
    first = app.acquisition_automation_cycle()
    state = app.acquisition_automation_state()
    due = datetime.fromisoformat(state["next_due"])
    with app.database.session(write=True) as (connection, _):
        connection.execute("UPDATE acquisition_briefs SET completed_at=?", ((datetime.now(timezone.utc)-timedelta(hours=25)).isoformat(),))
    second = app.acquisition_automation_cycle(now=due)
    assert second["result"]["brief_id"] != first["result"]["brief_id"]
    assert len(calls) == 4
    assert not second["result"]["changes"]
    assert not app.representative_handoff_history()["handoffs"]


def test_cached_evidence_can_prepare_draft_at_monthly_limit(app, monkeypatch):
    app._sentra_transport = transport([])
    app.build_acquisition_brief({})
    monkeypatch.setattr(app, "_provider_usage", lambda c, p: {"attempted_requests": 40})
    app.run_sentra = lambda data: pytest.fail("No request allowed at cap")
    app.save_acquisition_automation(settings())
    assert app.acquisition_automation_cycle()["result"]["handoff_status"] == "draft_not_sent"


@pytest.mark.parametrize("target", [50, 6.84, 0])
def test_no_qualifying_cards_never_packet(app, target):
    app._sentra_transport = transport([])
    app.save_acquisition_automation(settings(criteria={"min_yield_pct": target}))
    assert app.acquisition_automation_cycle()["result"]["handoff_status"] == "no_qualifying_cards"
    assert not app.representative_handoff_history()["handoffs"]


@pytest.mark.parametrize("env,value,status", [("CLUBSP_SENTRAS_DISABLED", "on", "paused_sources"),
    ("CLUBSP_DISABLED_SENTRAS", "rentcast_rent_estimate", "paused_sources"),
    ("RENTCAST_API_KEY", "", "paused_credentials"), ("CLUBSP_RENTCAST_MONTHLY_REQUEST_CAP", "1", "paused_budget")])
def test_pause_no_provider_calls(app, monkeypatch, env, value, status):
    calls = []
    app._sentra_transport = transport(calls)
    monkeypatch.setenv(env, value)
    app.save_acquisition_automation(settings())
    assert app.acquisition_automation_cycle()["status"] == status
    assert calls == []
    assert app.acquisition_automation_cycle() is None


def test_shared_usage_budget_and_next_month_recovery(app, monkeypatch):
    calls = []
    app._sentra_transport = transport(calls)
    monkeypatch.setattr(app, "_provider_usage", lambda c, p: {"attempted_requests": 38})
    app.save_acquisition_automation(settings())
    assert app.acquisition_automation_cycle()["status"] == "paused_budget"
    assert calls == []
    monkeypatch.setattr(app, "_provider_usage", lambda c, p: {"attempted_requests": 0})
    due = datetime.fromisoformat(app.acquisition_automation_state()["next_due"])
    assert app.acquisition_automation_cycle(now=due)["status"] == "completed"
    assert len(calls) == 2


def test_restart_lease_and_abandoned_work_reuses_packet(app):
    app._sentra_transport = transport([])
    app.save_acquisition_automation(settings())
    first = app.acquisition_automation_cycle()
    with app.database.session(write=True) as (connection, _):
        run = connection.execute("SELECT id FROM acquisition_automation_runs").fetchone()[0]
        connection.execute("UPDATE acquisition_automation SET token=?,lease_until=?,next_due=''", (run, (datetime.now(timezone.utc)+timedelta(minutes=20)).isoformat()))
        connection.execute("UPDATE acquisition_automation_runs SET status='running',completed_at='' WHERE id=?", (run,))
    restarted = Application(app.database.path)
    assert restarted.acquisition_automation_cycle() is None
    with restarted.database.session(write=True) as (connection, _):
        connection.execute("UPDATE acquisition_automation SET lease_until=''")
    restarted.run_sentra = lambda data: pytest.fail("Recovery must reuse evidence")
    recovered = restarted.acquisition_automation_cycle()
    assert recovered["result"]["handoff_id"] == first["result"]["handoff_id"]
    assert len(restarted.acquisition_automation_state()["runs"]) == 1
    assert len(restarted.representative_handoff_history()["handoffs"]) == 1


def test_concurrent_process_claim_has_one_worker(app):
    app.save_acquisition_automation(settings())
    started, release = threading.Event(), threading.Event()
    app._sentra_transport = transport([])
    original = app.build_acquisition_brief
    def build(data):
        started.set()
        assert release.wait(5)
        return original(data)
    app.build_acquisition_brief = build
    worker = threading.Thread(target=app.acquisition_automation_cycle)
    worker.start()
    assert started.wait(5)
    other = Application(app.database.path)
    assert other.acquisition_automation_cycle() is None
    release.set()
    worker.join(5)
    assert not worker.is_alive()
    assert len(app.acquisition_automation_state()["runs"]) == 1


@pytest.mark.parametrize("change", ["stop", "settings"])
def test_stop_or_settings_change_during_network_prevents_old_draft(app, change):
    app.save_acquisition_automation(settings())
    app._sentra_transport = transport([])
    original = app.build_acquisition_brief
    def build(data):
        brief = original(data)
        app.save_acquisition_automation({"enabled": False} if change == "stop" else settings(client_name="New Client"))
        return brief
    app.build_acquisition_brief = build
    assert app.acquisition_automation_cycle()["status"] == "settings_changed"
    assert not app.representative_handoff_history()["handoffs"]


def test_packet_transaction_checks_revision(app):
    app.save_acquisition_automation(settings())
    app._sentra_transport = transport([])
    original = app.prepare_representative_handoff
    def prepare(data, **kwargs):
        app.save_acquisition_automation({"enabled": False})
        return original(data, **kwargs)
    app.prepare_representative_handoff = prepare
    assert app.acquisition_automation_cycle()["status"] == "settings_changed"
    assert not app.representative_handoff_history()["handoffs"]


def test_error_redaction_and_disabled_handoffs(app):
    app.save_acquisition_automation(settings())
    def fail(data):
        raise RuntimeError("do-not-save-secret")
    app.build_acquisition_brief = fail
    assert app.acquisition_automation_cycle()["status"] == "retry_needed"
    assert "do-not-save-secret" not in json.dumps(app.acquisition_automation_state())


def test_schedule_dst_and_completion_window():
    now = datetime(2026, 10, 7, 14, tzinfo=timezone.utc)
    assert next_morning(now) == datetime(2026, 10, 8, 13, tzinfo=timezone.utc)
    assert next_morning(datetime(2026, 11, 1, 15, tzinfo=timezone.utc)) == datetime(2026, 11, 2, 14, tzinfo=timezone.utc)


def test_changes_compare_identity_not_rank_and_absence_not_sale():
    def brief(address, price):
        return {"result": {"cards": [{"listing": {"address": address, "asking_price": price}}]}}
    assert changes(brief("123 MAIN", 100), brief("123 main", 110)) == [
        {"address": "123 main", "kind": "asking_price", "before": 100, "after": 110}]
    assert changes(brief("A", 100), brief("B", 100)) == [
        {"address": "A", "kind": "absent_from_sample"}, {"address": "B", "kind": "new_in_sample"}]


def test_http_same_origin_and_no_scheduler_on_get(app):
    server = create_server(app.database.path, 0, application=app)
    thread = threading.Thread(target=server.serve_forever, daemon=True)
    thread.start()
    origin = f"http://127.0.0.1:{server.server_address[1]}"
    try:
        url = origin + "/api/sentras/automation"
        assert not json.load(urlopen(url))["enabled"]
        request = Request(url, json.dumps(settings()).encode(), {"Content-Type": "application/json", "Origin": "https://wrong.invalid"})
        with pytest.raises(HTTPError) as error:
            urlopen(request)
        assert error.value.code == 403
        request = Request(url, json.dumps(settings()).encode(), {"Content-Type": "application/json", "Origin": origin})
        assert json.load(urlopen(request))["enabled"]
        assert not app.acquisition_automation_state()["runs"]
    finally:
        server.shutdown()
        thread.join()
        server.server_close()
