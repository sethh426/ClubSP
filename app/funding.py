"""Append-only, owner-entered partner/lender reviews; no loan or payment execution."""
from datetime import date
import json
from uuid import UUID, uuid4

from core.memory.models import utc_now
from .money import cents, dollars
from .operations import business_today
from .validation import deal_exists, list_field, text_field
from .deal_actions import attach_actions
from .schema import assert_component_compatible, ensure_component


AMOUNTS = ("required_funding", "committed_funding", "owner_cash_required", "contingent_liability", "known_financing_cost")
REFERENCES = ("funding_need_reference", "terms_reference", "allocation_reference",
              "obligations_reference", "costs_reference", "conditions_reference")


def canonical_date(data, key):
    value = text_field(data, key, 10)
    try:
        parsed = date.fromisoformat(value)
        if parsed.isoformat() != value:
            raise ValueError()
    except ValueError:
        raise ValueError(f"{key} must be YYYY-MM-DD") from None
    return parsed


class FundingBook:
    def __init__(self, database):
        self.database = database
        with database.session(write=True) as (connection, _):
            assert_component_compatible(connection, "funding")
            connection.execute("""
                CREATE TABLE IF NOT EXISTS funding_reviews (
                    id TEXT PRIMARY KEY,
                    deal_id TEXT NOT NULL REFERENCES deals(id),
                    request_key TEXT NOT NULL UNIQUE,
                    previous_id TEXT UNIQUE REFERENCES funding_reviews(id),
                    underwriting_id TEXT NOT NULL REFERENCES underwritings(id),
                    financial_plan_id TEXT NOT NULL REFERENCES financial_plans(id),
                    payload_json TEXT NOT NULL,
                    created_at TEXT NOT NULL
                )
            """)
            connection.execute("CREATE INDEX IF NOT EXISTS funding_deal ON funding_reviews(deal_id)")
            ensure_component(connection, "funding")

    @staticmethod
    def decode(row):
        record = dict(row)
        record.update(json.loads(record.pop("payload_json")))
        for name in AMOUNTS:
            record[name] = dollars(record[name + "_cents"])
        return record

    def save(self, deal_id, data):
        try:
            key = str(UUID(text_field(data, "request_key", 36)))
        except ValueError:
            raise ValueError("request_key must be a UUID for safe retries") from None
        try:
            expected_plan = str(UUID(text_field(data, "financial_plan_id", 36)))
            expected_uw = str(UUID(text_field(data, "underwriting_id", 36)))
        except ValueError:
            raise ValueError("Review must identify the financial plan and underwriting shown to the owner") from None
        payload = {name + "_cents": cents(data.get(name), name) for name in AMOUNTS}
        if payload["required_funding_cents"] == 0:
            raise ValueError("Record the positive external funding requirement, including its basis")
        payload["counterparty"] = text_field(data, "counterparty", 160)
        payload["kind"] = text_field(data, "kind", 20)
        if payload["kind"] not in {"lender", "partner"}:
            raise ValueError("kind must be lender or partner")
        payload["status"] = text_field(data, "status", 20)
        if payload["status"] not in {"pending", "owner_reviewed", "withdrawn"}:
            raise ValueError("status must be pending, owner_reviewed, or withdrawn")
        payload["note"] = text_field(data, "note", 2000)
        reviewed = canonical_date(data, "reviewed_on")
        expires = canonical_date(data, "expires_on")
        if reviewed > business_today() or expires < reviewed:
            raise ValueError("Review cannot be in the future; expiry cannot predate review")
        payload.update(reviewed_on=reviewed.isoformat(), expires_on=expires.isoformat())
        payload["unresolved_conditions"] = list_field(data, "unresolved_conditions", max_items=20, required=False)
        for name in REFERENCES:
            required = name == "funding_need_reference" or (
                payload["status"] == "owner_reviewed" and
                (name != "conditions_reference" or not payload["unresolved_conditions"]))
            payload[name] = text_field(data, name, 500, required=required)
        payload["owner_confirmed"] = data.get("owner_confirmed") is True
        if payload["status"] == "owner_reviewed" and not payload["owner_confirmed"]:
            raise ValueError("Owner must review deal-specific allocation, obligations, costs, and conditions")
        previous_id = text_field(data, "supersedes_id", 36, required=False) or None
        if previous_id:
            try:
                previous_id = str(UUID(previous_id))
            except ValueError:
                raise ValueError("supersedes_id must identify the latest funding review") from None
        encoded = json.dumps(payload, sort_keys=True, allow_nan=False)
        with self.database.session(write=True) as (connection, _):
            deal = deal_exists(connection, deal_id)
            existing = connection.execute("SELECT * FROM funding_reviews WHERE request_key=?", (key,)).fetchone()
            if existing:
                if (existing["deal_id"], existing["previous_id"], existing["payload_json"], existing["financial_plan_id"], existing["underwriting_id"]) != (deal["id"], previous_id, encoded, expected_plan, expected_uw):
                    raise ValueError("request_key already records different funding data")
                return self.decode(existing)
            if deal["stage"] in {"completed", "lost"}:
                raise ValueError("Ended deals retain funding history; no new funding review can be added")
            latest = connection.execute("SELECT id FROM funding_reviews WHERE deal_id=? ORDER BY rowid DESC LIMIT 1", (deal["id"],)).fetchone()
            if previous_id != (latest["id"] if latest else None):
                raise ValueError("Refresh and supersede the latest funding review; history is preserved")
            plan = connection.execute("SELECT id,underwriting_id FROM financial_plans WHERE deal_id=? ORDER BY created_at DESC,id LIMIT 1", (deal["id"],)).fetchone()
            uw = connection.execute("SELECT id FROM underwritings WHERE deal_id=? ORDER BY created_at DESC,id LIMIT 1", (deal["id"],)).fetchone()
            if not plan or not uw or plan["underwriting_id"] != uw["id"]:
                raise ValueError("Save a financial plan tied to current underwriting before reviewing funding")
            if (expected_plan, expected_uw) != (plan["id"], uw["id"]):
                raise ValueError("Deal terms changed during review; reload and review the current versions")
            record_id = str(uuid4())
            connection.execute("INSERT INTO funding_reviews VALUES(?,?,?,?,?,?,?,?)", (
                record_id, deal["id"], key, previous_id, uw["id"], plan["id"], encoded, utc_now().isoformat()))
            return self.decode(connection.execute("SELECT * FROM funding_reviews WHERE id=?", (record_id,)).fetchone())

    def state(self, workspace):
        with self.database.session() as (connection, _):
            rows = connection.execute("SELECT * FROM funding_reviews ORDER BY rowid DESC").fetchall()
        histories = {}
        for row in rows:
            histories.setdefault(row["deal_id"], []).append(self.decode(row))
        active_deals = [d for d in workspace["deals"] if d["stage"] not in {"completed", "lost"}]
        allocations = {}
        for deal in active_deals:
            history = histories.get(deal["id"], [])
            if history and history[0]["status"] == "owner_reviewed":
                reference = history[0]["allocation_reference"].strip().casefold()
                allocations.setdefault(reference, set()).add(deal["id"])
        deals = []
        today = business_today()
        for deal in workspace["deals"]:
            history = histories.get(deal["id"], [])
            latest = history[0] if history else None
            finance = deal["finance"]
            plan = finance["plan"]
            ended = deal["stage"] in {"completed", "lost"}
            blockers = []
            gap = None
            current = False
            if ended:
                blockers.append("Ended deal: funding records are historical")
            if not plan:
                blockers.append("Record proposed terms and owner cash exposure first")
            if not latest:
                blockers.append("Record a deal-specific funding review")
            else:
                if latest["status"] != "owner_reviewed":
                    blockers.append("Funding is pending owner review or withdrawn")
                if date.fromisoformat(latest["expires_on"]) < today:
                    blockers.append("Recorded commitment has expired")
                current = bool(plan and deal["underwriting"] and
                    latest["financial_plan_id"] == plan["id"] and
                    latest["underwriting_id"] == deal["underwriting"]["id"])
                if not current:
                    blockers.append("Deal terms or underwriting changed; review funding again")
                gap = max(0, latest["required_funding_cents"] - latest["committed_funding_cents"])
                if gap:
                    blockers.append("Recorded external commitment does not cover the funding requirement")
                if latest["unresolved_conditions"]:
                    blockers.append("Resolve funding conditions: " + "; ".join(latest["unresolved_conditions"]))
                if len(allocations.get(latest["allocation_reference"].strip().casefold(), set())) > 1:
                    blockers.append("Allocation evidence is reused across active deals; record distinct earmarked amounts")
                if plan:
                    cash_required = latest["owner_cash_required_cents"]
                    if cash_required > plan["planned_cash_at_risk_cents"]:
                        blockers.append("Funding requires more owner cash than the financial plan records")
                    recorded_exposure = cash_required + latest["contingent_liability_cents"]
                    if max(recorded_exposure, cents(finance["summary"]["unrecovered_cash"]), plan["planned_cash_at_risk_cents"]) > plan["max_cash_at_risk_cents"]:
                        blockers.append("Owner cash exposure exceeds the recorded limit")
                    if deal["strategy"] == "resale" and latest["required_funding_cents"] + cash_required < plan["seller_price_cents"]:
                        blockers.append("Recorded funding requirement and owner cash do not cover even the purchase price")
                    inputs = deal["underwriting"]["inputs"] if deal["underwriting"] else {}
                    cost_fields = ["owner_transaction_costs", "partner_payout_allowance"]
                    if deal["strategy"] == "resale":
                        cost_fields.append("buyer_funding_holding")
                    cost_allowance = sum(cents(inputs.get(name, 0)) for name in cost_fields)
                    if latest["known_financing_cost_cents"] > cost_allowance:
                        blockers.append("Known funding costs exceed the relevant underwriting cost allowances")
            blockers.extend("Financial plan: " + reason for reason in finance["contract_blockers"])
            blockers = list(dict.fromkeys(blockers))
            deals.append({"id": deal["id"], "address": deal["address"], "city": deal["city"],
                "strategy": deal["strategy"], "stage": deal["stage"], "ended": ended,
                "review": latest, "history": history, "review_matches_current_terms": current,
                "current_financial_plan_id": plan["id"] if plan else None,
                "current_underwriting_id": deal["underwriting"]["id"] if deal["underwriting"] else None,
                "funding_gap": None if gap is None else dollars(gap),
                "forecast_net": plan["forecasts"]["base"]["net_contribution"] if plan else None,
                "owner_cash_limit": plan["max_cash_at_risk"] if plan else None,
                "checks_pass": not blockers, "blockers": blockers,
                "next_action": blockers[0] if blockers else "Confirm availability and disbursement with the funder and closing professional",
                "can_record": bool(plan and plan["current_underwriting"] and not ended)})
        pipeline = attach_actions(deals, workspace)
        return {"mode": "owner_entered_advisory", "deals": deals, "pipeline": pipeline,
            "summary": {"active_deals": len(active_deals),
                "checks_pass": sum(d["checks_pass"] for d in deals if not d["ended"]),
                "needs_review": sum(not d["checks_pass"] for d in deals if not d["ended"])} }
