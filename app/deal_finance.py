from datetime import date
import json
from uuid import UUID, uuid4

from core.memory.models import utc_now
from .money import KINDS, cents, dollars, fixed_price_forecast, ledger_summary
from .operations import business_today
from .validation import deal_exists, text_field


class FinanceMixin:
    def _finance(self, connection, deal):
        deal_id = str(deal["id"])
        entries = [dict(row) for row in connection.execute(
            "SELECT * FROM ledger_entries WHERE deal_id=? ORDER BY occurred_on,created_at,id", (deal_id,)
        )]
        summary = ledger_summary(entries)
        plan = connection.execute(
            "SELECT * FROM financial_plans WHERE deal_id=? ORDER BY created_at DESC,id LIMIT 1", (deal_id,)
        ).fetchone()
        latest_uw = connection.execute(
            "SELECT id FROM underwritings WHERE deal_id=? ORDER BY created_at DESC,id LIMIT 1", (deal_id,)
        ).fetchone()
        blockers = []
        if plan:
            plan = dict(plan)
            plan["forecasts"] = json.loads(plan.pop("forecasts_json"))
            for name in ("seller_price", "assignment_fee", "planned_cash_at_risk", "max_cash_at_risk"):
                plan[name] = dollars(plan[name + "_cents"])
            plan["current_underwriting"] = latest_uw is not None and plan["underwriting_id"] == latest_uw["id"]
            if not plan["current_underwriting"]:
                blockers.append("Financial plan must reference the latest underwriting")
            if plan["seller_price_cents"] > plan["offer_ceiling_cents"]:
                blockers.append("Seller price exceeds the base underwriting ceiling")
            if not plan["forecasts"]["base"]["supports_entered_terms"]:
                blockers.append("Base scenario cannot support the entered terms")
            if int(round(plan["forecasts"]["base"]["net_contribution"] * 100)) < plan["desired_net_cents"]:
                blockers.append("Forecast contribution is below the desired owner net")
            projected = max(summary["unrecovered_cash"], plan["planned_cash_at_risk"])
            if projected > plan["max_cash_at_risk"]:
                blockers.append("Owner cash exposure exceeds the chosen limit")
        else:
            projected = None
            blockers.append("Record proposed terms and a cash-at-risk plan")
        reconciliation = connection.execute(
            "SELECT * FROM reconciliations WHERE deal_id=? ORDER BY created_at DESC,id LIMIT 1", (deal_id,)
        ).fetchone()
        if reconciliation:
            reconciliation = dict(reconciliation)
            reconciliation["current"] = (
                reconciliation["ledger_digest"] == summary["digest"]
                and reconciliation["plan_id"] == (plan["id"] if plan else None)
            )
            for field in ("actual_net", "forecast_net", "variance"):
                value = reconciliation[field + "_cents"]
                reconciliation[field] = None if value is None else dollars(value)
        return {
            "entries": entries, "summary": summary, "plan": plan,
            "projected_cash_at_risk": projected, "contract_blockers": blockers,
            "reconciliation": reconciliation,
        }

    def save_financial_plan(self, deal_id, data):
        values = {name: cents(data.get(name), name) for name in (
            "seller_price", "assignment_fee", "planned_cash_at_risk", "max_cash_at_risk"
        )}
        note = text_field(data, "basis", 2000)
        with self.database.session(write=True) as (connection, _):
            deal = deal_exists(connection, deal_id)
            if deal["stage"] in {"completed", "lost"}:
                raise ValueError("Financial plans cannot be changed after a deal ends")
            uw = connection.execute(
                "SELECT * FROM underwritings WHERE deal_id=? ORDER BY created_at DESC,id LIMIT 1", (str(deal["id"]),)
            ).fetchone()
            if uw is None:
                raise ValueError("Record an underwriting before proposed terms")
            inputs, result = json.loads(uw["inputs_json"]), json.loads(uw["result_json"])
            if deal["strategy"] == "resale" and values["assignment_fee"] != 0:
                raise ValueError("A resale plan must use a zero assignment fee")
            record = {"id": str(uuid4()), "deal_id": str(deal["id"]), "underwriting_id": uw["id"],
                      **{k + "_cents": v for k, v in values.items()},
                      "offer_ceiling_cents": int(round(result["scenarios"]["base"]["owner_max_contract_price"] * 100)),
                      "desired_net_cents": cents(inputs["desired_owner_net"]),
                      "basis": note, "created_at": utc_now().isoformat()}
            record["forecasts_json"] = json.dumps(fixed_price_forecast(deal["strategy"], inputs, record))
            columns = ",".join(record)
            connection.execute("INSERT INTO financial_plans(" + columns + ") VALUES(" + ",".join("?" for _ in record) + ")", tuple(record.values()))
            self._complete_system_task(connection, str(deal["id"]), "money_plan", "Financial plan recorded", record["id"])
            finance = self._finance(connection, deal)
        return finance

    def record_ledger_entry(self, deal_id, data):
        try:
            key = str(UUID(text_field(data, "entry_key", 36)))
        except ValueError as exc:
            raise ValueError("entry_key must be a UUID for safe retries") from exc
        kind = text_field(data, "kind", 30)
        category = text_field(data, "category", 40)
        if kind not in KINDS or category not in KINDS[kind]:
            raise ValueError("Unsupported cash movement or category")
        amount = cents(data.get("amount"), positive=True)
        evidence = text_field(data, "evidence_reference", 500)
        note = text_field(data, "note", 1000)
        occurred_on = text_field(data, "occurred_on", 10)
        try:
            day = date.fromisoformat(occurred_on)
            if day.isoformat() != occurred_on:
                raise ValueError("Noncanonical date")
        except ValueError as exc:
            raise ValueError("occurred_on must be YYYY-MM-DD") from exc
        if day > business_today():
            raise ValueError("Actual cash movements cannot have a future date")
        reversal_of = data.get("reversal_of") or None
        if reversal_of:
            try:
                reversal_of = str(UUID(str(reversal_of)))
            except ValueError as exc:
                raise ValueError("reversal_of must identify a ledger entry") from exc
        with self.database.session(write=True) as (connection, _):
            deal = deal_exists(connection, deal_id)
            existing = connection.execute("SELECT * FROM ledger_entries WHERE entry_key=?", (key,)).fetchone()
            fields = {"deal_id": str(deal["id"]), "entry_key": key, "kind": kind, "category": category,
                      "amount_cents": amount, "occurred_on": occurred_on, "note": note,
                      "evidence_reference": evidence, "reversal_of": reversal_of}
            if existing:
                if any(existing[name] != value for name, value in fields.items()):
                    raise ValueError("entry_key was already used for different ledger data")
                return dict(existing)
            if reversal_of:
                original = connection.execute("SELECT * FROM ledger_entries WHERE id=? AND deal_id=?", (reversal_of, str(deal["id"]))).fetchone()
                if not original or original["reversal_of"]:
                    raise ValueError("Only an original entry on this deal can be reversed")
                if connection.execute("SELECT id FROM ledger_entries WHERE reversal_of=?", (reversal_of,)).fetchone():
                    raise ValueError("Ledger entry was already reversed")
                if any(original[k] != fields[k] for k in ("kind", "category", "amount_cents")):
                    raise ValueError("Reversal must have the same kind, category and amount")
            entry = {"id": str(uuid4()), **fields, "created_at": utc_now().isoformat()}
            previous = [dict(r) for r in connection.execute("SELECT * FROM ledger_entries WHERE deal_id=?", (str(deal["id"]),))]
            ledger_summary(previous + [entry])
            columns = ",".join(entry)
            connection.execute("INSERT INTO ledger_entries(" + columns + ") VALUES(" + ",".join("?" for _ in entry) + ")", tuple(entry.values()))
            self._reopen_system_task(connection, str(deal["id"]), "reconcile", "Ledger changed after reconciliation")
        return entry

    def reconcile_deal(self, deal_id, data):
        if data.get("owner_confirmed_complete") is not True:
            raise ValueError("Owner must confirm that all costs, receipts and escrow dispositions are recorded")
        evidence = text_field(data, "evidence_reference", 500)
        note = text_field(data, "note", 1000)
        with self.database.session(write=True) as (connection, _):
            deal = deal_exists(connection, deal_id)
            if deal["stage"] not in {"completed", "lost"}:
                raise ValueError("Only a completed or lost deal can be reconciled")
            finance = self._finance(connection, deal)
            if finance["summary"]["escrow_held"] != 0:
                raise ValueError("Resolve escrow before reconciliation")
            if deal["stage"] == "completed" and finance["summary"]["income"] <= 0:
                raise ValueError("Record money actually received before reconciling a completed deal")
            if finance["reconciliation"] and finance["reconciliation"]["current"]:
                return finance["reconciliation"]
            plan = finance["plan"]
            forecast = int(round(plan["forecasts"]["base"]["net_contribution"] * 100)) if plan else None
            actual = int(round(finance["summary"]["net_contribution"] * 100))
            row = {"id": str(uuid4()), "deal_id": str(deal["id"]), "plan_id": plan["id"] if plan else None,
                   "ledger_digest": finance["summary"]["digest"], "actual_net_cents": actual,
                   "forecast_net_cents": forecast, "variance_cents": actual - forecast if forecast is not None else None,
                   "evidence_reference": evidence, "note": note, "created_at": utc_now().isoformat()}
            columns = ",".join(row)
            connection.execute("INSERT INTO reconciliations(" + columns + ") VALUES(" + ",".join("?" for _ in row) + ")", tuple(row.values()))
            self._complete_system_task(connection, str(deal["id"]), "reconcile", "Profit reconciled by owner", row["id"])
            return self._finance(connection, deal)["reconciliation"]
