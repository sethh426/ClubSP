"""Read-only prioritization of entered deals, not sourcing or execution authority."""
from datetime import datetime, timezone
import hashlib
import json
from uuid import uuid4

from core.memory.models import utc_now
from .money import cents, dollars
from .validation import list_field, text_field
from .sourcing import sale_snapshot


def canonical(value):
    return "_".join(str(value).strip().lower().split())


def property_evidence(connection, property_id):
    """Never choose an arbitrary last row when current sources disagree."""
    facts = [json.loads(row["body"]) for row in connection.execute(
        "SELECT body FROM memory WHERE collection='facts' "
        "AND json_extract(body,'$.subject_type')='property' "
        "AND json_extract(body,'$.subject_id')=? ORDER BY id", (str(property_id),)
    )]
    superseded = {f["supersedes_fact_id"] for f in facts if f.get("supersedes_fact_id")}
    current = [f for f in facts if f.get("status") == "active" and f["id"] not in superseded]
    grouped = {}
    for fact in current:
        grouped.setdefault(canonical(fact["attribute"]), []).append(fact)
    conflicts = sorted(key for key, group in grouped.items()
                       if len({json.dumps(f["value"], sort_keys=True) if key != "property_type"
                               else canonical(f["value"]) for f in group}) > 1)
    values = {key: group[0]["value"] for key, group in grouped.items() if key not in conflicts}
    digest = hashlib.sha256(json.dumps(current, sort_keys=True, separators=(",", ":")).encode()).hexdigest()
    return {"facts": current, "values": values, "conflicts": conflicts, "digest": digest}


def recent(value, max_age_days, now):
    try:
        observed = datetime.fromisoformat(value.replace("Z", "+00:00"))
        if observed.tzinfo is None:
            observed = observed.replace(tzinfo=timezone.utc)
        return 0 <= (now - observed).total_seconds() <= max_age_days * 86400
    except (ValueError, TypeError, AttributeError):
        return False


class OpportunitiesMixin:
    def save_opportunity_policy(self, data):
        markets = [" ".join(v.lower().split()) for v in list_field(data, "markets")]
        strategies = list_field(data, "strategies", allowed={"assignment", "resale"})
        types = list_field(data, "property_types")
        limits = {key: cents(data.get(key), key) for key in (
            "max_seller_price", "max_deal_cash_at_risk", "max_portfolio_cash_at_risk",
            "min_downside_net",
        )}
        days = data.get("evidence_max_age_days")
        if isinstance(days, bool) or not isinstance(days, int) or not 1 <= days <= 365:
            raise ValueError("evidence_max_age_days must be an integer from 1 to 365")
        if any(limits[k] <= 0 for k in ("max_seller_price", "max_deal_cash_at_risk", "max_portfolio_cash_at_risk")):
            raise ValueError("Price and cash limits must be greater than zero")
        if limits["max_deal_cash_at_risk"] > limits["max_portfolio_cash_at_risk"]:
            raise ValueError("Deal cash limit cannot exceed the portfolio cash limit")
        policy = {"id": str(uuid4()), "markets": sorted(set(markets)), "strategies": strategies,
                  "property_types": sorted({canonical(v) for v in types}),
                  **{k: dollars(v) for k, v in limits.items()}, "evidence_max_age_days": days,
                  "basis": text_field(data, "basis", 2000), "created_at": utc_now().isoformat()}
        with self.database.session(write=True) as (connection, _):
            connection.execute("INSERT INTO opportunity_policies(id,body,created_at) VALUES(?,?,?)",
                               (policy["id"], json.dumps(policy), policy["created_at"]))
        return policy

    def _opportunity_state(self, connection, deals):
        row = connection.execute("SELECT body FROM opportunity_policies ORDER BY rowid DESC LIMIT 1").fetchone()
        policy = json.loads(row["body"]) if row else None
        now = utc_now()
        # This is a conservative planning sum, not verified bank availability.
        exposure = 0
        unknown_exposure = []
        for deal in deals:
            finance = deal["finance"]
            if deal["stage"] in {"completed", "lost"}:
                exposure += cents(finance["summary"]["unrecovered_cash"])
            elif finance["plan"]:
                exposure += cents(finance["projected_cash_at_risk"])
            else:
                exposure += cents(finance["summary"]["unrecovered_cash"])
                unknown_exposure.append(deal["id"])
        items = []
        for deal in deals:
            if deal["stage"] in {"contracted", "disposition", "closing", "completed", "lost"}:
                continue
            prop = dict(connection.execute("SELECT * FROM properties WHERE id=?", (deal["property_id"],)).fetchone())
            evidence = property_evidence(connection, prop["id"])
            outside, blockers, gaps = [], [], []
            if not policy:
                gaps.append("Save a buy box and risk policy")
            location = " ".join(f"{prop['city']}, {prop['state']}".lower().split())
            prop_type = evidence["values"].get("property_type")
            if evidence["conflicts"]:
                blockers.append("Resolve conflicting current facts: " + ", ".join(evidence["conflicts"]))
            if prop_type is None:
                gaps.append("Record an unambiguous current property type")
            if not evidence["values"].get("recorded_owner_name"):
                gaps.append("Record owner-of-record evidence; separately verify seller authority")
            if policy:
                if location not in policy["markets"]:
                    outside.append("Market is outside the recorded buy box")
                if deal["strategy"] not in policy["strategies"]:
                    outside.append("Strategy is outside the recorded buy box")
                if prop_type is not None and canonical(prop_type) not in policy["property_types"]:
                    outside.append("Property type is outside the recorded buy box")
                if any(not recent(f["observed_at"], policy["evidence_max_age_days"], now) for f in evidence["facts"]):
                    gaps.append("Recheck stale or future-dated property evidence")
                if unknown_exposure:
                    gaps.append("Portfolio cash exposure is incomplete: active deals lack plans")
                if exposure > cents(policy["max_portfolio_cash_at_risk"]):
                    blockers.append("Estimated portfolio cash exposure exceeds the policy limit")
            uw, finance = deal["underwriting"], deal["finance"]
            sales = sale_snapshot(connection, prop["id"])
            if not sales["items"]:
                gaps.append("Review comparable-sale evidence for the manual exit-price assumption")
            elif any((now.date() - datetime.fromisoformat(s["sale"]["sale_date"]).date()).days > 365 for s in sales["items"]):
                gaps.append("Review comparable sales older than one year; refresh the exit-price evidence")
            if sales["conflicts"]:
                blockers.append("Resolve conflicting comparable-sale prices")
            plan = finance["plan"]
            buyer_candidates = []
            if not uw:
                gaps.append("Record sourced underwriting assumptions")
            else:
                if uw["result"].get("sale_evidence", {}).get("digest") != sales["digest"]:
                    gaps.append("Comparable-sale evidence changed or was not tracked; review and resave underwriting")
                if uw["result"].get("evidence_digest") != evidence["digest"]:
                    gaps.append("Underwriting evidence changed or was not tracked; review and resave underwriting")
                if policy and not recent(uw["created_at"], policy["evidence_max_age_days"], now):
                    gaps.append("Underwriting is stale; recheck assumptions")
                buyer_candidates = self._compare_buyers(connection, deal)
            if not plan:
                gaps.append("Record fixed seller terms and peak cash exposure")
            else:
                blockers.extend(finance["contract_blockers"])
                if policy:
                    if cents(plan["seller_price"]) > cents(policy["max_seller_price"]):
                        outside.append("Seller price exceeds the buy-box limit")
                    if cents(finance["projected_cash_at_risk"]) > cents(policy["max_deal_cash_at_risk"]):
                        blockers.append("Estimated deal cash exposure exceeds the policy limit")
                    if cents_signed(plan["forecasts"]["downside"]["net_contribution"]) < cents(policy["min_downside_net"]):
                        blockers.append("Fixed-price downside contribution is below the policy minimum")
            current_buyers = [b for b in buyer_candidates if b["eligible_on_recorded_criteria"] and b["funding_verified_currently"]]
            if not current_buyers:
                gaps.append("Confirm a criteria-fit buyer with current owner-reviewed funding evidence")
            reviews = [t["title"] for t in deal["tasks"] if t["status"] == "open" and t["blocking_stage"] in {"contracted", "any"}]
            if reviews:
                gaps.append("Complete pre-contract evidence reviews: " + "; ".join(reviews))

            evidence_current = bool(
                prop_type is not None
                and evidence["values"].get("recorded_owner_name")
                and not evidence["conflicts"]
                and (not policy or all(recent(f["observed_at"], policy["evidence_max_age_days"], now) for f in evidence["facts"]))
            )
            sales_current = bool(
                sales["items"] and not sales["conflicts"]
                and all((now.date() - datetime.fromisoformat(s["sale"]["sale_date"]).date()).days <= 365
                        for s in sales["items"])
            )
            underwriting_current = bool(
                uw
                and uw["result"].get("sale_evidence", {}).get("digest") == sales["digest"]
                and uw["result"].get("evidence_digest") == evidence["digest"]
                and (not policy or recent(uw["created_at"], policy["evidence_max_age_days"], now))
            )
            plan_current = bool(
                plan and not finance["contract_blockers"]
                and (not policy or (
                    cents(plan["seller_price"]) <= cents(policy["max_seller_price"])
                    and cents(finance["projected_cash_at_risk"]) <= cents(policy["max_deal_cash_at_risk"])
                    and cents_signed(plan["forecasts"]["downside"]["net_contribution"]) >= cents(policy["min_downside_net"])
                ))
            )
            readiness = {
                "buy_box_fit": 20 if policy and not outside else 0,
                "property_evidence": 15 if evidence_current else 0,
                "comparable_sales": 15 if sales_current else 0,
                "underwriting": 20 if underwriting_current else 0,
                "financial_plan": 15 if plan_current else 0,
                "buyer_demand": 10 if current_buyers else 0,
                "precontract_reviews": 5 if not reviews else 0,
            }
            score = sum(readiness.values())
            decision = "outside_buy_box" if outside else "blocked" if blockers else "research" if gaps else "owner_review"
            reasons = outside + blockers + gaps
            items.append({"deal_id": deal["id"], "property_id": prop["id"], "address": prop["address"],
                          "market": location, "strategy": deal["strategy"], "stage": deal["stage"],
                          "decision": decision, "reasons": reasons,
                          "opportunity_score": score, "score_breakdown": readiness,
                          "score_type": "evidence-and-readiness score; not a probability of profit or closing",
                          "next_action": reasons[0] if reasons else "Owner review of terms and evidence; no execution authorized",
                          "current_fact_ids": [f["id"] for f in evidence["facts"]],
                          "conflicts": evidence["conflicts"], "buyer_candidates": buyer_candidates,
                          "current_criteria_fit_buyers": len(current_buyers),
                          "economics": plan["forecasts"] if plan else None,
                          "estimated_cash_at_risk": finance["projected_cash_at_risk"],
                          "underwriting_id": uw["id"] if uw else None, "policy_id": policy["id"] if policy else None})
        order = {"owner_review": 0, "research": 1, "blocked": 2, "outside_buy_box": 3}
        items.sort(key=lambda item: (order[item["decision"]], -item["opportunity_score"],
                                     -item["current_criteria_fit_buyers"], item["address"], item["deal_id"]))
        return {"policy": policy, "items": items, "estimated_portfolio_cash_at_risk": dollars(exposure),
                "unknown_exposure_deal_ids": unknown_exposure, "execution_authorized": False,
                "scope": "Entered pre-contract deals only; no discovery, valuation, spending, sending or offers"}


def cents_signed(value):
    return -cents(-value) if value < 0 else cents(value)
