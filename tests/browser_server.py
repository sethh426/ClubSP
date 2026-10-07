"""Run the actual HTTP/UI app with one injected offline parcel adapter for CI."""
import argparse

from app.server import create_server
from app.service import Application
from tests.research_fixture import SyntheticParcelAdapter
from tests.knowledge_fixture import SyntheticKnowledgeAdapter
from tests.sentra_fixture import seed_sentras


def main():
    parser = argparse.ArgumentParser()
    parser.add_argument("--port", type=int, default=8765)
    parser.add_argument("--db", default="data/browser-test.db")
    args = parser.parse_args()
    app = Application(args.db)
    seed_sentras(app)
    app.research_adapter = SyntheticParcelAdapter()
    app.knowledge_adapter = SyntheticKnowledgeAdapter()
    app.knowledge_adapter.failures = {"email_rules"}
    app.knowledge_inline = True
    server = create_server(args.db, args.port, application=app)
    try:
        server.serve_forever()
    finally:
        server.server_close()


if __name__ == "__main__":
    main()
