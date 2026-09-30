from app.providers import COUNTY_LAYER


class SyntheticParcelAdapter:
    """An explicit test double. Contains no real owners or opportunities."""
    def __init__(self):
        self.calls = 0

    def fetch(self, key):
        self.calls += 1
        return {"url": COUNTY_LAYER + "/query?test_fixture=" + key, "status": "pending", "record": {
            "parcel_id": key, "recorded_owner_name": "SYNTHETIC TEST OWNER",
            "official_property_address": "123 Browser Example St", "official_property_city": "Fort Wayne",
            "official_property_state": "IN", "official_property_zip": "46802", "record_pay_year": 2026,
        }}
