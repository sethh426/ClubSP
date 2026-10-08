"""Saved, bounded rental acquisition screens built from governed collectors."""
from datetime import datetime, timedelta, timezone
from hashlib import sha256
import json
import math
from uuid import uuid4

from .schema import assert_component_compatible, ensure_component

DEFAULTS = {"max_price": 150000, "min_beds": 2, "repair_reserve": 10000,
            "closing_pct": 3, "vacancy_pct": 8, "expense_pct": 30, "min_yield_pct": 5}


def criteria(data):
    if not isinstance(data, dict) or set(data) - set(DEFAULTS):
        raise ValueError("Unsupported screening criteria")
    result = {**DEFAULTS, **data}
    limits = {"max_price": (1000, 10000000), "min_beds": (0, 10), "repair_reserve": (0, 1000000),
              "closing_pct": (0, 20), "vacancy_pct": (0, 50), "expense_pct": (0, 90), "min_yield_pct": (0, 50)}
    for key, (low, high) in limits.items():
        value = result[key]
        if type(value) not in (int, float) or not math.isfinite(value) or not low <= value <= high:
            raise ValueError(f"Invalid {key}")
    if result["min_beds"] != int(result["min_beds"]):
        raise ValueError("Minimum bedrooms must be a whole number")
    return result


def economics(price, rent, assumptions):
    """Unlevered operating yield on assumed total cash basis; never a profit forecast."""
    basis = price * (1 + assumptions["closing_pct"] / 100) + assumptions["repair_reserve"]
    collected = rent * 12 * (1 - assumptions["vacancy_pct"] / 100)
    operating = collected * (1 - assumptions["expense_pct"] / 100)
    return {"cash_basis": round(basis, 2), "annual_operating_income": round(operating, 2),
            "yield_pct": round(operating / basis * 100, 2)}


def decision(card, assumptions):
    """Explain an existing screen without treating it as demand or an offer."""
    model = card.get("economics")
    price = card["listing"]["asking_price"]
    target = assumptions["min_yield_pct"]
    result = {"price_ceiling": None, "required_price_reduction": None,
              "monthly_operating_income": None,
              "basis": "Assumption-based asking-price ceiling, rounded down to whole dollars and capped by your budget. Not an offer, market valuation or verified buyer match."}
    if model is None:
        return {**result, "status": "needs_evidence", "label": "Rent evidence needed",
                "next_step": "Obtain rent evidence before deciding whether this property fits the target."}
    result["monthly_operating_income"] = round(model["annual_operating_income"] / 12, 2)
    if target == 0:
        return {**result, "status": "needs_target", "label": "Set a positive yield target",
                "next_step": "Choose a positive operating yield target to calculate a price ceiling."}
    ceiling = (model["annual_operating_income"] / (target / 100) - assumptions["repair_reserve"]) / (1 + assumptions["closing_pct"] / 100)
    result["price_ceiling"] = max(0, math.floor(min(ceiling, assumptions["max_price"])))
    result["required_price_reduction"] = max(0, math.ceil(price - result["price_ceiling"]))
    if ceiling <= 0:
        return {**result, "status": "does_not_fit", "label": "Does not fit these assumptions",
                "next_step": "The repair reserve alone consumes the supported cash basis. Recheck costs and rent or skip this property."}
    if result["required_price_reduction"]:
        return {**result, "status": "needs_lower_price", "label": "Needs a lower price",
                "next_step": "Have the representative verify rent and repair costs, then assess whether the price gap is realistic."}
    return {**result, "status": "review_candidate", "label": "Candidate for representative review",
            "next_step": "Include in a representative review draft; confirm the buyer's criteria, availability and actual costs before any offer."}


def enrich_card(card, assumptions):
    """Keep qualification consistent with the price decision, not display rounding."""
    card["decision"] = decision(card, assumptions)
    card["screen"] = {"needs_evidence": "needs_rent_evidence",
                      "needs_target": "needs_yield_target",
                      "review_candidate": "meets_assumed_yield"}.get(
                          card["decision"]["status"], "below_assumed_yield")
    return card


class AcquisitionBriefMixin:
    def _initialize_acquisition_briefs(self):
        with self.database.session(write=True) as (connection, _):
            assert_component_compatible(connection, "acquisition_briefs")
            connection.execute("""CREATE TABLE IF NOT EXISTS acquisition_briefs (
                id TEXT PRIMARY KEY, criteria_hash TEXT NOT NULL, criteria_json TEXT NOT NULL,
                status TEXT NOT NULL, result_json TEXT NOT NULL DEFAULT '{}',
                started_at TEXT NOT NULL, completed_at TEXT NOT NULL DEFAULT '')""")
            connection.execute("CREATE INDEX IF NOT EXISTS idx_brief_criteria ON acquisition_briefs(criteria_hash,started_at)")
            ensure_component(connection, "acquisition_briefs")

    @staticmethod
    def _brief_record(row):
        assumptions = json.loads(row["criteria_json"])
        result = json.loads(row["result_json"])
        # Enrich saved briefs too, without another provider request or a schema migration.
        for card in result.get("cards", []):
            enrich_card(card, assumptions)
        return {"id": row["id"], "status": row["status"], "criteria": json.loads(row["criteria_json"]),
                "started_at": row["started_at"], "completed_at": row["completed_at"],
                "result": result}

    def acquisition_brief_history(self):
        with self.database.session() as (connection, _):
            return {"briefs": [self._brief_record(row) for row in connection.execute(
                "SELECT * FROM acquisition_briefs ORDER BY started_at DESC,id DESC LIMIT 20")],
                    "defaults": DEFAULTS, "market": "Fort Wayne, IN", "property_type": "Single Family"}

    def build_acquisition_brief(self, data):
        assumptions = criteria(data)
        encoded = json.dumps(assumptions, sort_keys=True)
        digest = sha256(encoded.encode()).hexdigest()
        now = datetime.now(timezone.utc)
        # SQLite write reservation serializes duplicate builds across threads/processes.
        with self.database.session(write=True) as (connection, _):
            row = connection.execute("SELECT * FROM acquisition_briefs WHERE criteria_hash=? ORDER BY started_at DESC LIMIT 1", (digest,)).fetchone()
            if row and row["status"] != "running" and row["completed_at"] > (now - timedelta(hours=24)).isoformat():
                return {**self._brief_record(row), "reused": True}
            if row and row["status"] == "running":
                if row["started_at"] > (now - timedelta(minutes=15)).isoformat():
                    raise ValueError("This shortlist is being built. Check saved briefs shortly.")
                # Recover a stopped worker using the same collector idempotency keys.
                brief_id = row["id"]
                connection.execute("UPDATE acquisition_briefs SET started_at=? WHERE id=?", (now.isoformat(), brief_id))
            else:
                brief_id = str(uuid4())
                connection.execute("INSERT INTO acquisition_briefs(id,criteria_hash,criteria_json,status,started_at) VALUES(?,?,?,'running',?)",
                                   (brief_id, digest, encoded, now.isoformat()))
        evidence = []
        warnings = []

        def collect(source, inputs, suffix):
            try:
                run = self.run_sentra({"sentra_id": source, "input_data": inputs,
                    "confirm_external_request": True, "idempotency_key": f"brief:{brief_id}:{suffix}"})
            except ValueError:
                warnings.append(f"{source}: unavailable or request limit reached; no estimate substituted.")
                return []
            evidence.append({"run_id": run["id"], "source": source, "status": run["status"],
                             "retrieved_at": run.get("completed_at")})
            if run["status"] != "success":
                warnings.append(f"{source}: evidence unavailable; no estimate substituted.")
                return []
            return run["result"].get("payload", {}).get("records", [])

        listings = collect("rentcast_sale_listings", {"search_intent": {"market": "Fort Wayne, IN",
            "property_types": ["single_family"], "max_total_price": assumptions["max_price"],
            "filters": {"min_beds": assumptions["min_beds"]}}, "max_results": 3}, "listings")
        cards = []
        excluded = 0
        seen = set()
        for listing in listings[:3]:
            address = listing.get("address")
            price, beds = listing.get("asking_price"), listing.get("bedrooms")
            identity = str(address).casefold()
            if (not address or identity in seen or listing.get("city", "").casefold() != "fort wayne"
                    or listing.get("state") != "IN" or listing.get("propertyType") != "Single Family"
                    or listing.get("status") != "Active" or type(price) not in (int, float) or price <= 0
                    or price > assumptions["max_price"] or type(beds) not in (int, float)
                    or beds < assumptions["min_beds"]):
                excluded += 1
                continue
            seen.add(identity)
            full_address = ", ".join(str(listing.get(key)) for key in
                                     ("address", "addressLine2", "city", "state", "zip") if listing.get(key))
            estimates = collect("rentcast_rent_estimate", {"address": full_address}, f"rent{len(cards)}")
            estimate = estimates[0] if estimates else None
            low = estimate.get("range_low") if estimate else None
            model = economics(price, low, assumptions) if type(low) in (int, float) and low > 0 else None
            cards.append({"listing": listing, "rent_estimate": estimate, "economics": model,
                          "screen": "needs_rent_evidence" if model is None else
                          "meets_assumed_yield" if model["yield_pct"] >= assumptions["min_yield_pct"] else "below_assumed_yield",
                          "next_actions": ["Confirm listing availability and asking price with the listing agent.",
                              "Inspect condition and replace the repair reserve with contractor quotes.",
                              "Verify achievable rent, lease restrictions, taxes, insurance and management costs.",
                              "Have the operator verify title, financing and their actual buying criteria before an offer."]})
        cards.sort(key=lambda card: (card["economics"] is not None,
                   card["economics"]["yield_pct"] if card["economics"] else 0), reverse=True)
        for card in cards:
            enrich_card(card, assumptions)
            card.pop("decision")  # Decisions are derived when reading, including legacy briefs.
        result = {"cards": cards, "evidence": evidence, "warnings": warnings, "excluded_count": excluded,
                  "coverage": "Up to 3 provider listings; a bounded sample, not the full market or direct MLS access.",
                  "basis": "Lower provider rent range; vacancy deducted first, then expenses as a share of collected rent. Cash basis includes asking price, assumed closing costs and repair reserve.",
                  "limitations": "Screening preferences are not verified buyer demand. AVM rent is an estimate. Expense allowance assumes taxes, insurance, management and maintenance; actual costs and future capital work remain unverified. Debt, income tax, resale proceeds and transaction profit are excluded.",
                  "max_provider_requests": 4}
        status = "ready" if cards and not warnings and all(card["economics"] for card in cards) else "incomplete"
        with self.database.session(write=True) as (connection, _):
            connection.execute("UPDATE acquisition_briefs SET status=?,result_json=?,completed_at=? WHERE id=?",
                               (status, json.dumps(result, allow_nan=False), datetime.now(timezone.utc).isoformat(), brief_id))
            return self._brief_record(connection.execute("SELECT * FROM acquisition_briefs WHERE id=?", (brief_id,)).fetchone())
