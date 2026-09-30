from __future__ import annotations

import argparse
from http.server import BaseHTTPRequestHandler, ThreadingHTTPServer
import json
from pathlib import Path
import sqlite3
from urllib.parse import urlsplit

from .database import dumps
from .service import Application

STATIC = Path(__file__).with_name("static")
ASSETS = {
    "/": ("index.html", "text/html; charset=utf-8"),
    "/app.js": ("app.js", "text/javascript; charset=utf-8"),
    "/workspace.js": ("workspace.js", "text/javascript; charset=utf-8"),
    "/style.css": ("style.css", "text/css; charset=utf-8"),
}
MAX_BODY = 65536


def reject_constant(value):
    raise ValueError("Nonfinite JSON numbers are not supported")


def handler_for(application):
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

        def do_GET(self):
            if not self.valid_host():
                self.send_json(403, {"error": "Use the local application URL"})
                return
            path = urlsplit(self.path).path
            if path in ASSETS:
                name, content_type = ASSETS[path]
                self.send_content(200, (STATIC / name).read_bytes(), content_type)
            elif path == "/api/health":
                self.send_json(200, {"status": "ok", "mode": "local"})
            elif path == "/api/state":
                try:
                    self.send_json(200, application.state())
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
                if path == "/api/properties":
                    result = application.create_property(data)
                elif path == "/api/facts":
                    result = application.record_fact(data)
                elif path == "/api/predictions":
                    result = application.predict(data)
                elif path == "/api/deals":
                    result = application.create_deal(data)
                elif path == "/api/buyers":
                    result = application.create_buyer(data)
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
                    elif parts[3] == "financial-plan":
                        result = application.save_financial_plan(parts[2], data)
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
            # Log routes/statuses without request payloads or stored evidence.
            super().log_message(format, *args)

    return Handler


def create_server(database_path, port=8000):
    return ThreadingHTTPServer(("127.0.0.1", port), handler_for(Application(database_path)))


def main():
    parser = argparse.ArgumentParser(description="Run the ClubSP local app")
    parser.add_argument("--db", default="data/clubsp.sqlite3", help="SQLite database file")
    parser.add_argument("--port", type=int, default=8000)
    args = parser.parse_args()
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
