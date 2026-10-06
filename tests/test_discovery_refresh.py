from datetime import datetime, timezone

import pytest

from app.discovery_refresh import refresh_source
from app.service import Application

SHERIFF_TEXT = """[[SOURCE_DOCUMENT:https://www.allencountysheriff.org/wp-content/uploads/2026/09/OCTOBER-2026-1.pdf]]
1 DATE OF SALE CAUSE NUMBER ADDRESS CANCELLATION DATE JUDGEMENT BID ATTORNEY PHONE SOLD TO SOLD FOR SATISFIED SHERIFF FEES
2 10/21/2026 02D03-2505-MF-000199 3817 MARIGOLD DR FORT WAYNE, IN 46815 $ 72,048.49 ELYSSA MEADE 850-422-2520 $ 313.00
"""

NO_SALES_TEXT = """[[SOURCE_DOCUMENT:https://www.allencountysheriff.org/wp-content/uploads/2026/09/NOVEMBER-2026.pdf]]
NO SALES FOR THE MONTH OF NOVEMBER
"""


def test_scheduled_sheriff_refresh_returns_bounded_summary_and_creates_no_deal(tmp_path):
    app = Application(tmp_path / "app.db")
    app.sheriff_discovery_fetch = lambda url, now: SHERIFF_TEXT
    app.parcel_resolver = lambda candidate: {
        "status": "resolved",
        "pin": "021110327015000075",
        "gis_id": "02-11-10-327-015.000-075",
        "official_address": "10324 GREENOAK BLVD",
        "zip": "46814",
        "municipality": "FW",
        "property_class": "1 Family Dwell - Platted Lot",
        "assessed_total": 618600,
        "prior_sale_price": 499900,
        "prior_sale_date": 1668578333000,
        "year_built": 1997,
        "legal_acreage": 0.0,
        "site_address_service": "synthetic",
        "parcel_service": "synthetic",
    }
    result = refresh_source(app)
    assert result["source_id"] == "sheriff_sales"
    assert result["status"] == "scheduled_sales"
    assert result["candidate_count"] == 1
    assert result["parcel_resolution_count"] == 1
    assert result["execution_authorized"] is False
    assert result["creates_deals"] is False
    assert "candidates" not in result
    state = app.state()
    assert state["properties"] == []
    assert state["deals"] == []


def test_sheriff_refresh_reuses_same_day_cache(tmp_path):
    app = Application(tmp_path / "app.db")
    calls = {"count": 0}
    def fetch(url, now):
        calls["count"] += 1
        return NO_SALES_TEXT
    app.sheriff_discovery_fetch = fetch
    first = refresh_source(app)
    second = refresh_source(app)
    assert first["status"] == "no_inventory"
    assert second["cached"] is True
    assert calls["count"] == 1


@pytest.mark.parametrize("source_id", ["accdc", "north_campus", "anything_else"])
def test_unattended_refresh_is_allowlisted(source_id, tmp_path):
    app = Application(tmp_path / "app.db")
    with pytest.raises(ValueError, match="not approved"):
        refresh_source(app, source_id)


def test_source_failure_is_not_reported_as_success(tmp_path):
    app = Application(tmp_path / "app.db")
    app.sheriff_discovery_fetch = lambda url, now: (_ for _ in ()).throw(ValueError("synthetic failure"))
    with pytest.raises(RuntimeError):
        refresh_source(app)
