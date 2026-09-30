"""Run the actual HTTP/UI app with one injected offline parcel adapter for CI."""
import argparse

from app.server import create_server
from app.service import Application
from tests.research_fixture import SyntheticParcelAdapter


def main():
    parser = argparse.ArgumentParser()
    parser.add_argument("--port", type=int, default=8765)
    parser.add_argument("--db", default="data/browser-test.db")
    args = parser.parse_args()
    app = Application(args.db)
    app.research_adapter = SyntheticParcelAdapter()
    server = create_server(args.db, args.port, application=app)
    try:
        server.serve_forever()
    finally:
        server.server_close()


if __name__ == "__main__":
    main()
