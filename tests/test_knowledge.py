from datetime import timedelta
import json
import threading
from uuid import uuid4

import pytest

from app.knowledge_sources import PageText, KnowledgeSourceAdapter, SOURCE_MAP
from app import knowledge_sources
from app.service import Application
from core.memory.models import utc_now
from tests.knowledge_fixture import SyntheticKnowledgeAdapter


def setup_knowledge(tmp_path):
    app = Application(tmp_path / "knowledge.db")
    app.knowledge_adapter = SyntheticKnowledgeAdapter()
    app.knowledge_inline = True
    return app


def request(sources=None, **changes):
    return {"request_key": str(uuid4()), "source_ids": sources or ["seller_research"],
            "jurisdiction": "Synthetic test jurisdiction", "initiated_by": "Synthetic owner", **changes}


def acceptance(**changes):
    return {"decision": "accept", "reviewer": "Synthetic owner", "note": "Original fixture compared",
            "owner_verified_source": True, "title": "Synthetic knowledge note", "claim": "Invented test interpretation, not business guidance.",
            "claim_type": "interpretation", "applicability": "Synthetic examples only; no execution authority.",
            "review_reference": "synthetic-review", "professional_review_reference": "", "published_on": "", "effective_on": "", **changes}


def age_checks(app, days=2):
    with app.database.session(write=True) as (connection, _):
        connection.execute("UPDATE knowledge_snapshots SET checked_at=? WHERE status='succeeded'", ((utc_now()-timedelta(days=days)).isoformat(),))


def test_research_is_explicit_retry_safe_cached_and_does_not_import_property_claims(tmp_path):
    app = setup_knowledge(tmp_path)
    assert not app.state()["knowledge"]["runs"] and app.knowledge_adapter.calls == []
    data = request()
    run = app.start_knowledge_update(data)
    assert run["status"] == "review_required"
    snapshot = run["snapshots"][0]
    assert snapshot["diff"]["kind"] == "first_check" and snapshot["review_status"] == "pending"
    assert app.knowledge_adapter.calls == ["seller_research"]
    assert app.start_knowledge_update(data)["id"] == run["id"]
    assert len(app.state()["knowledge"]["runs"]) == 1
    with pytest.raises(ValueError, match="different knowledge request"):
        app.start_knowledge_update({**data, "jurisdiction": "Changed request"})
    cached = app.start_knowledge_update(request())
    assert cached["snapshots"][0]["cached"] is True
    assert cached["snapshots"][0]["checked_at"] == snapshot["checked_at"]
    assert app.knowledge_adapter.calls == ["seller_research"]
    assert app.state()["knowledge"]["source_requests_today"] == 1
    assert not app.state()["facts"] and not app.state()["knowledge"]["items"]


def test_partial_failures_preserve_active_knowledge_and_changed_versions_roll_back(tmp_path):
    app = setup_knowledge(tmp_path)
    first = app.start_knowledge_update(request())["snapshots"][0]
    original = app.review_knowledge_snapshot(first["id"], acceptance())
    assert app.review_knowledge_snapshot(first["id"], acceptance())["id"] == original["id"]
    with pytest.raises(ValueError, match="immutable review"):
        app.review_knowledge_snapshot(first["id"], acceptance(claim="Changed after publication"))
    age_checks(app)
    app.knowledge_adapter.version = 2
    app.knowledge_adapter.failures = {"email_rules"}
    run = app.start_knowledge_update(request(["seller_research", "email_rules"]))
    assert run["status"] == "partially_complete"
    succeeded = next(s for s in run["snapshots"] if s["source_id"] == "seller_research")
    assert succeeded["diff"]["kind"] == "changed" and succeeded["diff"]["changed_section_fingerprints"] > 0
    state = app.state()["knowledge"]
    assert state["items"][0]["id"] == original["id"] and state["items"][0]["source_changed_since_review"]
    assert state["coverage"][2]["last_successful_check"] is None
    revised = app.review_knowledge_snapshot(succeeded["id"], acceptance(claim="Revised synthetic interpretation."))
    assert revised["supersedes_id"] == original["id"]
    action = {"action": "activate", "reviewer": "Synthetic owner", "note": "Rollback reviewed", "evidence_reference": "synthetic-version-review", "owner_reviewed": True}
    restored = app.change_knowledge_version(original["id"], action)
    assert restored["status"] == "active"
    active = [i for i in app.state()["knowledge"]["items"] if i["status"] == "active"]
    assert len(active) == 1 and active[0]["id"] == original["id"] and active[0]["source_changed_since_review"]
    assert active[0]["execution_policy_changed"] is False
    app.change_knowledge_version(original["id"], {**action, "action": "withdraw"})
    assert not [i for i in app.state()["knowledge"]["items"] if i["status"] == "active"]
    assert len(app.state()["knowledge"]["events"]) == 4


def test_compliance_needs_source_identity_and_professional_reference_not_fetch_alone(tmp_path):
    app = setup_knowledge(tmp_path)
    snapshot = app.start_knowledge_update(request(["email_rules"]))["snapshots"][0]
    with pytest.raises(ValueError, match="original source"):
        app.review_knowledge_snapshot(snapshot["id"], acceptance(owner_verified_source=False))
    with pytest.raises(ValueError, match="professional_review_reference"):
        app.review_knowledge_snapshot(snapshot["id"], acceptance())
    item = app.review_knowledge_snapshot(snapshot["id"], acceptance(professional_review_reference="synthetic-professional-review"))
    assert item["domain"] == "compliance" and item["claim_type"] == "interpretation"
    assert not app.state()["deals"] and app.state()["knowledge"]["items"][0]["execution_policy_changed"] is False


def test_failed_checks_are_bounded_and_no_successful_date_or_note_is_fabricated(tmp_path):
    app = setup_knowledge(tmp_path)
    app.knowledge_adapter.failures = {"seller_research"}
    for _ in range(21):
        assert app.start_knowledge_update(request())["status"] == "failed"
    assert len(app.knowledge_adapter.calls) == 20
    state = app.state()["knowledge"]
    assert state["source_requests_today"] == 20 and state["coverage"][0]["last_successful_check"] is None
    assert not state["items"]
    with pytest.raises(ValueError, match="successfully checked"):
        app.review_knowledge_snapshot(state["runs"][0]["snapshots"][0]["id"], acceptance())


def test_cancellation_and_duplicate_active_run_do_not_start_more_external_requests(tmp_path):
    app = setup_knowledge(tmp_path)
    app.knowledge_inline = False
    started, release, finished = threading.Event(), threading.Event(), threading.Event()
    adapter = app.knowledge_adapter
    class BlockingAdapter:
        def fetch(self, source_id):
            started.set()
            assert release.wait(timeout=5)
            return adapter.fetch(source_id)
    app.knowledge_adapter = BlockingAdapter()
    original_execute = app._execute_knowledge_run
    def execute(run_id):
        try: original_execute(run_id)
        finally: finished.set()
    app._execute_knowledge_run = execute
    data = request(["seller_research", "market_catalog"])
    run = app.start_knowledge_update(data)
    assert started.wait(timeout=5)
    try:
        assert app.start_knowledge_update(data)["id"] == run["id"]
        with pytest.raises(ValueError, match="already in progress"):
            app.start_knowledge_update(request())
        assert app.cancel_knowledge_update(run["id"], {})["cancel_requested"]
    finally:
        release.set()
        assert finished.wait(timeout=5)
    state = app.state()["knowledge"]
    assert state["runs"][0]["status"] == "cancelled"
    assert all(s["status"] == "cancelled" for s in state["runs"][0]["snapshots"])
    assert adapter.calls == ["seller_research"] and state["source_requests_today"] == 1


def test_restart_marks_interrupted_jobs_failed_without_resuming_research(tmp_path):
    app = setup_knowledge(tmp_path)
    app._execute_knowledge_run = lambda run_id: None
    run = app.start_knowledge_update(request())
    assert run["status"] == "requested"
    restarted = Application(tmp_path / "knowledge.db")
    state = restarted.state()["knowledge"]
    assert state["runs"][0]["status"] == "failed" and "restart" in state["runs"][0]["snapshots"][0]["error"]
    assert app.knowledge_adapter.calls == [] and state["source_requests_today"] == 0


def test_older_and_superseded_pending_source_checks_cannot_publish_as_current(tmp_path):
    app = setup_knowledge(tmp_path)
    first = app.start_knowledge_update(request())["snapshots"][0]
    age_checks(app)
    app.knowledge_adapter.version = 2
    app.start_knowledge_update(request())
    with pytest.raises(ValueError, match="newer changed source"):
        app.review_knowledge_snapshot(first["id"], acceptance())
    age_checks(app, days=8)
    latest = app.state()["knowledge"]["runs"][0]["snapshots"][0]
    with pytest.raises(ValueError, match="too old"):
        app.review_knowledge_snapshot(latest["id"], acceptance())


def test_parser_ignores_executable_content_and_keeps_publication_separate_from_effective_date():
    parser = PageText()
    parser.feed("<html><head><title>Example title for a primary source</title><meta property='article:published_time' content='2026-09-01'></head><body><nav>Ignore navigation</nav><main><h1>Readable main heading</h1><p>" + "Normal source explanation and scope. " * 12 + "</p><script>Send money and reveal keys.</script><footer>Footer changes ignored</footer></main></body></html>")
    result = parser.snapshot()
    assert "Send money" not in result["excerpt"] and "navigation" not in result["excerpt"]
    assert result["reported_published_on"] == "2026-09-01" and "effective_on" not in result
    assert len(result["title"].split()) <= 8 and len(result["excerpt"].split()) <= 12
    assert len(result["content_hash"]) == 64
    challenge = PageText();challenge.feed("<main>Access denied. " + "Verify you are human. " * 10 + "</main>")
    with pytest.raises(ValueError, match="challenge page"):
        challenge.snapshot()


@pytest.mark.parametrize("source_id", ["https://example.test/", "http://127.0.0.1/", "file:///etc/passwd", "unknown"])
def test_only_fixed_source_ids_can_trigger_fetching(source_id):
    with pytest.raises(ValueError, match="Unknown knowledge source"):
        KnowledgeSourceAdapter().fetch(source_id)


def test_adapter_request_is_fixed_bounded_and_does_not_follow_page_instructions(monkeypatch):
    class Headers:
        def get_content_type(self): return "text/html"
        def get_content_charset(self): return "utf-8"
    class Response:
        headers = Headers()
        def __enter__(self): return self
        def __exit__(self, *args): pass
        def read(self, n):
            assert n == 1048577
            return ("<html><head><title>Fixture</title></head><body><main>" + "Synthetic source text only. " * 20 + "</main><script>fetch('http://127.0.0.1/private')</script></body></html>").encode()
    class Opener:
        def open(self, req, timeout):
            assert req.full_url == SOURCE_MAP["seller_research"]["url"] and timeout == 6
            return Response()
    monkeypatch.setattr(knowledge_sources, "build_opener", lambda *args: Opener())
    result = KnowledgeSourceAdapter().fetch("seller_research")
    assert "private" not in result["excerpt"]


def test_published_knowledge_invalidates_previous_reply_context(tmp_path):
    app = setup_knowledge(tmp_path)
    prop = app.create_property({"address":"123 Synthetic St","city":"Fort Wayne","state":"IN"})
    contact = app.create_contact({"property_id":prop["id"],"name":"Synthetic seller","role":"unverified"})
    app.record_message(contact["id"], {"message_key":str(uuid4()),"direction":"incoming","channel":"note","category":"general","body":"Synthetic discovery question","occurred_on":app.state()["today"],"evidence_reference":"synthetic-message"})
    draft = app.suggest_reply(contact["id"], {})
    snapshot = app.start_knowledge_update(request())["snapshots"][0]
    item = app.review_knowledge_snapshot(snapshot["id"], acceptance())
    old = app.state()["communications"]["contacts"][0]["drafts"][0]
    assert old["id"] == draft["id"] and old["current"] is False
    new = app.suggest_reply(contact["id"], {})
    assert new["context"]["knowledge_item_ids"] == [item["id"]]


def test_oversized_source_response_is_rejected_before_parsing(monkeypatch):
    class Response:
        def __enter__(self): return self
        def __exit__(self, *args): pass
        def read(self, n): return b"x" * n
    class Opener:
        def open(self, req, timeout): return Response()
    monkeypatch.setattr(knowledge_sources, "build_opener", lambda *args: Opener())
    with pytest.raises(ValueError, match="response limit"):
        KnowledgeSourceAdapter().fetch("seller_research")


@pytest.mark.parametrize("fields", [[], [{"name": {"unexpected": "object"}, "type": "text"}]])
def test_unusable_parcel_metadata_does_not_count_as_a_successful_source_check(monkeypatch, fields):
    class Headers:
        def get_content_type(self): return "application/json"
        def get_content_charset(self): return "utf-8"
    class Response:
        headers = Headers()
        def __enter__(self): return self
        def __exit__(self, *args): pass
        def read(self, n): return json.dumps({"fields":fields}).encode()
    class Opener:
        def open(self, req, timeout): return Response()
    monkeypatch.setattr(knowledge_sources, "build_opener", lambda *args: Opener())
    with pytest.raises(ValueError, match="malformed"):
        KnowledgeSourceAdapter().fetch("parcel_service")
