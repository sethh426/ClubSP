import json
import threading
from urllib.error import HTTPError
from urllib.parse import parse_qs, urlsplit
from urllib.request import Request, urlopen

import pytest

from app.gmail_inbox import ROOT, preview, sync_previews
from app.server import create_server
from app.service import Application
from tests.test_gmail import make_connection, finish


def message(message_id="abc123", **changes):
    return {"id": message_id, "threadId": "def456", "internalDate": "1700000000000",
            "snippet": "SYNTHETIC &lt;script&gt;preview&lt;/script&gt;", "payload": {"headers": [
                {"name": "From", "value": "Synthetic Seller <seller@example.test>"},
                {"name": "To", "value": "owner@example.test"},
                {"name": "Subject", "value": "=?utf-8?b?UHJpY2UgcmV2aWV3?="}]}, **changes}


def setup(tmp_path):
    app = Application(tmp_path / "app.db")
    gmail, _ = make_connection(tmp_path / "private")
    finish(gmail)
    calls = []
    def request(url, **kwargs):
        calls.append((url, kwargs))
        if url.endswith("/profile"):
            return {"emailAddress": "owner@example.test"}
        if urlsplit(url).path.endswith("/messages"):
            return {"messages": [{"id": "abc123"}], "nextPageToken": "next synthetic page"}
        return message()
    gmail.request = request
    return app, gmail, calls


def test_sync_metadata_only_pagination_duplicates_and_persistence(tmp_path):
    app, gmail, calls = setup(tmp_path)
    result = sync_previews(gmail, app, {"query": "from:seller@example.test", "page_token": "a+b&c", "limit": 5})
    assert result["inserted"] == 1 and not result["automatic_sync"] and not result["sending_enabled"]
    params = parse_qs(urlsplit(calls[1][0]).query)
    assert params["q"] == ["from:seller@example.test"] and params["pageToken"] == ["a+b&c"]
    params = parse_qs(urlsplit(calls[2][0]).query)
    assert params["format"] == ["metadata"] and set(params["metadataHeaders"]) == {"From", "To", "Subject", "Date"}
    assert all(url.startswith("https://gmail.googleapis.com/") for url, _ in calls)
    assert sync_previews(gmail, app, {})["duplicates"] == 1
    data = Application(tmp_path / "app.db").gmail_inbox()
    assert data["total"] == 1 and data["messages"][0]["subject"] == "Price review"
    assert data["messages"][0]["snippet"] == "SYNTHETIC <script>preview</script>"
    assert "synthetic-access" not in json.dumps(data)
    assert not app.state()["communications"]["contacts"]


@pytest.mark.parametrize("data", [{"limit": True}, {"limit": 11}, {"limit": 0}, {"query": 8}, {"page_token": "x" * 2049}, {"url": "https://attacker.example"}])
def test_invalid_sync_options_do_not_contact_google(tmp_path, data):
    app, gmail, calls = setup(tmp_path)
    with pytest.raises(ValueError):
        sync_previews(gmail, app, data)
    assert not calls


def test_provider_failure_leaves_inbox_unchanged(tmp_path):
    app, gmail, calls = setup(tmp_path)
    sync_previews(gmail, app, {})
    before = app.gmail_inbox()
    gmail.request = lambda *args, **kwargs: (_ for _ in ()).throw(ValueError("Synthetic failure"))
    with pytest.raises(ValueError):
        sync_previews(gmail, app, {})
    assert app.gmail_inbox() == before


def test_storage_limit_rolls_back_whole_batch(tmp_path):
    app, gmail, calls = setup(tmp_path)
    app.save_gmail_previews([preview(message(str(index)), "owner@example.test", str(index)) for index in range(999)])
    with pytest.raises(ValueError, match="full"):
        app.save_gmail_previews([preview(message(value), "owner@example.test", value) for value in ("new1", "new2")])
    assert app.gmail_inbox()["total"] == 999


def test_disconnect_during_sync_prevents_import(tmp_path):
    app, gmail, calls = setup(tmp_path)
    original = gmail.request
    def request(url, **kwargs):
        if "/messages/" in url:
            gmail.disconnect()
        return original(url, **kwargs)
    gmail.request = request
    with pytest.raises(ValueError, match="removed"):
        sync_previews(gmail, app, {})
    assert app.gmail_inbox()["total"] == 0


def test_only_one_sync_and_wrong_mailbox_rejected(tmp_path):
    app, gmail, calls = setup(tmp_path)
    gmail.sync_lock.acquire()
    with pytest.raises(ValueError, match="already running"):
        sync_previews(gmail, app, {})
    gmail.sync_lock.release()
    gmail.request = lambda *args, **kwargs: {"emailAddress": "wrong@example.test"}
    with pytest.raises(ValueError, match="mailbox"):
        sync_previews(gmail, app, {})
    assert app.gmail_inbox()["total"] == 0


@pytest.mark.parametrize("value", ["../../profile", "abc?token=x", "https://example.test"])
def test_provider_ids_cannot_change_request_destination(tmp_path, value):
    app, gmail, calls = setup(tmp_path)
    original = gmail.request
    gmail.request = lambda url, **kwargs: ({"messages": [{"id": value}]}
        if urlsplit(url).path.endswith("/messages") else original(url, **kwargs))
    with pytest.raises(ValueError, match="identity"):
        sync_previews(gmail, app, {})
    assert not any("/messages/" in url for url, _ in calls)


def test_invalid_metadata_and_partial_batch_are_not_saved(tmp_path):
    app, gmail, calls = setup(tmp_path)
    original = gmail.request
    def request(url, **kwargs):
        if urlsplit(url).path.endswith("/messages"):
            return {"messages": [{"id": "abc123"}, {"id": "abc456"}]}
        if "/abc456?" in url:
            return message("abc456", internalDate="not a timestamp")
        return original(url, **kwargs)
    gmail.request = request
    with pytest.raises(ValueError, match="date"):
        sync_previews(gmail, app, {})
    assert app.gmail_inbox()["total"] == 0
    malformed = message()
    malformed["payload"]["headers"].append({"name": "From", "value": "other@example.test"})
    with pytest.raises(ValueError):
        preview(malformed, "owner@example.test", "abc123")


def test_link_requires_matching_contact_and_does_not_grant_permission(tmp_path):
    app, gmail, calls = setup(tmp_path)
    sync_previews(gmail, app, {})
    row = app.gmail_inbox()["messages"][0]
    prop = app.create_property({"address": "123 Synthetic St", "city": "Fort Wayne", "state": "IN"})
    contact = app.create_contact({"name": "Seller", "email": "seller@example.test", "role": "unverified", "property_id": prop["id"]})
    other = app.create_contact({"name": "Other", "email": "other@example.test", "role": "unverified", "property_id": prop["id"]})
    with pytest.raises(ValueError, match="matches"):
        app.review_gmail_preview(row["id"], {"action": "link", "contact_id": other["id"]})
    app.review_gmail_preview(row["id"], {"action": "link", "contact_id": contact["id"]})
    saved = app.gmail_inbox()["messages"][0]
    assert saved["contact_id"] == contact["id"] and saved["property_id"] == prop["id"]
    state = app.state()["communications"]["contacts"]
    assert all(c["permission_status"] == "unknown" and not c["messages"] for c in state)
    app.review_gmail_preview(row["id"], {"action": "unlink"})
    assert app.gmail_inbox()["messages"][0]["contact_id"] is None
    app.review_gmail_preview(row["id"], {"action": "remove"})
    assert app.gmail_inbox()["total"] == 0 and len(app.state()["communications"]["contacts"]) == 2


def test_sync_and_review_routes_require_origin_and_do_not_expose_tokens(tmp_path):
    app, gmail, calls = setup(tmp_path)
    server = create_server(tmp_path / "app.db", port=0, application=app, gmail=gmail)
    origin = "http://127.0.0.1:" + str(server.server_address[1])
    worker = threading.Thread(target=server.serve_forever); worker.start()
    def post(path, data, include_origin=True):
        headers = {"Content-Type": "application/json"}
        if include_origin: headers["Origin"] = origin
        return urlopen(Request(origin + path, data=json.dumps(data).encode(), headers=headers))
    try:
        with pytest.raises(HTTPError) as missing:
            post("/api/gmail/sync", {}, False)
        assert missing.value.code == 403 and not calls
        with post("/api/gmail/sync", {}) as response:
            result = json.load(response)
        assert result["inserted"] == 1 and "synthetic-access" not in json.dumps(result)
        with urlopen(origin + "/api/gmail/inbox") as response:
            row = json.load(response)["messages"][0]
        with pytest.raises(HTTPError) as missing:
            post("/api/gmail/previews/" + row["id"] + "/review", {"action": "remove"}, False)
        assert missing.value.code == 403
        with post("/api/gmail/previews/" + row["id"] + "/review", {"action": "remove"}):
            pass
        assert app.gmail_inbox()["total"] == 0
    finally:
        server.shutdown(); server.server_close(); worker.join()
