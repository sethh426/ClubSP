"""Integer-cent cash accounting and fixed-price scenario comparisons."""
from decimal import Decimal, InvalidOperation
import hashlib
import json


KINDS = {
    "expense": {"purchase", "repairs", "funding_holding", "closing", "selling", "transaction", "partner_payout", "other"},
    "income": {"assignment_fee", "resale_proceeds", "other_receipt"},
    "escrow_deposit": {"earnest_money"},
    "escrow_return": {"earnest_money"},
    "escrow_applied": {"purchase"},
    "escrow_forfeit": {"earnest_money_loss"},
}


def cents(value, name="amount", positive=False):
    if isinstance(value, bool) or not isinstance(value, (str, int, float)):
        raise ValueError(f"{name} must be a dollar amount")
    try:
        amount = Decimal(str(value))
        if not amount.is_finite() or amount < 0 or amount > 1_000_000_000:
            raise ValueError(f"{name} must be between 0 and 1 billion dollars")
        if amount != amount.quantize(Decimal("0.01")):
            raise ValueError(f"{name} cannot have more than two decimal places")
        if positive and amount <= 0:
            raise ValueError(f"{name} must be greater than zero")
        return int(amount * 100)
    except InvalidOperation as exc:
        raise ValueError(f"{name} must be a valid dollar amount") from exc


def dollars(value):
    return value / 100


def ledger_summary(entries):
    reversed_ids = {e["reversal_of"] for e in entries if e["reversal_of"]}
    active = [e for e in entries if not e["reversal_of"] and e["id"] not in reversed_ids]
    active.sort(key=lambda e: (e["occurred_on"], e["created_at"], e["id"]))
    expenses = income = deposited = returned = applied = forfeited = held = 0
    by_category = {}
    for e in active:
        kind, amount = e["kind"], e["amount_cents"]
        if kind == "expense":
            expenses += amount
            by_category[e["category"]] = by_category.get(e["category"], 0) + amount
        elif kind == "income":
            income += amount
        elif kind == "escrow_deposit":
            deposited += amount
            held += amount
        elif kind == "escrow_return":
            returned += amount
            held -= amount
        elif kind == "escrow_applied":
            applied += amount
            held -= amount
            by_category["purchase"] = by_category.get("purchase", 0) + amount
        elif kind == "escrow_forfeit":
            forfeited += amount
            held -= amount
            by_category["earnest_money_loss"] = by_category.get("earnest_money_loss", 0) + amount
        if held < 0:
            raise ValueError("Escrow return, application or forfeiture exceeds the deposit held on that date")
    net = income - expenses - applied - forfeited
    cash_net = income + returned - expenses - deposited
    digest = hashlib.sha256(json.dumps(
        [(e["id"], e["amount_cents"]) for e in active], separators=(",", ":")
    ).encode()).hexdigest()
    return {
        "income": dollars(income), "paid_expenses": dollars(expenses),
        "escrow_held": dollars(held), "escrow_applied": dollars(applied),
        "escrow_forfeited": dollars(forfeited), "net_contribution": dollars(net),
        "cash_net": dollars(cash_net), "unrecovered_cash": dollars(max(-cash_net, 0)),
        "costs_by_category": {k: dollars(v) for k, v in by_category.items()},
        "active_entry_ids": [e["id"] for e in active], "digest": digest,
    }


def fixed_price_forecast(strategy, inputs, plan):
    """Hold the selected seller price fixed instead of repricing to each ceiling."""
    price, fee = plan["seller_price_cents"], plan["assignment_fee_cents"]
    reserves = sum(cents(inputs[k]) for k in (
        "owner_transaction_costs", "partner_payout_allowance", "contingency"
    ))
    costs = sum(cents(inputs[k]) for k in (
        "buyer_repairs", "buyer_funding_holding", "buyer_closing", "buyer_selling_costs"
    ))
    exit_price = cents(inputs["expected_exit_price"])
    minimum = cents(inputs["buyer_minimum_profit"])
    result = {}
    for name, exit_factor, cost_factor in (
        ("downside", Decimal("0.90"), Decimal("1.25")),
        ("base", Decimal("1"), Decimal("1")),
        ("upside", Decimal("1.05"), Decimal("1")),
    ):
        projected_exit = int((exit_price * exit_factor).quantize(Decimal("1")))
        projected_costs = int((costs * cost_factor).quantize(Decimal("1")))
        if strategy == "assignment":
            buyer_ceiling = projected_exit - projected_costs - minimum
            fee_supported = max(0, min(fee, buyer_ceiling - price))
            net = fee_supported - reserves
            supports_terms = buyer_ceiling >= price + fee
        else:
            net = projected_exit - projected_costs - reserves - price
            supports_terms = net >= cents(inputs["desired_owner_net"])
        result[name] = {
            "net_contribution": dollars(net), "supports_entered_terms": supports_terms,
            "seller_price": dollars(price),
        }
    return result
