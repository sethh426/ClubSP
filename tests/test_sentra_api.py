from http.cookies import SimpleCookie
import json
import threading
from urllib.error import HTTPError
from urllib.request import Request, urlopen

import pytest

from app.auth import OwnerAuth, SESSION_COOKIE
from app.server import create_server
from app.service import Application
from tests.sentra_helpers import activate_synthetic


def test_kernel_endpoints_enforce_auth_origin_and_return_measured_state(tmp_path, monkeypatch):
    app = Application(tmp_path / "app.db")
    activate_synthetic(app, monkeypatch)
    server = create_server(app.database.path, port=0, application=app,
                           auth=OwnerAuth(secret="synthetic-owner-secret-1234"))
    origin = "http://127.0.0.1:" + str(server.server_address[1])
    worker = threading.Thread(target=server.serve_forever, daemon=True)
    worker.start()
    try:
        with pytest.raises(HTTPError) as denied:
            urlopen(origin + "/api/sentras")
        assert denied.value.code == 401
        login = Request(origin + "/api/auth/login", data=json.dumps({"secret": "synthetic-owner-secret-1234"}).encode(),
                        headers={"Content-Type": "application/json", "Origin": origin}, method="POST")
        with urlopen(login) as response:
            cookie = SimpleCookie(response.headers["Set-Cookie"])
        session = SESSION_COOKIE + "=" + cookie[SESSION_COOKIE].value
        def post(path, data, with_origin=True):
            headers = {"Content-Type": "application/json", "Cookie": session}
            if with_origin:
                headers["Origin"] = origin
            with urlopen(Request(origin + path, data=json.dumps(data).encode(), headers=headers, method="POST")) as response:
                return json.load(response)
        with pytest.raises(HTTPError) as mismatch:
            post("/api/sentras/run", {"sentra_id": "example_sales", "request_key": "http-first-run"}, False)
        assert mismatch.value.code == 403
        with urlopen(Request(origin + "/api/sentras", headers={"Cookie": session})) as response:
            catalog = json.load(response)
        entry = next(r for r in catalog["sentras"] if r["id"] == "example_sales")
        assert entry["health"] == "never_run"
        assert entry["execution_ready"]
        routed = post("/api/sentras/route", {"capability": "sale_event", "market": "Example County"})
        assert "example_sales" in {r["id"] for r in routed["routes"]}
        assert not routed["coverage_confirmed"]
        first = post("/api/sentras/run", {"sentra_id": "example_sales", "request_key": "http-first-run"})
        assert first["status"] == "success"
        assert post("/api/sentras/run", {"sentra_id": "example_sales", "request_key": "http-first-run"})["replayed"]
        assert post("/api/sentras/meta/health", {"max_sources": 1})["results"][0]["status"] == "healthy"
        assert not app.state()["facts"]
    finally:
        server.shutdown()
        server.server_close()
        worker.join()
