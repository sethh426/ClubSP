"""Bounded, owner-reviewed CSV intake. No network requests or seller outreach."""
import csv
from datetime import date, datetime, time, timezone
from decimal import Decimal, InvalidOperation
import hashlib
import io
import json
import re
from urllib.parse import urlsplit
from uuid import UUID, NAMESPACE_URL, uuid4, uuid5

from core.memory import Fact, SourceRecord
from core.memory.models import utc_now
from .validation import property_exists, text_field


def identity(value):
    return " ".join(str(value).casefold().split())


def dated(value, key):
    try:
        result = date.fromisoformat(value)
    except (ValueError, TypeError):
        raise ValueError(f"{key} must be an ISO date (YYYY-MM-DD)") from None
    if result > utc_now().date():
        raise ValueError(f"{key} cannot be in the future")
    return result



def apply_discovery_gis_evidence(memory, property_id, batch, row_id):
    """Persist saved official GIS resolution facts idempotently; never infer economics."""
    discovery = batch.get("discovery") or {}
    candidate = discovery.get("candidate") or {}
    resolution = candidate.get("parcel_resolution") or {}
    if resolution.get("status") != "resolved":
        return []
    checked_raw = resolution.get("checked_at")
    try:
        observed_at = datetime.fromisoformat(checked_raw) if isinstance(checked_raw, str) else utc_now()
        if observed_at.tzinfo is None:
            observed_at = observed_at.replace(tzinfo=timezone.utc)
    except ValueError:
        observed_at = utc_now()
    raw_reference = f"discovery:{batch.get('id')}:{row_id}:allen-county-gis"
    source_id = uuid5(NAMESPACE_URL, raw_reference)
    content_hash = hashlib.sha256(
        json.dumps(resolution, sort_keys=True, separators=(",", ":"), default=str).encode()
    ).hexdigest()
    if source_id not in memory.sources:
        memory.add_source(SourceRecord(
            id=source_id,
            source_type="official_gis",
            provider="Allen County GIS",
            url=resolution.get("parcel_service") or resolution.get("site_address_service"),
            retrieved_at=observed_at,
            raw_reference=raw_reference,
            content_hash=content_hash,
            reliability_score=0.9,
        ))

    values = {
        "recorded_owner_name": resolution.get("owner_of_record"),
        "official_gis_address": resolution.get("official_address"),
        "gis_pin": resolution.get("pin"),
        "gis_parcel_id": resolution.get("gis_id"),
        "property_class": resolution.get("property_class"),
        "assessed_total": resolution.get("assessed_total"),
        "prior_sale_price": resolution.get("prior_sale_price"),
        "prior_sale_date": resolution.get("prior_sale_date"),
        "year_built": resolution.get("year_built"),
        "legal_acreage": resolution.get("legal_acreage"),
        "municipality": resolution.get("municipality"),
    }
    if isinstance(values["prior_sale_date"], (int, float)) and not isinstance(values["prior_sale_date"], bool):
        try:
            values["prior_sale_date"] = datetime.fromtimestamp(
                values["prior_sale_date"] / 1000, tz=timezone.utc
            ).date().isoformat()
        except (OverflowError, OSError, ValueError):
            values["prior_sale_date"] = str(values["prior_sale_date"])

    applied = []
    pid = UUID(property_id)
    for attribute, value in values.items():
        if value is None or value == "":
            continue
        value_type = "number" if isinstance(value, (int, float)) and not isinstance(value, bool) else "text"
        fact_id = uuid5(source_id, attribute)
        if fact_id not in memory.facts:
            memory.add_fact(Fact(
                id=fact_id, subject_type="property", subject_id=pid, attribute=attribute,
                value=value, value_type=value_type, source_id=source_id,
                observed_at=observed_at, confidence=0.85,
            ))
        applied.append(attribute)
    return applied


def apply_current_owner_evidence(memory, property_id, row_id, resolution):
    owner = str(resolution.get("owner_of_record") or "").strip()
    if not owner:
        raise ValueError("Allen County parcel record does not publish an owner of record for this parcel")
    checked_raw = resolution.get("checked_at")
    try:
        observed_at = datetime.fromisoformat(checked_raw) if isinstance(checked_raw, str) else utc_now()
        if observed_at.tzinfo is None:
            observed_at = observed_at.replace(tzinfo=timezone.utc)
    except ValueError:
        observed_at = utc_now()
    digest = hashlib.sha256(
        json.dumps({
            "gis_id": resolution.get("gis_id"),
            "pin": resolution.get("pin"),
            "owner_of_record": owner,
            "checked_at": observed_at.isoformat(),
        }, sort_keys=True, separators=(",", ":")).encode()
    ).hexdigest()
    raw_reference = f"official-owner-recheck:{row_id}:{digest}"
    source_id = uuid5(NAMESPACE_URL, raw_reference)
    if source_id not in memory.sources:
        memory.add_source(SourceRecord(
            id=source_id, source_type="official_gis_owner",
            provider="Allen County GIS",
            url=resolution.get("parcel_service"),
            retrieved_at=observed_at,
            raw_reference=raw_reference,
            content_hash=digest,
            reliability_score=0.9,
        ))

    pid = UUID(property_id)
    current = [
        fact for fact in memory.facts.values()
        if fact.subject_type == "property"
        and fact.subject_id == pid
        and fact.attribute == "recorded_owner_name"
        and fact.status == "active"
    ]
    same = next((fact for fact in current if str(fact.value).strip() == owner), None)
    if same is not None:
        return {
            "owner": owner, "fact_id": str(same.id), "source_id": str(same.source_id),
            "changed": False,
        }
    for fact in current:
        fact.status = "superseded"
    fact_id = uuid5(source_id, "recorded_owner_name")
    if fact_id not in memory.facts:
        memory.add_fact(Fact(
            id=fact_id, subject_type="property", subject_id=pid,
            attribute="recorded_owner_name", value=owner, value_type="text",
            source_id=source_id, observed_at=observed_at, confidence=0.9,
        ))
    return {
        "owner": owner, "fact_id": str(fact_id), "source_id": str(source_id),
        "changed": bool(current),
    }

def sale_snapshot(connection, property_id):
    rows = [json.loads(r["body"]) for r in connection.execute(
        "SELECT body FROM sale_evidence WHERE property_id=? AND status='accepted' ORDER BY id", (str(property_id),))]
    digest = hashlib.sha256(json.dumps(rows, sort_keys=True, separators=(",", ":")).encode()).hexdigest()
    keys = {}
    for row in rows:
        keys.setdefault((row["sale"]["parcel_id"], row["sale"]["sale_date"]), set()).add(row["sale"]["sale_price"])
    return {"items": rows, "digest": digest, "conflicts": any(len(prices) > 1 for prices in keys.values())}


def preliminary_candidate_buyers(connection, market, property_type, asking_price=None):
    """Compare only buyer criteria that are actually known for a reviewed candidate."""
    from .opportunities import canonical

    matches = []
    for row in connection.execute("SELECT * FROM buyers WHERE status='active' ORDER BY created_at DESC,id"):
        locations = json.loads(row["locations_json"])
        types = json.loads(row["property_types_json"])
        reasons = []
        if market not in locations:
            reasons.append("market does not match")
        if types and (property_type is None or canonical(property_type) not in types):
            reasons.append("property type is unknown or outside buyer criteria")
        if asking_price is not None:
            try:
                price = float(asking_price)
            except (TypeError, ValueError):
                reasons.append("asking price is not a usable number")
            else:
                if price > row["max_total_price"]:
                    reasons.append("asking price exceeds buyer limit")
        possible = not reasons
        gaps = [
            "Strategy, repair scope, final acquisition price, buyer interest and current funding are not yet confirmed."
        ]
        matches.append({
            "buyer_id": row["id"], "name": row["name"], "company": row["company"],
            "possible_fit_on_known_fields": possible,
            "reasons": reasons + gaps,
            "funding_status_on_record": row["funding_status"],
            "commitment_confirmed": False,
        })
    matches.sort(key=lambda item: (not item["possible_fit_on_known_fields"], item["name"].lower()))
    return matches


class SourcingMixin:
    def _discovery_state(self, connection):
        """Rank reviewed candidate properties for research; never invent economics."""
        policy_row = connection.execute("SELECT body FROM opportunity_policies ORDER BY rowid DESC LIMIT 1").fetchone()
        policy = json.loads(policy_row["body"]) if policy_row else None
        from .opportunities import canonical, property_evidence
        from datetime import datetime, timezone
        now = utc_now()
        existing = {str(row["property_id"]): row["stage"] for row in connection.execute(
            "SELECT property_id,stage FROM deals WHERE stage NOT IN ('completed','lost')")}
        candidates = []
        rows = connection.execute(
            "SELECT r.body,b.body AS batch_body FROM sourcing_rows r JOIN sourcing_batches b ON b.id=r.batch_id "
            "WHERE r.status='accepted' ORDER BY r.rowid DESC")
        for record in rows:
            row, batch = json.loads(record["body"]), json.loads(record["batch_body"])
            if batch.get("kind") != "candidates" or not row.get("review", {}).get("property_id"):
                continue
            property_id = row["review"]["property_id"]
            prop = connection.execute("SELECT * FROM properties WHERE id=?", (property_id,)).fetchone()
            if prop is None:
                continue
            evidence = property_evidence(connection, property_id)
            reasons, outside, score = [], [], 0
            market = " ".join(f"{prop['city']}, {prop['state']}".lower().split())
            ptype = evidence["values"].get("property_type")
            if not policy:
                reasons.append("Save a buy-box policy before ranking this candidate")
            else:
                if market in policy["markets"]:
                    score += 40
                else:
                    outside.append("Market is outside the saved buy box")
                if ptype and canonical(ptype) in policy["property_types"]:
                    score += 25
                elif ptype:
                    outside.append("Property type is outside the saved buy box")
                else:
                    reasons.append("Record an unambiguous property type")
            commitment_matches = self.reverse_match_candidate(
                connection,
                market=market,
                property_type=ptype,
                asking_price=evidence["values"].get("asking_price"),
                beds=evidence["values"].get("beds"),
                baths=evidence["values"].get("baths"),
                sqft=evidence["values"].get("sqft"),
                year_built=evidence["values"].get("year_built"),
            )
            if commitment_matches:
                score += min(25, round(commitment_matches[0]["score"] / 4))
                reasons.append(
                    f"{len(commitment_matches)} current standing buyer mandate(s) fit this research candidate"
                )
            else:
                reasons.append("No current standing buyer mandate matches this research candidate")
            if evidence["values"].get("recorded_owner_name"):
                score += 15
            else:
                reasons.append("Confirm owner-of-record evidence")
            if evidence["values"].get("parcel_id"):
                score += 10
            else:
                reasons.append("Confirm parcel identity")
            try:
                source_date = datetime.fromisoformat(batch["source_date"]).replace(tzinfo=timezone.utc)
                age_days = max(0, (now - source_date).days)
            except (ValueError, TypeError, KeyError):
                age_days = None
            if age_days is not None and age_days <= (policy["evidence_max_age_days"] if policy else 30):
                score += 10
            else:
                reasons.append("Refresh the candidate source record")
            if property_id in existing:
                reasons.append("Already in an active deal pipeline")
            asking_price = evidence["values"].get("asking_price")
            if not asking_price:
                reasons.append("Capture seller price or terms before economics can be tested")
            buyer_matches = preliminary_candidate_buyers(connection, market, ptype, asking_price)
            possible_buyers = [buyer for buyer in buyer_matches if buyer["possible_fit_on_known_fields"]]
            if not possible_buyers:
                reasons.append("No active buyer currently fits the candidate's known market, type and recorded asking price")
            decision = "outside_buy_box" if outside else "research_candidate"
            if property_id in existing:
                decision = "already_in_pipeline"
            candidates.append({"property_id": property_id, "address": prop["address"], "market": market,
                               "property_type": ptype, "score": score, "decision": decision,
                               "commitment_matches": commitment_matches[:5],
                               "commitment_match_count": len(commitment_matches),
                               "best_commitment_score": commitment_matches[0]["score"] if commitment_matches else 0,
                               "reasons": outside + reasons,
                               "buyer_matches": buyer_matches,
                               "possible_buyer_count": len(possible_buyers),
                               "source": {"provider": batch["provider"], "url": batch["source_url"],
                                          "as_of": batch["source_date"], "batch_id": batch["id"]},
                               "economics_available": False,
                               "scope": "Research candidate only; buyer fit uses known criteria only and does not confirm interest, funding or economics"})
        order = {"research_candidate": 0, "already_in_pipeline": 1, "outside_buy_box": 2}
        candidates.sort(key=lambda item: (
            order[item["decision"]],
            -item["commitment_match_count"],
            -item["best_commitment_score"],
            -item["possible_buyer_count"],
            -item["score"],
            item["address"],
            item["property_id"],
        ))
        return {"items": candidates, "policy_id": policy["id"] if policy else None,
                "execution_authorized": False,
                "scope": "Reviewed candidate imports only; no autonomous discovery or external actions"}

    def import_candidates(self, data):
        kind = text_field(data, "kind", 20)
        if kind not in {"candidates", "county_sales"}:
            raise ValueError("kind must be candidates or county_sales")
        provider = text_field(data, "provider", 120)
        source_url = text_field(data, "source_url", 500)
        url = urlsplit(source_url)
        if url.scheme != "https" or not url.hostname or url.username or url.password:
            raise ValueError("source_url must be an HTTPS source reference without credentials")
        source_date = dated(text_field(data, "source_date", 10), "source_date").isoformat()
        rights = text_field(data, "rights_basis", 1000)
        city, state = text_field(data, "city", 100), text_field(data, "state", 2).upper()
        if not re.fullmatch(r"[A-Z]{2}", state):
            raise ValueError("state must be a two-letter code")
        raw = text_field(data, "csv", 45000).lstrip("\ufeff")
        if len(raw.encode()) > 45000:
            raise ValueError("CSV must fit within 45,000 bytes; split larger exports")
        required = {"address", "zip", "parcel_id", "property_type"} if kind == "candidates" else {
            "Parcel Number", "Address", "Sale Date", "Sale Price", "Living Area"}
        reader = csv.DictReader(io.StringIO(raw), strict=True)
        try:
            headers = reader.fieldnames
        except csv.Error as exc:
            raise ValueError("Malformed CSV header: " + str(exc)) from exc
        if not headers or len(headers) != len(set(headers)) or not required.issubset(headers):
            raise ValueError("CSV requires unique headers including: " + ", ".join(sorted(required)))
        try:
            parsed = []
            for row in reader:
                if len(parsed) == 50:
                    raise ValueError("CSV is limited to 50 rows; narrow or split the source export")
                parsed.append(row)
        except csv.Error as exc:
            raise ValueError("Malformed CSV: " + str(exc)) from exc
        if not parsed:
            raise ValueError("CSV must contain at least one row")
        metadata = {"kind": kind, "provider": provider, "source_url": source_url, "source_date": source_date,
                    "rights_basis": rights, "city": city, "state": state}
        raw_hash = hashlib.sha256(raw.encode()).hexdigest()
        batch_key = hashlib.sha256(json.dumps([metadata, raw_hash], sort_keys=True).encode()).hexdigest()
        now = utc_now().isoformat()
        with self.database.session(write=True) as (connection, _):
            prior = connection.execute("SELECT id FROM sourcing_batches WHERE batch_key=?", (batch_key,)).fetchone()
            if prior:
                return {"id": prior["id"], "duplicate": True}
            batch_id = str(uuid4())
            batch = {"id": batch_id, **metadata, "raw_hash": raw_hash, "retrieved_at": now, "row_count": len(parsed)}
            connection.execute("INSERT INTO sourcing_batches(id,batch_key,body) VALUES(?,?,?)", (batch_id, batch_key, json.dumps(batch)))
            seen = set()
            for number, raw_row in enumerate(parsed, 2):
                errors = []
                row = {"city": city, "state": state}
                try:
                    if None in raw_row or any(v is None for v in raw_row.values()):
                        raise ValueError("Wrong column count")
                    if any(len(v) > 1000 for v in raw_row.values()):
                        raise ValueError("A cell exceeds 1000 characters")
                    row["address"] = text_field(raw_row, "address" if kind == "candidates" else "Address", 200)
                    row["parcel_id"] = text_field(raw_row, "parcel_id" if kind == "candidates" else "Parcel Number", 100)
                    if kind == "candidates":
                        row["zip"] = text_field(raw_row, "zip", 10)
                        row["property_type"] = text_field(raw_row, "property_type", 60)
                        for header, key, integer_only in (
                            ("asking_price", "asking_price", False),
                            ("beds", "beds", False),
                            ("baths", "baths", False),
                            ("sqft", "sqft", False),
                            ("year_built", "year_built", True),
                        ):
                            raw_value = raw_row.get(header, "")
                            if raw_value is None or not raw_value.strip():
                                continue
                            try:
                                numeric = Decimal(raw_value.strip().replace("$", "").replace(",", ""))
                                if not numeric.is_finite() or numeric < 0 or numeric > Decimal("1000000000"):
                                    raise InvalidOperation
                                if header == "asking_price" and numeric != numeric.quantize(Decimal("0.01")):
                                    raise InvalidOperation
                                if integer_only and numeric != numeric.to_integral_value():
                                    raise InvalidOperation
                            except InvalidOperation:
                                raise ValueError(f"{header} must be a finite nonnegative number") from None
                            row[key] = int(numeric) if integer_only else float(numeric)
                    else:
                        value = text_field(raw_row, "Sale Date", 40)
                        try:
                            sold = datetime.strptime(value, "%m/%d/%Y").date() if "/" in value else date.fromisoformat(value)
                        except ValueError:
                            raise ValueError("Sale Date must be YYYY-MM-DD or MM/DD/YYYY") from None
                        row["sale_date"] = dated(sold.isoformat(), "Sale Date").isoformat()
                        if row["sale_date"] > source_date:
                            raise ValueError("Sale Date cannot follow source_date")
                        for header, key in (("Sale Price", "sale_price"), ("Living Area", "living_area")):
                            try:
                                number_value = Decimal(raw_row[header].strip().replace("$", "").replace(",", ""))
                                if not number_value.is_finite() or number_value <= 0 or number_value > Decimal("1000000000"):
                                    raise InvalidOperation
                                if key == "sale_price" and number_value != number_value.quantize(Decimal("0.01")):
                                    raise InvalidOperation
                            except InvalidOperation:
                                raise ValueError(f"{header} must be a positive finite number (price in cents)") from None
                            row[key] = float(number_value)
                        row["property_class"] = raw_row.get("Property Class", raw_row.get("Class", "")).strip()
                        # Preserve useful assessor fields in normalized form when present,
                        # while leaving them advisory rather than feeding them into valuation.
                        for header, key in (("Acreage", "acreage"), ("Year Built", "year_built"),
                                            ("Bath", "bath"), ("Price/SqFt", "price_per_sqft"),
                                            ("Land Value", "land_value"), ("Improvement Value", "improvement_value"),
                                            ("Total Value", "total_value")):
                            if header not in raw_row or not raw_row[header].strip():
                                continue
                            try:
                                optional = Decimal(raw_row[header].strip().replace("$", "").replace(",", ""))
                                if not optional.is_finite() or optional < 0 or optional > Decimal("1000000000"):
                                    raise InvalidOperation
                            except InvalidOperation:
                                raise ValueError(f"{header} must be a finite nonnegative number") from None
                            row[key] = float(optional)
                        for header, key in (("Neighborhood Code", "neighborhood_code"), ("Property Code", "property_code")):
                            if header in raw_row and raw_row[header].strip():
                                row[key] = raw_row[header].strip()
                    key = (identity(row["parcel_id"]), row.get("sale_date", ""))
                    if key in seen:
                        raise ValueError("Repeated parcel/sale identity within this batch; resolve at source")
                    seen.add(key)
                except ValueError as exc:
                    errors.append(str(exc))
                row_id = str(uuid4())
                body = {"id": row_id, "batch_id": batch_id, "line": number, "value": row, "raw": raw_row, "errors": errors}
                connection.execute("INSERT INTO sourcing_rows(id,batch_id,body,status) VALUES(?,?,?,?)",
                                   (row_id, batch_id, json.dumps(body), "invalid" if errors else "pending"))
        return {"id": batch_id, "duplicate": False}

    def review_candidate(self, row_id, data):
        from .opportunities import canonical, property_evidence
        action = text_field(data, "action", 20)
        if action not in {"accept", "exclude"}:
            raise ValueError("action must be accept or exclude")
        review = {k: text_field(data, k, 1000) for k in ("reviewer", "note", "evidence_reference")}
        with self.database.session(write=True) as (connection, memory):
            record = connection.execute("SELECT * FROM sourcing_rows WHERE id=?", (row_id,)).fetchone()
            if not record:
                raise LookupError("Import row not found")
            if record["status"] != "pending":
                raise ValueError("Only pending valid rows can be reviewed")
            body = json.loads(record["body"])
            batch = json.loads(connection.execute("SELECT body FROM sourcing_batches WHERE id=?", (record["batch_id"],)).fetchone()["body"])
            value = body["value"]
            property_id = None
            gis_evidence_attributes = []
            if action == "accept":
                self.validate_discovery_acceptance(connection, batch, value)
                if data.get("identity_confirmed") is not True:
                    raise ValueError("Confirm parcel, address and market identity before acceptance")
                if batch["kind"] == "candidates":
                    # Never merge on a similar address or assume parcel ownership.
                    collisions = [p for p in connection.execute("SELECT * FROM properties")
                                  if identity(p["address"]) == identity(value["address"]) and identity(p["city"]) == identity(value["city"]) and identity(p["state"]) == identity(value["state"])]
                    collision_ids = {p["id"] for p in collisions} | {str(f.subject_id) for f in memory.facts.values()
                                     if f.subject_type == "property" and canonical(f.attribute) == "parcel_id" and identity(f.value) == identity(value["parcel_id"])}
                    if collision_ids:
                        raise ValueError("Existing address or parcel found; review the existing property instead of importing a duplicate")
                    property_id = str(uuid4())
                    connection.execute("INSERT INTO properties(id,address,city,state,zip,created_at) VALUES(?,?,?,?,?,?)",
                                       (property_id, value["address"], value["city"], value["state"], value["zip"], utc_now().isoformat()))
                    source = memory.add_source(SourceRecord(source_type="reviewed_official_notice" if batch.get("discovery") else "reviewed_csv", provider=batch["provider"], url=batch["source_url"],
                        published_at=datetime.combine(date.fromisoformat(batch["source_date"]), time(), timezone.utc),
                        raw_reference=f"import:{batch['id']}:row:{row_id}", content_hash=batch["raw_hash"]))
                    for attribute in ("parcel_id", "property_type", "asking_price", "beds", "baths", "sqft", "year_built"):
                        if attribute not in value:
                            continue
                        is_number = attribute not in {"parcel_id", "property_type"}
                        memory.add_fact(Fact(subject_type="property", subject_id=UUID(property_id), attribute=attribute,
                            value=value[attribute], value_type="number" if is_number else "text",
                            source_id=source.id, observed_at=source.published_at, confidence=0.5))
                    if batch.get("discovery"):
                        gis_evidence_attributes = apply_discovery_gis_evidence(
                            memory, property_id, batch, row_id
                        )
                else:
                    property_id = str(property_exists(connection, data.get("property_id")))
                    prop = connection.execute("SELECT * FROM properties WHERE id=?", (property_id,)).fetchone()
                    parcels = [f["value"] for f in property_evidence(connection, property_id)["facts"] if canonical(f["attribute"]) == "parcel_id"]
                    if any(identity(parcel) == identity(value["parcel_id"]) for parcel in parcels) or (identity(prop["address"]) == identity(value["address"]) and identity(prop["city"]) == identity(value["city"]) and identity(prop["state"]) == identity(value["state"])):
                        raise ValueError("The subject property cannot be its own comparable")
                    if data.get("sale_verified") is not True:
                        raise ValueError("Verify sale validity, arm's-length status and comparability before accepting")
                    sale = {"id": str(uuid4()), "row_id": row_id, "property_id": property_id, "sale": value,
                            "source": batch, "review": review, "created_at": utc_now().isoformat()}
                    existing = sale_snapshot(connection, property_id)["items"]
                    if any(identity(s["sale"]["parcel_id"]) == identity(value["parcel_id"]) and s["sale"]["sale_date"] == value["sale_date"] for s in existing):
                        raise ValueError("This parcel/sale is already accepted; withdraw it before reviewing a correction")
                    connection.execute("INSERT INTO sale_evidence(id,property_id,body,status) VALUES(?,?,?,'accepted')",
                                       (sale["id"], property_id, json.dumps(sale)))
            body["review"] = {
                **review, "action": action, "property_id": property_id,
                "gis_evidence_attributes": gis_evidence_attributes,
                "created_at": utc_now().isoformat()
            }
            connection.execute("UPDATE sourcing_rows SET body=?,status=? WHERE id=?", (json.dumps(body), "accepted" if action == "accept" else "excluded", row_id))
        return {"id": row_id, "status": "accepted" if action == "accept" else "excluded", "property_id": property_id}



    def refresh_discovery_owner_evidence(self, row_id, data):
        if data != {}:
            raise ValueError("Owner evidence refresh takes no editable fields")
        with self.database.session() as (connection, _):
            record = connection.execute(
                "SELECT * FROM sourcing_rows WHERE id=?", (row_id,)
            ).fetchone()
            if not record:
                raise LookupError("Import row not found")
            if record["status"] != "accepted":
                raise ValueError("Only accepted discovery rows can refresh owner evidence")
            body = json.loads(record["body"])
            batch = json.loads(connection.execute(
                "SELECT body FROM sourcing_batches WHERE id=?", (record["batch_id"],)
            ).fetchone()["body"])
            discovery = batch.get("discovery") or {}
            candidate = discovery.get("candidate") or {}
            property_id = body.get("review", {}).get("property_id")
            expected_parcel = body.get("value", {}).get("parcel_id")
            if not property_id or not expected_parcel or not discovery:
                raise ValueError("This row is not an accepted discovery property")
            lookup_candidate = {
                "address": candidate.get("address") or body.get("value", {}).get("address"),
                "city": candidate.get("city") or body.get("value", {}).get("city"),
                "state": candidate.get("state") or body.get("value", {}).get("state"),
                "zip": candidate.get("zip") or body.get("value", {}).get("zip"),
            }

        try:
            resolution = self.parcel_resolver(lookup_candidate)
        except (OSError, ValueError, UnicodeError):
            raise ValueError("Allen County GIS owner lookup was unavailable; no owner evidence was changed") from None
        if resolution.get("status") != "resolved":
            raise ValueError("Allen County GIS did not resolve the accepted property exactly; no owner evidence was changed")
        if identity(resolution.get("gis_id")) != identity(expected_parcel):
            raise ValueError("Allen County GIS parcel no longer matches the accepted parcel; no owner evidence was changed")
        resolution["checked_at"] = utc_now().isoformat()

        with self.database.session(write=True) as (connection, memory):
            record = connection.execute(
                "SELECT * FROM sourcing_rows WHERE id=?", (row_id,)
            ).fetchone()
            if not record or record["status"] != "accepted":
                raise ValueError("Accepted sourcing row changed during owner lookup; refresh before retrying")
            body = json.loads(record["body"])
            if body.get("review", {}).get("property_id") != property_id:
                raise ValueError("Accepted property changed during owner lookup; refresh before retrying")
            result = apply_current_owner_evidence(memory, property_id, row_id, resolution)
            body["review"]["owner_evidence_refreshed_at"] = resolution["checked_at"]
            body["review"]["owner_evidence_fact_id"] = result["fact_id"]
            connection.execute(
                "UPDATE sourcing_rows SET body=? WHERE id=?", (json.dumps(body), row_id)
            )
            return {
                "id": row_id, "property_id": property_id,
                "recorded_owner_name": result["owner"],
                "owner_fact_id": result["fact_id"],
                "changed": result["changed"],
                "execution_authorized": False,
            }

    def refresh_discovery_gis_evidence(self, row_id, data):
        if data != {}:
            raise ValueError("GIS evidence refresh takes no editable fields")
        with self.database.session(write=True) as (connection, memory):
            record = connection.execute(
                "SELECT * FROM sourcing_rows WHERE id=?", (row_id,)
            ).fetchone()
            if not record:
                raise LookupError("Import row not found")
            if record["status"] != "accepted":
                raise ValueError("Only accepted discovery rows can refresh saved GIS evidence")
            body = json.loads(record["body"])
            batch = json.loads(connection.execute(
                "SELECT body FROM sourcing_batches WHERE id=?", (record["batch_id"],)
            ).fetchone()["body"])
            if not batch.get("discovery") or not body.get("review", {}).get("property_id"):
                raise ValueError("This row is not an accepted discovery property")
            property_id = body["review"]["property_id"]
            attributes = apply_discovery_gis_evidence(memory, property_id, batch, row_id)
            if not attributes:
                raise ValueError("No resolved saved GIS evidence is available for this row")
            body["review"]["gis_evidence_attributes"] = attributes
            body["review"]["gis_evidence_refreshed_at"] = utc_now().isoformat()
            connection.execute(
                "UPDATE sourcing_rows SET body=? WHERE id=?", (json.dumps(body), row_id)
            )
            return {
                "id": row_id, "property_id": property_id,
                "gis_evidence_attributes": attributes,
                "execution_authorized": False,
            }

    def withdraw_sale(self, sale_id, data):
        review = {k: text_field(data, k, 1000) for k in ("reviewer", "note", "evidence_reference")}
        with self.database.session(write=True) as (connection, _):
            row = connection.execute("SELECT * FROM sale_evidence WHERE id=?", (sale_id,)).fetchone()
            if not row:
                raise LookupError("Sale evidence not found")
            if row["status"] != "accepted":
                raise ValueError("Sale evidence already withdrawn")
            body = json.loads(row["body"])
            body["withdrawal"] = {**review, "created_at": utc_now().isoformat()}
            connection.execute("UPDATE sale_evidence SET status='withdrawn',body=? WHERE id=?", (json.dumps(body), sale_id))
        return {"id": sale_id, "status": "withdrawn"}

    def _sourcing_state(self, connection):
        from .comparables import comparable_screen
        from .opportunities import property_evidence
        policy_row = connection.execute("SELECT body FROM opportunity_policies ORDER BY rowid DESC LIMIT 1").fetchone()
        max_age = json.loads(policy_row["body"])["evidence_max_age_days"] if policy_row else 30
        screens = [comparable_screen(dict(prop), property_evidence(connection, prop["id"]),
                                    sale_snapshot(connection, prop["id"]), evidence_max_age_days=max_age)
                   for prop in connection.execute("SELECT * FROM properties WHERE id IN (SELECT property_id FROM sale_evidence WHERE status='accepted') ORDER BY id")]
        return {"comparable_screens": screens,
                "batches": [json.loads(r["body"]) for r in connection.execute("SELECT body FROM sourcing_batches ORDER BY rowid DESC")],
                "rows": [{**json.loads(r["body"]), "status": r["status"]} for r in connection.execute("SELECT body,status FROM sourcing_rows ORDER BY rowid DESC")],
                "sales": [{**json.loads(r["body"]), "status": r["status"]} for r in connection.execute("SELECT body,status FROM sale_evidence ORDER BY rowid DESC")]}
