"""Link existing fixed-price calculations to expiring evidence reviews."""
import hashlib
import json
from uuid import uuid4

from core.memory.models import utc_now
from .comparables import comparable_screen
from .opportunities import canonical, property_evidence, recent
from .money import cents, dollars
from .sourcing import sale_snapshot
from .validation import text_field


def economic_screen(connection, deal, policy, portfolio_deals):
    prop = dict(connection.execute("SELECT * FROM properties WHERE id=?", (deal["property_id"],)).fetchone())
    evidence = property_evidence(connection, prop["id"])
    sales = sale_snapshot(connection, prop["id"])
    max_age = policy["evidence_max_age_days"] if policy else 30
    comps = comparable_screen(prop, evidence, sales, evidence_max_age_days=max_age)
    uw, finance = deal["underwriting"], deal["finance"]
    plan = finance["plan"]
    gaps = []
    if not policy:
        gaps.append("Save a buy box and downside-risk policy")
    if not uw or not plan:
        gaps.append("Save current underwriting and fixed seller terms")
    if evidence["conflicts"] or sales["conflicts"]:
        gaps.append("Resolve conflicting property or comparable evidence")
    if any(not recent(f["observed_at"], max_age, utc_now()) for f in evidence["facts"]):
        gaps.append("Refresh stale or future-dated subject evidence")
    fit_ids = [c["sale_id"] for c in comps["items"] if c["decision"] == "screening_fit"]
    if not fit_ids:
        gaps.append("Review at least one comparable that fits the recorded screening limits")
    if any(c["decision"] != "screening_fit" for c in comps["items"]):
        gaps.append("Correct or withdraw accepted comparables that do not fit the screen")
    if uw:
        if (uw["result"].get("evidence_digest") != evidence["digest"] or
                uw["result"].get("sale_evidence", {}).get("digest") != sales["digest"]):
            gaps.append("Resave underwriting after evidence changes")
        if not recent(uw["created_at"], max_age, utc_now()):
            gaps.append("Refresh stale underwriting")
    if plan and not plan["current_underwriting"]:
        gaps.append("Resave seller terms against the current underwriting")
    portfolio = [{"deal_id": d["id"], "stage": d["stage"],
                  "plan_id": d["finance"]["plan"]["id"] if d["finance"]["plan"] else None,
                  "cash_exposure": d["finance"]["summary"]["unrecovered_cash"] if d["stage"] in {"completed", "lost"} or not d["finance"]["plan"] else d["finance"]["projected_cash_at_risk"],
                  "ledger_digest": d["finance"]["summary"]["digest"]} for d in portfolio_deals]
    portfolio.sort(key=lambda d: d["deal_id"])
    exposure = dollars(sum(cents(d["cash_exposure"]) for d in portfolio))
    if any(d["stage"] not in {"completed", "lost"} and d["plan_id"] is None for d in portfolio):
        gaps.append("Complete missing portfolio cash-exposure plans")
    context = {"underwriting_id": uw["id"] if uw else None, "plan_id": plan["id"] if plan else None,
               "policy_id": policy["id"] if policy else None, "property_digest": evidence["digest"],
               "sale_digest": sales["digest"], "fit_sale_ids": sorted(fit_ids),
               "portfolio": portfolio, "screen_version": "evidence-economics-v1"}
    digest = hashlib.sha256(json.dumps(context, sort_keys=True).encode()).hexdigest()
    row = connection.execute("SELECT body FROM economic_reviews WHERE deal_id=? ORDER BY rowid DESC LIMIT 1", (deal["id"],)).fetchone()
    review = json.loads(row["body"]) if row else None
    current = bool(review and review["context_digest"] == digest and not gaps and
                   recent(review["created_at"], max_age, utc_now()))
    failures = list(finance["contract_blockers"]) if plan else []
    if plan and policy and plan["forecasts"]["downside"]["net_contribution"] < policy["min_downside_net"]:
        failures.append("Downside contribution is below the saved policy minimum")
    if plan and not plan["forecasts"]["downside"]["supports_entered_terms"]:
        failures.append("Downside scenario does not support the entered seller price and fee")
    if policy:
        market = " ".join(f"{prop['city']}, {prop['state']}".lower().split())
        if market not in policy["markets"] or deal["strategy"] not in policy["strategies"]:
            failures.append("Market or strategy is outside the saved buy box")
        property_type = evidence["values"].get("property_type")
        if not property_type:
            gaps.append("Confirm current subject property type")
        elif canonical(property_type) not in policy["property_types"]:
            failures.append("Property type is outside the saved buy box")
        if plan and plan["seller_price"] > policy["max_seller_price"]:
            failures.append("Seller price exceeds the saved buy-box limit")
        if finance["projected_cash_at_risk"] is not None and finance["projected_cash_at_risk"] > policy["max_deal_cash_at_risk"]:
            failures.append("Deal cash exposure exceeds the saved policy limit")
    if policy and exposure > policy["max_portfolio_cash_at_risk"]:
        failures.append("Portfolio cash exposure exceeds the saved policy limit")
    current = current and not gaps and not failures
    status = "incomplete" if gaps else "fails_economic_limits" if failures else "owner_reviewed" if current else "evidence_review_required"
    inputs = uw["inputs"] if uw else {}
    breakdown = {k: inputs[k] for k in ("expected_exit_price", "buyer_repairs", "buyer_funding_holding",
        "buyer_closing", "buyer_selling_costs", "buyer_minimum_profit", "owner_transaction_costs",
        "partner_payout_allowance", "contingency", "desired_owner_net") if k in inputs}
    return {"status": status, "gaps": gaps, "failed_limits": failures,
            "context_digest": digest, "context": context, "comparable_screen": comps,
            "fit_comparable_count": len(fit_ids), "assumptions": breakdown,
            "estimated_portfolio_cash_at_risk": exposure,
            "forecasts": plan["forecasts"] if plan else None,
            "review": review, "review_current": current, "review_allowed": not gaps and not failures,
            "execution_authorized": False,
            "scope": "Manual economics and owner-reviewed evidence; no independent valuation, buyer commitment or profit guarantee"}


class EconomicReviewsMixin:
    def review_economics(self, deal_id, data):
        reviewer = text_field(data, "reviewer", 120)
        references = {key: text_field(data, key, 1000) for key in (
            "exit_price_reference", "repair_reference", "funding_cost_reference",
            "closing_selling_reference", "owner_cost_partner_reference", "condition_concessions_reference", "note")}
        if data.get("owner_confirmed_assumptions") is not True:
            raise ValueError("Confirm all assumptions, including zero costs, partner payouts and contingencies")
        submitted_digest = text_field(data, "context_digest", 64)
        with self.database.session(write=True) as (connection, _):
            deals = self._list_deals(connection)
            deal = next((d for d in deals if d["id"] == deal_id), None)
            if deal is None:
                raise LookupError("Deal not found")
            if deal["stage"] in {"contracted", "disposition", "closing", "completed", "lost"}:
                raise ValueError("Economic review is for pre-contract deals only")
            policy_row = connection.execute("SELECT body FROM opportunity_policies ORDER BY rowid DESC LIMIT 1").fetchone()
            policy = json.loads(policy_row["body"]) if policy_row else None
            screen = economic_screen(connection, deal, policy, deals)
            if screen["context_digest"] != submitted_digest:
                raise ValueError("Economic context changed; reload and review the current evidence")
            if not screen["review_allowed"]:
                raise ValueError("Resolve economic review gaps and failed limits first")
            prior = screen["review"]
            if screen["review_current"] and prior["reviewer"] == reviewer and all(prior[k] == v for k, v in references.items()):
                return prior
            review = {"id": str(uuid4()), "deal_id": deal_id, "reviewer": reviewer, **references,
                      "context_digest": screen["context_digest"], "context": screen["context"],
                      "owner_confirmed_assumptions": True, "created_at": utc_now().isoformat()}
            connection.execute("INSERT INTO economic_reviews(id,deal_id,body) VALUES(?,?,?)",
                               (review["id"], deal_id, json.dumps(review)))
        return review
