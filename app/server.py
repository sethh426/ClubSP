from __future__ import annotations

import argparse
from http.server import BaseHTTPRequestHandler, ThreadingHTTPServer
from http.cookies import SimpleCookie, CookieError
import json
from pathlib import Path
import sqlite3
from urllib.parse import urlsplit, parse_qs

from .database import dumps
from .service import Application
from .gmail import GmailConnection, load_local_environment, CALLBACK
from .gmail_inbox import sync_previews
from .gmail_send import send_approved_draft
from .funding import FundingBook
from .relationships import RelationshipBook
from .command_center import build_command_center

STATIC = Path(__file__).with_name("static")
ASSETS = {
    "/relationships": ("relationships.html", "text/html; charset=utf-8"),
    "/relationships.js": ("relationships.js", "text/javascript; charset=utf-8"),
    "/relationships.css": ("relationships.css", "text/css; charset=utf-8"),
    "/command": ("command.html", "text/html; charset=utf-8"),
    "/command.js": ("command.js", "text/javascript; charset=utf-8"),
    "/command.css": ("command.css", "text/css; charset=utf-8"),
    "/": ("index.html", "text/html; charset=utf-8"),
    "/app.js": ("app.js", "text/javascript; charset=utf-8"),
    "/workspace.js": ("workspace.js", "text/javascript; charset=utf-8"),
    "/sourcing.js": ("sourcing.js", "text/javascript; charset=utf-8"),
    "/communications.js": ("communications.js", "text/javascript; charset=utf-8"),
    "/knowledge.js": ("knowledge.js", "text/javascript; charset=utf-8"),
    "/discovery.js": ("discovery.js", "text/javascript; charset=utf-8"),
    "/commitment.js": ("commitment.js", "text/javascript; charset=utf-8"),
    "/gmail.js": ("gmail.js", "text/javascript; charset=utf-8"),
    "/style.css": ("style.css", "text/css; charset=utf-8"),
    "/funding": ("funding.html", "text/html; charset=utf-8"),
    "/funding.js": ("funding.js", "text/javascript; charset=utf-8"),
    "/funding.css": ("funding.css", "text/css; charset=utf-8"),
}
MAX_BODY = 65536


def reject_constant(value):
    raise ValueError("Nonfinite JSON numbers are not supported")


def handler_for(application, gmail):
    funding = FundingBook(application.database)
    relationships = RelationshipBook(application)
    class Handler(BaseHTTPRequestHandler):
        def send_content(self, code, body, content_type="application/json; charset=utf-8"):
            if isinstance(body, str):
                body = body.encode("utf-8")
            self.send_response(code)
            self.send_header("Content-Type", content_type)
            self.send_header("Content-Length", str(len(body)))
            self.send_header("Cache-Control", "no-store")
            self.send_header("X-Content-Type-Options", "nosniff")
            self.send_header("Referrer-Policy", "no-referrer")
            self.send_header(
                "Content-Security-Policy",
                "default-src 'self'; script-src 'self'; style-src 'self'; "
                "connect-src 'self'; img-src 'self'; object-src 'none'; "
                "frame-ancestors 'none'; base-uri 'none'; form-action 'self'",
            )
            self.end_headers()
            self.wfile.write(body)

        def send_json(self, code, data):
            self.send_content(code, dumps(data))

        def valid_host(self):
            port = self.server.server_address[1]
            return self.headers.get("Host") in {
                f"127.0.0.1:{port}", f"localhost:{port}",
            }

        def gmail_origin(self):
            return "http://" + self.headers["Host"]

        def relationship_state(self):
            state = relationships.state()
            send_enabled = gmail.status(self.gmail_origin())["sending_enabled"]
            state["sending_enabled"] = send_enabled
            for relationship in state["relationships"]:
                for draft in relationship["saved_drafts"]:
                    draft["sending_enabled"] = bool(draft["sending_enabled"] and send_enabled)
            return state

        def redirect(self, target, cookie):
            self.send_response(303)
            self.send_header("Location", target)
            self.send_header("Set-Cookie", cookie)
            self.send_header("Cache-Control", "no-store")
            self.send_header("Referrer-Policy", "no-referrer")
            self.send_header("Content-Length", "0")
            self.end_headers()

        def do_GET(self):
            if not self.valid_host():
                self.send_json(403, {"error": "Use the local application URL"})
                return
            path = urlsplit(self.path).path
            if path == "/api/discovery":
                self.send_json(200, application.discovery_state())
            elif path == "/api/gmail/status":
                self.send_json(200, gmail.status(self.gmail_origin()))
            elif path == "/api/gmail/inbox":
                try:
                    self.send_json(200, application.gmail_inbox())
                except sqlite3.Error:
                    self.send_json(503, {"error": "Database temporarily unavailable"})
            elif path == CALLBACK:
                clear_cookie = "clubsp_gmail=; Path=/auth/gmail; HttpOnly; SameSite=Lax; Max-Age=0"
                try:
                    if len(self.path) > 8192:
                        raise ValueError("Google callback is too large")
                    query = parse_qs(urlsplit(self.path).query, keep_blank_values=True, max_num_fields=12)
                    if any(len(values) != 1 for values in query.values()):
                        raise ValueError("Duplicate callback parameters")
                    cookie = SimpleCookie(self.headers.get("Cookie", ""))
                    browser_state = cookie.get("clubsp_gmail")
                    gmail.complete({k: v[0] for k, v in query.items()},
                                   browser_state.value if browser_state else "", self.gmail_origin())
                    self.redirect("/?gmail=connected", clear_cookie)
                except (ValueError, TypeError, CookieError):
                    self.redirect("/?gmail=failed", clear_cookie)
                except OSError:
                    self.redirect("/?gmail=storage_failed", clear_cookie)
            elif path in ASSETS:
                name, content_type = ASSETS[path]
                self.send_content(200, (STATIC / name).read_bytes(), content_type)
            elif path == "/api/health":
                self.send_json(200, {"status": "ok", "mode": "local"})
            elif path == "/api/state":
                try:
                    self.send_json(200, application.state())
                except sqlite3.Error:
                    self.send_json(503, {"error": "Database temporarily unavailable"})
            elif path == "/api/relationships":
                try:
                    self.send_json(200, self.relationship_state())
                except sqlite3.Error:
                    self.send_json(503, {"error": "Database temporarily unavailable"})
            elif path == "/api/command-center":
                try:
                    workspace = application.state()
                    self.send_json(200, build_command_center(
                        workspace, funding.state(workspace), self.relationship_state()
                    ))
                except sqlite3.Error:
                    self.send_json(503, {"error": "Database temporarily unavailable"})
            elif path == "/api/funding":
                try:
                    self.send_json(200, funding.state(application.state()))
                except sqlite3.Error:
                    self.send_json(503, {"error": "Database temporarily unavailable"})
            else:
                self.send_json(404, {"error": "Not found"})

        def do_POST(self):
            if not self.valid_host():
                self.send_json(403, {"error": "Use the local application URL"})
                return
            origin = self.headers.get("Origin")
            if origin and origin != "http://" + self.headers["Host"]:
                self.send_json(403, {"error": "Cross-origin requests are not allowed"})
                return
            if self.headers.get("Content-Type", "").split(";")[0].strip() != "application/json":
                self.send_json(415, {"error": "Use application/json"})
                return
            if self.headers.get("Transfer-Encoding"):
                self.send_json(400, {"error": "Transfer-Encoding is not supported"})
                return
            try:
                length = int(self.headers.get("Content-Length", "0"))
                if not 0 < length <= MAX_BODY:
                    self.send_json(413, {"error": "Request body must be between 1 and 65536 bytes"})
                    return
                data = json.loads(self.rfile.read(length), parse_constant=reject_constant)
                if not isinstance(data, dict):
                    raise ValueError("Request body must be a JSON object")
                path = urlsplit(self.path).path
                if path == "/api/discovery/intake":
                    if origin != self.gmail_origin():
                        self.send_json(403, {"error": "A matching Origin is required"})
                        return
                    result = application.stage_discovery_intake(data)
                elif path == "/api/discovery/check":
                    if origin != self.gmail_origin():
                        self.send_json(403, {"error": "A matching Origin is required"})
                        return
                    result = application.check_discovery(data)
                elif path == "/api/gmail/connect":
                    if origin != self.gmail_origin():
                        self.send_json(403, {"error": "Open Connect Gmail from the ClubSP workspace"})
                        return
                    if data != {}:
                        raise ValueError("Connection request must be empty")
                    url, state = gmail.begin(self.gmail_origin())
                    self.send_response(200)
                    body = dumps({"authorization_url": url}).encode()
                    self.send_header("Content-Type", "application/json")
                    self.send_header("Content-Length", str(len(body)))
                    self.send_header("Cache-Control", "no-store")
                    self.send_header("Set-Cookie", "clubsp_gmail=" + state + "; Path=/auth/gmail; HttpOnly; SameSite=Lax; Max-Age=600")
                    self.end_headers()
                    self.wfile.write(body)
                    return
                elif path == "/api/gmail/enable-send":
                    if origin != self.gmail_origin():
                        self.send_json(403, {"error": "Enable Gmail sending from the ClubSP workspace"})
                        return
                    if data != {}:
                        raise ValueError("Send-permission request must be empty")
                    url, state = gmail.begin(self.gmail_origin(), include_send=True)
                    self.send_response(200)
                    body = dumps({"authorization_url": url}).encode()
                    self.send_header("Content-Type", "application/json")
                    self.send_header("Content-Length", str(len(body)))
                    self.send_header("Cache-Control", "no-store")
                    self.send_header("Set-Cookie", "clubsp_gmail=" + state + "; Path=/auth/gmail; HttpOnly; SameSite=Lax; Max-Age=600")
                    self.end_headers()
                    self.wfile.write(body)
                    return
                elif path == "/api/gmail/refresh":
                    if origin != self.gmail_origin():
                        self.send_json(403, {"error": "Refresh Gmail from the ClubSP workspace"})
                        return
                    if data != {}:
                        raise ValueError("Refresh request must be empty")
                    gmail.ensure_access_token(force=True)
                    result = gmail.status(self.gmail_origin())
                elif path == "/api/gmail/sync":
                    if origin != self.gmail_origin():
                        self.send_json(403, {"error": "Sync Gmail from the ClubSP workspace"})
                        return
                    result = sync_previews(gmail, application, data)
                elif path.startswith("/api/gmail/previews/"):
                    parts = path.strip("/").split("/")
                    if len(parts) != 5 or parts[4] not in {"review", "relationship"}:
                        raise LookupError("Route not found")
                    if origin != self.gmail_origin():
                        self.send_json(403, {"error": "Review Gmail from the ClubSP workspace"})
                        return
                    if parts[4] == "relationship":
                        result = relationships.review_gmail_reply(parts[3], data)
                    else:
                        result = application.review_gmail_preview(parts[3], data)
                elif path == "/api/gmail/disconnect":
                    if origin != self.gmail_origin():
                        self.send_json(403, {"error": "Open Disconnect from the ClubSP workspace"})
                        return
                    if data != {}:
                        raise ValueError("Disconnect request must be empty")
                    gmail.disconnect()
                    result = {"connected": False, "google_permission_revoked": False}
                elif path == "/api/relationships":
                    result = relationships.save(data)
                elif path.startswith("/api/relationships/"):
                    parts = path.strip("/").split("/")
                    if len(parts) != 4 or parts[3] not in {"interactions", "drafts", "draft-reviews", "send"}:
                        raise LookupError("Route not found")
                    if parts[3] == "send":
                        if origin != self.gmail_origin():
                            self.send_json(403, {"error": "Send Gmail from the ClubSP workspace"})
                            return
                        result = send_approved_draft(gmail, relationships, parts[2], data)
                    else:
                        action = {"interactions": relationships.interact, "drafts": relationships.save_draft, "draft-reviews": relationships.review_draft}[parts[3]]
                        result = action(parts[2], data)
                elif path == "/api/properties":
                    result = application.create_property(data)
                elif path.startswith("/api/properties/"):
                    parts = path.strip("/").split("/")
                    if len(parts) != 4 or parts[3] != "research":
                        raise LookupError("Route not found")
                    result = application.lookup_parcel(parts[2], data)
                elif path.startswith("/api/research/"):
                    parts = path.strip("/").split("/")
                    if len(parts) != 4 or parts[3] != "review":
                        raise LookupError("Route not found")
                    result = application.review_research(parts[2], data)
                elif path == "/api/facts":
                    result = application.record_fact(data)
                elif path == "/api/predictions":
                    result = application.predict(data)
                elif path == "/api/deals":
                    result = application.create_deal(data)
                elif path == "/api/buyers":
                    result = application.create_buyer(data)
                elif path == "/api/commitments/buyer-mandates":
                    result = application.create_buyer_mandate(data)
                elif path == "/api/commitments/capital":
                    result = application.create_capital_profile(data)
                elif path == "/api/commitments/outcomes":
                    result = application.record_commitment_outcome(data)
                elif path == "/api/commitments/reservations":
                    result = application.reserve_buyer_commitment(data)
                elif path.startswith("/api/commitments/reservations/"):
                    parts = path.strip("/").split("/")
                    if len(parts) != 5 or parts[4] != "release":
                        raise LookupError("Route not found")
                    result = application.release_buyer_commitment(parts[3], data)
                elif path == "/api/providers/search":
                    result = application.search_property_provider(data)
                elif path == "/api/providers/preflight":
                    result = application.preflight_property_count(data)
                elif path.startswith("/api/commitments/"):
                    parts = path.strip("/").split("/")
                    if len(parts) != 5 or parts[4] not in {"status", "reconfirm"}:
                        raise LookupError("Route not found")
                    entity = {"buyer-mandates": "buyer_mandate", "capital": "capital_profile"}.get(parts[2])
                    if entity is None:
                        raise LookupError("Route not found")
                    if parts[4] == "status":
                        result = application.change_commitment_status(entity, parts[3], data)
                    else:
                        result = application.reconfirm_commitment(entity, parts[3], data)
                elif path == "/api/opportunities/policy":
                    result = application.save_opportunity_policy(data)
                elif path == "/api/sourcing/import":
                    result = application.import_candidates(data)
                elif path.startswith("/api/sourcing/rows/"):
                    parts = path.strip("/").split("/")
                    if len(parts) != 5 or parts[4] != "review":
                        raise LookupError("Route not found")
                    result = application.review_candidate(parts[3], data)
                elif path.startswith("/api/sourcing/sales/"):
                    parts = path.strip("/").split("/")
                    if len(parts) != 5 or parts[4] != "withdraw":
                        raise LookupError("Route not found")
                    result = application.withdraw_sale(parts[3], data)
                elif path == "/api/contacts":
                    result = application.create_contact(data)
                elif path == "/api/training/practice":
                    result = application.save_practice(data)
                elif path == "/api/knowledge/update":
                    result = application.start_knowledge_update(data)
                elif path.startswith("/api/knowledge/"):
                    parts = path.strip("/").split("/")
                    if len(parts) != 5:
                        raise LookupError("Route not found")
                    routes = {("runs", "cancel"): application.cancel_knowledge_update,
                              ("snapshots", "review"): application.review_knowledge_snapshot,
                              ("items", "version"): application.change_knowledge_version}
                    action = routes.get((parts[2], parts[4]))
                    if action is None:
                        raise LookupError("Route not found")
                    result = action(parts[3], data)
                elif path.startswith("/api/contacts/"):
                    parts = path.strip("/").split("/")
                    routes = {"permission": application.set_contact_permission, "messages": application.record_message,
                              "profile": application.save_seller_profile, "reply": application.suggest_reply}
                    if len(parts) != 4 or parts[3] not in routes:
                        raise LookupError("Route not found")
                    result = routes[parts[3]](parts[2], data)
                elif path.startswith("/api/replies/"):
                    parts = path.strip("/").split("/")
                    if len(parts) != 4 or parts[3] != "review":
                        raise LookupError("Route not found")
                    result = application.review_reply(parts[2], data)
                elif path.startswith("/api/tasks/"):
                    parts = path.strip("/").split("/")
                    if len(parts) != 4 or parts[3] not in {"status", "schedule"}:
                        raise LookupError("Route not found")
                    result = application.resolve_task(parts[2], data) if parts[3] == "status" else application.schedule_task(parts[2], data)
                elif path.startswith("/api/deals/"):
                    parts = path.strip("/").split("/")
                    if len(parts) != 4:
                        raise LookupError("Route not found")
                    if parts[3] == "stage":
                        result = application.advance_deal(parts[2], data)
                    elif parts[3] == "underwriting":
                        result = application.underwrite(parts[2], data)
                    elif parts[3] == "buyer-matches" and data == {}:
                        result = application.match_buyers(parts[2])
                    elif parts[3] == "readiness" and data == {}:
                        result = application.deal_readiness(parts[2])
                    elif parts[3] == "economic-review":
                        result = application.review_economics(parts[2], data)
                    elif parts[3] == "financial-plan":
                        result = application.save_financial_plan(parts[2], data)
                    elif parts[3] == "funding":
                        result = funding.save(parts[2], data)
                    elif parts[3] == "ledger":
                        result = application.record_ledger_entry(parts[2], data)
                    elif parts[3] == "reconciliation":
                        result = application.reconcile_deal(parts[2], data)
                    elif parts[3] == "tasks":
                        result = application.create_task(parts[2], data)
                    else:
                        raise LookupError("Route not found")
                elif path.startswith("/api/predictions/") and path.endswith("/outcome"):
                    parts = path.strip("/").split("/")
                    if len(parts) != 4:
                        raise LookupError("Route not found")
                    result = application.resolve(parts[2], data)
                else:
                    raise LookupError("Route not found")
                self.send_json(201, result)
            except LookupError as error:
                self.send_json(404, {"error": str(error)})
            except (ValueError, TypeError, UnicodeError) as error:
                self.send_json(400, {"error": str(error)})
            except sqlite3.Error:
                self.send_json(503, {"error": "Database temporarily unavailable"})

        def log_message(self, format, *args):
            # Never log OAuth codes/state or provider response material.
            if urlsplit(self.path).path == CALLBACK:
                super().log_message("Gmail OAuth callback handled")
            else:
                super().log_message(format, *args)

    return Handler


def create_server(database_path, port=8000, application=None, gmail=None):
    connection = gmail or GmailConnection(Path(database_path).parent / "private")
    return ThreadingHTTPServer(("127.0.0.1", port), handler_for(application or Application(database_path), connection))


def main():
    parser = argparse.ArgumentParser(description="Run the ClubSP local app")
    parser.add_argument("--db", default="data/clubsp.sqlite3", help="SQLite database file")
    parser.add_argument("--port", type=int, default=8000)
    args = parser.parse_args()
    load_local_environment(Path(__file__).resolve().parent.parent / ".env")
    server = create_server(args.db, args.port)
    print(f"ClubSP is running at http://127.0.0.1:{server.server_address[1]}", flush=True)
    try:
        server.serve_forever()
    except KeyboardInterrupt:
        pass
    finally:
        server.server_close()


if __name__ == "__main__":
    main()
