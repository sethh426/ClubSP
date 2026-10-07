"""Run the actual HTTP/UI app with one injected offline parcel adapter for CI."""
import argparse
import os
import httpx

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
    # Explicit offline-only provider fixture: browser tests never call paid APIs.
    os.environ["RENTCAST_API_KEY"] = "synthetic-browser-key"
    def brief_provider(request):
        subject = {"id": "browser-rental", "addressLine1": "123 Example Rd", "city": "Fort Wayne",
                   "state": "IN", "zipCode": "46802", "propertyType": "Single Family",
                   "status": "Active", "price": 100000, "bedrooms": 3, "bathrooms": 2}
        if request.url.path == "/v1/listings/sale":
            return httpx.Response(200, json=[subject])
        if request.url.path == "/v1/avm/rent/long-term":
            return httpx.Response(200, json={"subjectProperty": subject, "rent": 1200,
                                            "rentRangeLow": 1000, "rentRangeHigh": 1400})
        return httpx.Response(503)
    app._sentra_transport = httpx.MockTransport(brief_provider)
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
