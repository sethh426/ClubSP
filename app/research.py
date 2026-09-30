from datetime import datetime, timedelta
import hashlib
import json
from uuid import UUID, uuid4

from core.memory import Fact, SourceRecord
from core.memory.models import utc_now
from .operations import business_today
from .providers import AllenCountyAdapter, PROVIDER, normalized_address, parcel_key
from .validation import number_field, property_exists, text_field


class ResearchMixin:
    research_adapter = None

    @staticmethod
    def _research_row(row):
        item = dict(row)
        for column in ("record_json", "identity_json", "fact_ids_json"):
            item[column.removesuffix("_json")] = json.loads(item.pop(column))
        return item

    def _research_snapshots(self, connection):
        return [self._research_row(row) for row in connection.execute("SELECT * FROM research_snapshots ORDER BY created_at DESC,id")]

    def lookup_parcel(self, property_id, data):
        key = parcel_key(text_field(data, "parcel_key", 30))
        now = utc_now()
        with self.database.session(write=True) as (connection, _):
            pid = property_exists(connection, property_id)
            prop = connection.execute("SELECT * FROM properties WHERE id=?", (str(pid),)).fetchone()
            if normalized_address(prop["state"]) != "IN":
                raise ValueError("This adapter covers Allen County, Indiana only")
            latest = connection.execute("SELECT * FROM research_snapshots WHERE property_id=? AND parcel_key=? ORDER BY created_at DESC,id LIMIT 1", (str(pid), key)).fetchone()
            if latest:
                age = now - datetime.fromisoformat(latest["created_at"])
                if latest["status"] in {"pending", "accepted"} and age < timedelta(hours=24):
                    return self._research_row(latest)
                if latest["status"] == "fetching" and age < timedelta(seconds=30):
                    raise ValueError("This parcel lookup is already in progress")
                if latest["status"] == "fetching":
                    connection.execute("UPDATE research_snapshots SET status='failed',error='Interrupted lookup; retry requested' WHERE id=?", (latest["id"],))
            count = connection.execute("SELECT count(*) FROM research_snapshots WHERE request_day=?", (business_today().isoformat(),)).fetchone()[0]
            if count >= PROVIDER["daily_request_limit"]:
                raise ValueError("Daily parcel lookup budget reached; try again on the next business date")
            snapshot_id = str(uuid4())
            connection.execute(
                "INSERT INTO research_snapshots(id,property_id,provider_id,parcel_key,status,request_day,source_url,record_json,identity_json,fact_ids_json,error,review_note,reviewed_at,created_at) VALUES(?,?,?,?,?,?,?,?,?,?,?,?,?,?)",
                (snapshot_id, str(pid), PROVIDER["id"], key, "fetching", business_today().isoformat(), "", "null", "{}", "[]", "", "", None, now.isoformat()),
            )
            prop = dict(prop)
        try:
            result = (self.research_adapter or AllenCountyAdapter()).fetch(key)
            identity = {}
            if result["record"]:
                record = result["record"]
                for field, official in (("address", "official_property_address"), ("city", "official_property_city"), ("state", "official_property_state")):
                    identity[field] = {"entered": prop[field], "reported": record.get(official), "matches": bool(record.get(official)) and normalized_address(prop[field]) == normalized_address(record[official])}
        except (OSError, ValueError, KeyError, TypeError, OverflowError) as error:
            with self.database.session(write=True) as (connection, _):
                connection.execute("UPDATE research_snapshots SET status='failed',error=? WHERE id=?", ("Lookup failed: " + type(error).__name__ + ". No evidence was imported.", snapshot_id))
            raise ValueError("Official record lookup failed; see the saved attempt and retry when the provider is available") from error
        with self.database.session(write=True) as (connection, _):
            connection.execute("UPDATE research_snapshots SET status=?,source_url=?,record_json=?,identity_json=? WHERE id=?", (result["status"], result["url"], json.dumps(result["record"], allow_nan=False), json.dumps(identity), snapshot_id))
            row = connection.execute("SELECT * FROM research_snapshots WHERE id=?", (snapshot_id,)).fetchone()
            return self._research_row(row)

    def review_research(self, snapshot_id, data):
        decision = text_field(data, "decision", 20)
        note = text_field(data, "note", 1000)
        if decision not in {"accept", "reject"}:
            raise ValueError("decision must be accept or reject")
        confidence = number_field(data, "confidence", 0.8)
        if not 0 <= confidence <= 1:
            raise ValueError("confidence must be between 0 and 1")
        with self.database.session(write=True) as (connection, memory):
            row = connection.execute("SELECT * FROM research_snapshots WHERE id=?", (snapshot_id,)).fetchone()
            if row is None:
                raise LookupError("Research snapshot not found")
            snapshot = self._research_row(row)
            if snapshot["status"] == "accepted" and decision == "accept":
                return snapshot
            if snapshot["status"] != "pending":
                raise ValueError("Only a pending single-record snapshot can be reviewed")
            if decision == "accept":
                if data.get("owner_confirmed_identity") is not True:
                    raise ValueError("Owner must compare and confirm the parcel identity")
                if utc_now() - datetime.fromisoformat(snapshot["created_at"]) > timedelta(days=7):
                    raise ValueError("Snapshot is too old for acceptance; run a fresh lookup")
                if any(not value["matches"] for value in snapshot["identity"].values()):
                    explanation = text_field(data, "mismatch_explanation", 1000)
                    note += " Identity discrepancy explained: " + explanation
                body = json.dumps(snapshot["record"], sort_keys=True)
                source = memory.add_source(SourceRecord(
                    source_type="public_record", provider=PROVIDER["name"], external_id=snapshot["parcel_key"],
                    url=snapshot["source_url"], retrieved_at=datetime.fromisoformat(snapshot["created_at"]),
                    raw_reference="research_snapshot:" + snapshot["id"], content_hash=hashlib.sha256(body.encode()).hexdigest(),
                ))
                subject_id = UUID(snapshot["property_id"])
                facts = memory.facts_for("property", subject_id)
                superseded = {f.supersedes_fact_id for f in facts}
                for attribute, value in snapshot["record"].items():
                    current = [f for f in facts if f.attribute == attribute and f.id not in superseded]
                    prior = max(current, key=lambda f: (f.observed_at, str(f.id))) if current else None
                    fact = memory.add_fact(Fact(subject_type="property", subject_id=subject_id,
                        attribute=attribute, value=value, value_type="number" if isinstance(value, int) else "text",
                        source_id=source.id, observed_at=source.retrieved_at, confidence=confidence,
                        supersedes_fact_id=prior.id if prior else None))
                    snapshot["fact_ids"].append(str(fact.id))
            status = "accepted" if decision == "accept" else "rejected"
            connection.execute("UPDATE research_snapshots SET status=?,review_note=?,reviewed_at=?,fact_ids_json=? WHERE id=?", (status, note, utc_now().isoformat(), json.dumps(snapshot["fact_ids"]), snapshot_id))
            return self._research_row(connection.execute("SELECT * FROM research_snapshots WHERE id=?", (snapshot_id,)).fetchone())
