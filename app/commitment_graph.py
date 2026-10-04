from __future__ import annotations

from datetime import datetime, timezone
import json
from uuid import UUID, uuid4

from core.memory.models import utc_now
from .validation import deal_exists, list_field, number_field, text_field

STRATEGIES = {"assignment", "resale"}
MANDATE_STATUSES = {"active", "paused", "expired"}
CAPITAL_STATUSES = {"active", "paused", "unverified", "expired"}
CAPITAL_TYPES = {"partner", "lender", "self", "other"}
OUTCOMES = {
    "closed", "lost", "withdrawn", "buyer_declined", "funding_failed",
    "title_failed", "seller_changed", "no_response", "other",
}


def _uuid(value, field):
    try:
        return str(UUID(str(value)))
    except (TypeError, ValueError, AttributeError) as exc:
        raise ValueError(f"{field} must be a valid UUID") from exc


def _iso(value, field, required=True):
    if value in (None, ""):
        if required:
            raise ValueError(f"{field} is required")
        return ""
    if not isinstance(value, str):
        raise ValueError(f"{field} must be ISO-8601 text")
    try:
        parsed = datetime.fromisoformat(value.replace("Z", "+00:00"))
    except ValueError as exc:
        raise ValueError(f"{field} must be ISO-8601 text") from exc
    if parsed.tzinfo is None:
        parsed = parsed.replace(tzinfo=timezone.utc)
    return parsed.astimezone(timezone.utc).isoformat()


def _current(record, now):
    if record["status"] != "active":
        return False
    expires_at = record.get("expires_at") or ""
    if not expires_at:
        return True
    return datetime.fromisoformat(expires_at) >= now


class CommitmentGraphMixin:
    """Buyer-first demand/capital graph and explainable deal-readiness layer.

    The score is an operational evidence score, not a calibrated closing probability,
    property valuation, lending decision, or investment recommendation.
    """

    def create_buyer_mandate(self, data):
        buyer_id = _uuid(data.get("buyer_id"), "buyer_id")
        name = text_field(data, "name", 160, required=False) or "Standing buyer mandate"
        markets = list_field(data, "markets", max_items=50)
        strategies = list_field(data, "strategies", allowed=STRATEGIES, max_items=2)
        property_types = list_field(data, "property_types", max_items=30, required=False)
        max_total_price = number_field(data, "max_total_price", nonnegative=True)
        max_repairs = number_field(data, "max_repairs", default=0, nonnegative=True)
        priority = int(number_field(data, "priority", default=50, nonnegative=True))
        if priority > 100:
            raise ValueError("priority cannot exceed 100")
        status = text_field(data, "status", 20, required=False) or "active"
        if status not in MANDATE_STATUSES:
            raise ValueError("unsupported mandate status")
        evidence_reference = text_field(data, "evidence_reference", 500)
        verified_at = _iso(data.get("verified_at"), "verified_at")
        expires_at = _iso(data.get("expires_at"), "expires_at", required=False)
        now = utc_now().isoformat()
        mandate_id = str(uuid4())
        with self.database.session(write=True) as (connection, _):
            if connection.execute("SELECT id FROM buyers WHERE id=?", (buyer_id,)).fetchone() is None:
                raise LookupError("Buyer not found")
            connection.execute(
                """INSERT INTO buyer_mandates(
                    id,buyer_id,name,markets_json,strategies_json,property_types_json,
                    max_total_price,max_repairs,priority,status,evidence_reference,
                    verified_at,expires_at,created_at
                ) VALUES(?,?,?,?,?,?,?,?,?,?,?,?,?,?)""",
                (
                    mandate_id, buyer_id, name, json.dumps(markets), json.dumps(strategies),
                    json.dumps(property_types), max_total_price, max_repairs, priority,
                    status, evidence_reference, verified_at, expires_at, now,
                ),
            )
        return {
            "id": mandate_id, "buyer_id": buyer_id, "name": name, "markets": markets,
            "strategies": strategies, "property_types": property_types,
            "max_total_price": max_total_price, "max_repairs": max_repairs,
            "priority": priority, "status": status,
            "evidence_reference": evidence_reference, "verified_at": verified_at,
            "expires_at": expires_at, "created_at": now,
        }

    def create_capital_profile(self, data):
        name = text_field(data, "name", 160)
        provider_type = text_field(data, "provider_type", 20)
        if provider_type not in CAPITAL_TYPES:
            raise ValueError("unsupported capital provider type")
        markets = list_field(data, "markets", max_items=50, required=False)
        strategies = list_field(data, "strategies", allowed=STRATEGIES, max_items=2, required=False)
        max_commitment = number_field(data, "max_commitment", nonnegative=True)
        available_amount = number_field(data, "available_amount", nonnegative=True)
        if available_amount > max_commitment:
            raise ValueError("available_amount cannot exceed max_commitment")
        status = text_field(data, "status", 20, required=False) or "unverified"
        if status not in CAPITAL_STATUSES:
            raise ValueError("unsupported capital status")
        verification_reference = text_field(data, "verification_reference", 500)
        verified_at = _iso(data.get("verified_at"), "verified_at")
        expires_at = _iso(data.get("expires_at"), "expires_at", required=False)
        terms = data.get("terms", {})
        if not isinstance(terms, dict):
            raise ValueError("terms must be an object")
        now = utc_now().isoformat()
        profile_id = str(uuid4())
        with self.database.session(write=True) as (connection, _):
            connection.execute(
                """INSERT INTO capital_profiles(
                    id,name,provider_type,markets_json,strategies_json,max_commitment_cents,
                    available_cents,status,verification_reference,verified_at,expires_at,
                    terms_json,created_at
                ) VALUES(?,?,?,?,?,?,?,?,?,?,?,?,?)""",
                (
                    profile_id, name, provider_type, json.dumps(markets), json.dumps(strategies),
                    round(max_commitment * 100), round(available_amount * 100), status,
                    verification_reference, verified_at, expires_at,
                    json.dumps(terms, allow_nan=False), now,
                ),
            )
        return {
            "id": profile_id, "name": name, "provider_type": provider_type,
            "markets": markets, "strategies": strategies,
            "max_commitment": max_commitment, "available_amount": available_amount,
            "status": status, "verification_reference": verification_reference,
            "verified_at": verified_at, "expires_at": expires_at,
            "terms": terms, "created_at": now,
        }

    def record_commitment_outcome(self, data):
        deal_id = _uuid(data.get("deal_id"), "deal_id")
        outcome = text_field(data, "outcome", 40)
        if outcome not in OUTCOMES:
            raise ValueError("unsupported commitment outcome")
        reason_code = text_field(data, "reason_code", 80)
        evidence_reference = text_field(data, "evidence_reference", 500)
        note = text_field(data, "note", 1000, required=False)
        buyer_id = data.get("buyer_id")
        capital_profile_id = data.get("capital_profile_id")
        buyer_id = _uuid(buyer_id, "buyer_id") if buyer_id else ""
        capital_profile_id = _uuid(capital_profile_id, "capital_profile_id") if capital_profile_id else ""
        now = utc_now().isoformat()
        outcome_id = str(uuid4())
        with self.database.session(write=True) as (connection, _):
            deal_exists(connection, deal_id)
            if buyer_id and connection.execute("SELECT id FROM buyers WHERE id=?", (buyer_id,)).fetchone() is None:
                raise LookupError("Buyer not found")
            if capital_profile_id and connection.execute(
                "SELECT id FROM capital_profiles WHERE id=?", (capital_profile_id,)
            ).fetchone() is None:
                raise LookupError("Capital profile not found")
            connection.execute(
                """INSERT INTO commitment_outcomes(
                    id,deal_id,buyer_id,capital_profile_id,outcome,reason_code,
                    evidence_reference,note,created_at
                ) VALUES(?,?,?,?,?,?,?,?,?)""",
                (
                    outcome_id, deal_id, buyer_id, capital_profile_id, outcome, reason_code,
                    evidence_reference, note, now,
                ),
            )
        return {
            "id": outcome_id, "deal_id": deal_id, "buyer_id": buyer_id,
            "capital_profile_id": capital_profile_id, "outcome": outcome,
            "reason_code": reason_code, "evidence_reference": evidence_reference,
            "note": note, "created_at": now,
        }

    def deal_readiness(self, deal_id):
        with self.database.session() as (connection, _):
            deal = deal_exists(connection, deal_id)
            row = connection.execute(
                """SELECT d.*,p.city,p.state,p.zip
                   FROM deals d JOIN properties p ON p.id=d.property_id WHERE d.id=?""",
                (str(deal["id"]),),
            ).fetchone()
            return self._deal_readiness(connection, dict(row))

    def _commitment_graph_state(self, connection, deals):
        mandates = [self._mandate_json(row) for row in connection.execute(
            "SELECT * FROM buyer_mandates ORDER BY priority DESC,created_at DESC,id"
        )]
        capital = [self._capital_json(row) for row in connection.execute(
            "SELECT * FROM capital_profiles ORDER BY created_at DESC,id"
        )]
        outcomes = [dict(row) for row in connection.execute(
            "SELECT * FROM commitment_outcomes ORDER BY created_at DESC,id LIMIT 250"
        )]
        readiness = [self._deal_readiness(connection, deal) for deal in deals]
        return {
            "buyer_mandates": mandates,
            "capital_profiles": capital,
            "recent_outcomes": outcomes,
            "deal_readiness": readiness,
            "score_semantics": (
                "Operational readiness evidence only; not a calibrated closing probability, "
                "valuation, lending approval, or investment recommendation."
            ),
        }

    @staticmethod
    def _mandate_json(row):
        item = dict(row)
        item["markets"] = json.loads(item.pop("markets_json"))
        item["strategies"] = json.loads(item.pop("strategies_json"))
        item["property_types"] = json.loads(item.pop("property_types_json"))
        return item

    @staticmethod
    def _capital_json(row):
        item = dict(row)
        item["markets"] = json.loads(item.pop("markets_json"))
        item["strategies"] = json.loads(item.pop("strategies_json"))
        item["terms"] = json.loads(item.pop("terms_json"))
        item["max_commitment"] = item.pop("max_commitment_cents") / 100
        item["available_amount"] = item.pop("available_cents") / 100
        return item

    def reverse_match_candidate(self, connection, *, market, property_type=None, asking_price=None):
        """Match a research candidate to current standing demand before a deal exists."""
        now = datetime.now(timezone.utc)
        normalized_market = " ".join(str(market or "").lower().replace(",", " ").split())
        normalized_type = " ".join(str(property_type or "").lower().replace("-", "_").split())
        try:
            price = float(asking_price) if asking_price not in (None, "") else None
        except (TypeError, ValueError):
            price = None
        rows = connection.execute(
            """SELECT m.*,b.name AS buyer_name,b.company AS buyer_company
               FROM buyer_mandates m JOIN buyers b ON b.id=m.buyer_id
               WHERE m.status='active' ORDER BY m.priority DESC,m.created_at DESC,m.id"""
        )
        matches = []
        for row in rows:
            mandate = self._mandate_json(row)
            if not _current(mandate, now):
                continue
            mandate_markets = [" ".join(v.lower().replace(",", " ").split()) for v in mandate["markets"]]
            if mandate_markets and not any(
                target in normalized_market or normalized_market in target
                for target in mandate_markets if normalized_market
            ):
                continue
            reasons = ["current standing buyer mandate matches market"]
            score = 45
            types = [" ".join(v.lower().replace("-", "_").split()) for v in mandate["property_types"]]
            if property_type:
                if types and normalized_type not in types:
                    continue
                score += 25
                reasons.append("property type fits mandate")
            elif types:
                reasons.append("property type still needs confirmation")
            else:
                score += 15
                reasons.append("mandate accepts any recorded property type")
            if price is not None:
                if price > mandate["max_total_price"]:
                    continue
                price_headroom = max(0.0, mandate["max_total_price"] - price)
                score += 20
                reasons.append(f"asking price is within mandate by ${price_headroom:,.0f}")
            else:
                reasons.append("asking price is unknown; price fit is not yet proven")
            score += round(min(10, mandate["priority"] / 10))
            matches.append({
                "mandate_id": mandate["id"],
                "buyer_id": mandate["buyer_id"],
                "buyer_name": row["buyer_name"],
                "buyer_company": row["buyer_company"],
                "score": min(100, int(score)),
                "priority": mandate["priority"],
                "max_total_price": mandate["max_total_price"],
                "max_repairs": mandate["max_repairs"],
                "reasons": reasons,
                "verified_at": mandate["verified_at"],
                "expires_at": mandate["expires_at"],
            })
        matches.sort(key=lambda item: (-item["score"], -item["priority"], item["buyer_name"].lower()))
        return matches
    def _deal_readiness(self, connection, deal):
        deal_id = str(deal["id"])
        now = datetime.now(timezone.utc)
        score = 0
        components = {}
        blockers = []
        next_actions = []

        # Demand commitment: favor an eligible buyer that also has a current standing mandate.
        match_row = connection.execute(
            "SELECT matches_json,created_at FROM buyer_match_runs WHERE deal_id=? "
            "ORDER BY created_at DESC,id LIMIT 1", (deal_id,)
        ).fetchone()
        matches = json.loads(match_row["matches_json"]) if match_row else []
        eligible = [m for m in matches if m.get("eligible_on_recorded_criteria")]
        eligible_ids = {str(m.get("buyer_id") or m.get("id") or "") for m in eligible}
        current_mandates = []
        for row in connection.execute("SELECT * FROM buyer_mandates WHERE status='active'"):
            item = self._mandate_json(row)
            if _current(item, now) and item["buyer_id"] in eligible_ids:
                current_mandates.append(item)
        if current_mandates:
            demand = 35
        elif eligible:
            demand = 20
            blockers.append("Eligible buyer criteria exist, but no current standing mandate is recorded.")
            next_actions.append("Confirm a standing buyer mandate with evidence and expiration.")
        else:
            demand = 0
            blockers.append("No current eligible buyer path is recorded.")
            next_actions.append("Run buyer matching and secure a standing mandate before pursuing the deal.")
        components["demand_commitment"] = {"score": demand, "max": 35}
        score += demand

        # Underwriting / economic evidence.
        underwriting = connection.execute(
            "SELECT id FROM underwritings WHERE deal_id=? ORDER BY created_at DESC,id LIMIT 1", (deal_id,)
        ).fetchone()
        plan = connection.execute(
            "SELECT * FROM financial_plans WHERE deal_id=? ORDER BY created_at DESC,id LIMIT 1", (deal_id,)
        ).fetchone()
        evidence = (10 if underwriting else 0) + (10 if plan else 0)
        components["deal_evidence"] = {"score": evidence, "max": 20}
        score += evidence
        if not underwriting:
            blockers.append("Current underwriting is missing.")
            next_actions.append("Complete underwriting from reviewed evidence.")
        if not plan:
            blockers.append("Current financial plan is missing.")
            next_actions.append("Record seller terms, target economics, and cash-at-risk limits.")

        # Capital path against recorded peak cash-at-risk.
        required_cents = int(plan["planned_cash_at_risk_cents"]) if plan else None
        market = " ".join(part for part in (deal.get("city"), deal.get("state")) if part).strip().lower()
        capital_rows = [self._capital_json(row) for row in connection.execute(
            "SELECT * FROM capital_profiles ORDER BY created_at DESC,id"
        )]
        current_capital = []
        for item in capital_rows:
            if not _current(item, now):
                continue
            if item["strategies"] and deal.get("strategy") not in item["strategies"]:
                continue
            if item["markets"] and market and not any(m.lower() in market or market in m.lower() for m in item["markets"]):
                continue
            current_capital.append(item)
        if required_cents is None:
            capital_score = 0
        elif required_cents <= 0:
            capital_score = 20
        else:
            required = required_cents / 100
            verified = [c for c in current_capital if c["available_amount"] >= required]
            capital_score = 20 if verified else 0
        components["capital_path"] = {"score": capital_score, "max": 20}
        score += capital_score
        if required_cents is not None and required_cents > 0 and not capital_score:
            blockers.append("No current recorded capital profile covers planned cash at risk.")
            next_actions.append("Confirm partner/lender availability against the current cash-at-risk plan.")

        stage_points = {
            "research": 1, "contacting": 2, "qualified": 4, "underwriting": 6,
            "offer_decision": 9, "contracted": 12, "disposition": 13,
            "closing": 14, "completed": 15, "lost": 0,
        }
        operations = stage_points.get(deal.get("stage"), 0)
        components["transaction_progress"] = {"score": operations, "max": 15}
        score += operations

        # Outcome evidence rewards actual network history without pretending it is a probability.
        outcome_rows = connection.execute(
            "SELECT outcome,buyer_id FROM commitment_outcomes ORDER BY created_at DESC"
        ).fetchall()
        network = 0
        if outcome_rows:
            closed = sum(1 for row in outcome_rows if row["outcome"] == "closed")
            network = min(10, 2 + closed * 2)
        components["network_outcome_evidence"] = {"score": network, "max": 10}
        score += network
        if not outcome_rows:
            next_actions.append("Record closed and failed outcomes so ClubSP can learn which paths actually perform.")

        score = max(0, min(100, int(score)))
        label = "close-ready" if score >= 80 else "strong path" if score >= 60 else "needs work" if score >= 40 else "weak path"
        return {
            "deal_id": deal_id,
            "score": score,
            "label": label,
            "components": components,
            "blockers": list(dict.fromkeys(blockers)),
            "next_actions": list(dict.fromkeys(next_actions)),
            "eligible_buyer_count": len(eligible),
            "current_mandate_count": len(current_mandates),
            "required_cash_at_risk": None if required_cents is None else required_cents / 100,
            "calibrated_probability": False,
        }
