from __future__ import annotations

from dataclasses import asdict
from datetime import datetime, timezone
import json
from math import isfinite
from uuid import UUID, uuid4

from core.memory import Fact, LearningEngine, Observation, Prediction, SourceRecord
from core.memory.models import utc_now
from .database import COLLECTIONS, Database
from .validation import text_field, number_field, list_field, property_exists, deal_exists
from .deal_finance import FinanceMixin
from .operations import OperationsMixin, OPERATIONS, business_today
from .money import cents
from .research import ResearchMixin
from .providers import PROVIDER
from .communications import CommunicationsMixin
from .training import TrainingMixin
from .knowledge import KnowledgeMixin
from .opportunities import OpportunitiesMixin, property_evidence, canonical
from .sourcing import SourcingMixin, sale_snapshot


DEAL_STAGES = (
    "research", "contacting", "qualified", "underwriting",
    "offer_decision", "contracted", "disposition", "closing", "completed", "lost",
)
STAGE_NEXT = {
    "research": {"contacting", "qualified", "underwriting", "lost"},
    "contacting": {"qualified", "underwriting", "lost"},
    "qualified": {"underwriting", "lost"},
    "underwriting": {"contacting", "offer_decision", "lost"},
    "offer_decision": {"underwriting", "contracted", "lost"},
    "contracted": {"disposition", "closing", "lost"},
    "disposition": {"closing", "lost"},
    "closing": {"completed", "disposition", "lost"},
    "completed": set(), "lost": set(),
}
STRATEGIES = {"assignment", "resale"}
UNDERWRITING_FIELDS = (
    "expected_exit_price", "buyer_repairs", "buyer_funding_holding",
    "buyer_closing", "buyer_selling_costs", "buyer_minimum_profit",
    "target_assignment_fee", "owner_transaction_costs", "partner_payout_allowance",
    "contingency", "desired_owner_net",
)


def money(value):
    return round(float(value), 2)


def calculate_scenario(strategy, values):
    exit_price = values["expected_exit_price"]
    costs = (
        values["buyer_repairs"] + values["buyer_funding_holding"]
        + values["buyer_closing"] + values["buyer_selling_costs"]
    )
    buyer_ceiling = exit_price - costs - values["buyer_minimum_profit"]
    reserves = (
        values["owner_transaction_costs"] + values["partner_payout_allowance"]
        + values["contingency"]
    )
    if strategy == "assignment":
        required_fee = max(values["target_assignment_fee"], values["desired_owner_net"] + reserves)
        owner_price_ceiling = buyer_ceiling - required_fee
        fee = buyer_ceiling - owner_price_ceiling
        planned_net = fee - reserves
        return {
            "buyer_acquisition_ceiling": money(buyer_ceiling),
            "owner_max_contract_price": money(owner_price_ceiling),
            "target_assignment_fee": money(fee),
            "buyer_all_in_at_owner_ceiling": money(owner_price_ceiling + fee),
            "planned_owner_net": money(planned_net),
            "buyer_margin_after_costs_and_minimum": money(exit_price - costs - (owner_price_ceiling + fee)),
            "profitable": buyer_ceiling > 0 and owner_price_ceiling > 0 and planned_net >= values["desired_owner_net"],
        }
    owner_price_ceiling = exit_price - costs - reserves - values["desired_owner_net"]
    planned_net = exit_price - costs - reserves - owner_price_ceiling
    return {
        "buyer_acquisition_ceiling": None,
        "owner_max_contract_price": money(owner_price_ceiling),
        "target_assignment_fee": None,
        "buyer_all_in_at_owner_ceiling": None,
        "planned_owner_net": money(planned_net),
        "buyer_margin_after_costs_and_minimum": None,
        "profitable": owner_price_ceiling > 0 and planned_net >= values["desired_owner_net"],
    }


class Application(FinanceMixin, OperationsMixin, ResearchMixin, CommunicationsMixin, TrainingMixin, KnowledgeMixin, OpportunitiesMixin, SourcingMixin):
    def __init__(self, path):
        self.database = Database(path)
        with self.database.session(write=True) as (connection, _):
            self._recover_knowledge_runs(connection)
            for deal in connection.execute("SELECT id FROM deals").fetchall():
                self._seed_tasks(connection, deal["id"])
                uw = connection.execute("SELECT id FROM underwritings WHERE deal_id=? ORDER BY created_at DESC LIMIT 1", (deal["id"],)).fetchone()
                if uw:
                    self._complete_system_task(connection, deal["id"], "underwriting", "Existing underwriting found", uw["id"])

    def state(self):
        with self.database.session() as (connection, memory):
            result = {
                "properties": [dict(row) for row in connection.execute(
                    "SELECT * FROM properties ORDER BY created_at DESC, id"
                )],
                "mode": "local",
            }
            result.update({
                name: [asdict(record) for record in getattr(memory, name).values()]
                for name in COLLECTIONS
            })
            result["deals"] = self._list_deals(connection)
            result["opportunities"] = self._opportunity_state(connection, result["deals"])
            result["sourcing"] = self._sourcing_state(connection)
            result["today"] = business_today().isoformat()
            result["research"] = self._research_snapshots(connection)
            result["providers"] = [PROVIDER]
            result["communications"] = self._communications_state(connection)
            result["training"] = self._training_state(connection)
            result["knowledge"] = self._knowledge_state(connection)
            result["operations"] = [{"number": i + 1, "name": op[0], "expectation": op[1], "capability": op[2]} for i, op in enumerate(OPERATIONS)]
            result["scorecard"] = {
                "reconciled_net_contribution": money(sum(
                    d["finance"]["reconciliation"]["actual_net"] for d in result["deals"]
                    if d["finance"]["reconciliation"] and d["finance"]["reconciliation"]["current"]
                )),
                "unrecovered_cash": money(sum(d["finance"]["summary"]["unrecovered_cash"] for d in result["deals"])),
                "unreconciled_ended_deals": sum(
                    d["stage"] in {"completed", "lost"} and not (
                        d["finance"]["reconciliation"] and d["finance"]["reconciliation"]["current"]
                    ) for d in result["deals"]
                ),
                "open_exceptions": sum(t["status"] == "open" and t["kind"] == "exception" for d in result["deals"] for t in d["tasks"]),
                "overdue_tasks": sum(t["overdue"] for d in result["deals"] for t in d["tasks"]),
            }
            result["buyers"] = [
                self._buyer_json(row)
                for row in connection.execute("SELECT * FROM buyers ORDER BY created_at DESC,id")
            ]
            return result

    @staticmethod
    def _buyer_json(row):
        buyer = dict(row)
        buyer["locations"] = json.loads(buyer.pop("locations_json"))
        buyer["strategies"] = json.loads(buyer.pop("strategies_json"))
        buyer["property_types"] = json.loads(buyer.pop("property_types_json"))
        return buyer

    def _list_deals(self, connection, property_id=None):
        query = (
            "SELECT d.*, p.address, p.city, p.state, p.zip "
            "FROM deals d JOIN properties p ON p.id=d.property_id"
        )
        parameters = ()
        if property_id is not None:
            query += " WHERE d.property_id=?"
            parameters = (str(property_id),)
        query += " ORDER BY d.updated_at DESC,d.id"
        deals = []
        for row in connection.execute(query, parameters):
            item = dict(row)
            uw = connection.execute(
                "SELECT id,inputs_json,result_json,created_at FROM underwritings "
                "WHERE deal_id=? ORDER BY created_at DESC,id LIMIT 1", (item["id"],)
            ).fetchone()
            item["underwriting"] = ({
                "id": uw["id"], "inputs": json.loads(uw["inputs_json"]),
                "result": json.loads(uw["result_json"]), "created_at": uw["created_at"],
            } if uw else None)
            run = connection.execute(
                "SELECT id,matches_json,created_at FROM buyer_match_runs "
                "WHERE deal_id=? ORDER BY created_at DESC,id LIMIT 1", (item["id"],)
            ).fetchone()
            item["buyer_matches"] = ({
                "id": run["id"], "matches": json.loads(run["matches_json"]),
                "created_at": run["created_at"],
            } if run else None)
            item["events"] = [dict(event) for event in connection.execute(
                "SELECT stage_before,stage_after,note,evidence_reference,created_at "
                "FROM deal_events WHERE deal_id=? ORDER BY created_at,id", (item["id"],)
            )]
            item["finance"] = self._finance(connection, item)
            item["tasks"] = self._tasks(connection, item["id"])
            deals.append(item)
        return deals

    def create_property(self, data):
        record = {
            "id": str(uuid4()),
            "address": text_field(data, "address"),
            "city": text_field(data, "city", 120),
            "state": text_field(data, "state", 80),
            "zip": text_field(data, "zip", 20, required=False),
            "created_at": utc_now().isoformat(),
        }
        with self.database.session(write=True) as (connection, _):
            connection.execute(
                "INSERT INTO properties(id,address,city,state,zip,created_at) VALUES(?,?,?,?,?,?)",
                tuple(record.values()),
            )
        return record

    def record_fact(self, data):
        attribute = text_field(data, "attribute", 80)
        value = data.get("value")
        if not isinstance(value, (str, int, float, bool)) or (
            isinstance(value, (int, float)) and not isinstance(value, bool) and not isfinite(value)
        ):
            raise ValueError("value must be text, a finite number, or a boolean")
        if isinstance(value, str) and len(value) > 4000:
            raise ValueError("value is too long")
        confidence = number_field(data, "confidence", 0.5)
        provider = text_field(data, "provider", 120)
        url = text_field(data, "url", 2000, required=False)
        if url and not url.startswith(("https://", "http://")):
            raise ValueError("Source URL must start with http:// or https://")
        with self.database.session(write=True) as (connection, memory):
            property_id = property_exists(connection, data.get("property_id"))
            source = memory.add_source(SourceRecord(source_type="manual", provider=provider, url=url or None))
            supersedes = data.get("supersedes_fact_id")
            fact = memory.add_fact(Fact(
                subject_type="property", subject_id=property_id, attribute=attribute,
                value=value, value_type=("boolean" if isinstance(value, bool) else
                    "number" if isinstance(value, (int, float)) else "text"),
                source_id=source.id, confidence=confidence,
                supersedes_fact_id=UUID(supersedes) if supersedes else None,
            ))
        return {"source": asdict(source), "fact": asdict(fact)}

    def predict(self, data):
        prediction_type = text_field(data, "prediction_type", 80)
        predicted_value = number_field(data, "predicted_value", nonnegative=True)
        confidence = number_field(data, "confidence", 0.5)
        with self.database.session(write=True) as (connection, memory):
            property_id = property_exists(connection, data.get("property_id"))
            prediction = memory.add_prediction(Prediction(
                subject_type="property", subject_id=property_id,
                prediction_type=prediction_type, predicted_value=predicted_value,
                confidence=confidence, model_version="manual-v1",
                feature_snapshot={"fact_ids": [str(f.id) for f in memory.facts_for("property", property_id)]},
            ))
        return asdict(prediction)

    def resolve(self, prediction_id, data):
        try:
            prediction_id = UUID(prediction_id)
        except (ValueError, TypeError, AttributeError) as exc:
            raise ValueError("prediction_id must be a valid UUID") from exc
        actual = number_field(data, "actual_value", nonnegative=True)
        confidence = number_field(data, "confidence", 1.0)
        provider = text_field(data, "provider", 120)
        with self.database.session(write=True) as (_, memory):
            prediction = memory.predictions.get(prediction_id)
            if prediction is None:
                raise LookupError("Prediction not found")
            source = memory.add_source(SourceRecord(source_type="manual", provider=provider))
            observation = Observation(
                subject_type=prediction.subject_type, subject_id=prediction.subject_id,
                observation_type=prediction.prediction_type + "_actual",
                actual_value=actual, source_id=source.id, confidence=confidence,
            )
            resolved, learning = LearningEngine(memory).resolve_prediction(prediction_id, observation)
        return {"prediction": asdict(resolved), "observation": asdict(observation), "learning": asdict(learning)}

    def create_deal(self, data):
        strategy = text_field(data, "strategy", 20)
        if strategy not in STRATEGIES:
            raise ValueError("strategy must be assignment or resale")
        now = utc_now().isoformat()
        deal_id = uuid4()
        with self.database.session(write=True) as (connection, _):
            property_id = property_exists(connection, data.get("property_id"))
            connection.execute(
                "INSERT INTO deals(id,property_id,strategy,stage,created_at,updated_at) VALUES(?,?,?,?,?,?)",
                (str(deal_id), str(property_id), strategy, "research", now, now),
            )
            self._seed_tasks(connection, str(deal_id))
        return {"id": str(deal_id), "property_id": str(property_id), "strategy": strategy, "stage": "research", "created_at": now, "updated_at": now}

    def advance_deal(self, deal_id, data):
        target = text_field(data, "stage", 30)
        note = text_field(data, "note", 1000)
        evidence = text_field(data, "evidence_reference", 500, required=False)
        if target not in DEAL_STAGES:
            raise ValueError("Unknown deal stage")
        now = utc_now().isoformat()
        with self.database.session(write=True) as (connection, _):
            deal = deal_exists(connection, deal_id)
            current = deal["stage"]
            if target not in STAGE_NEXT[current]:
                raise ValueError(f"Cannot move a {current} deal to {target}")
            if target == "contracted":
                underwriting = connection.execute(
                    "SELECT result_json FROM underwritings WHERE deal_id=? ORDER BY created_at DESC,id LIMIT 1",
                    (str(deal["id"]),),
                ).fetchone()
                if underwriting is None or not json.loads(underwriting["result_json"])["scenarios"]["base"]["profitable"]:
                    raise ValueError("A profitable base underwriting is required before a deal can be contracted")
                saved = json.loads(underwriting["result_json"])
                if saved.get("sale_evidence", {}).get("digest") != sale_snapshot(connection, deal["property_id"])["digest"]:
                    raise ValueError("Review and resave underwriting after comparable-sale evidence changes")
                if data.get("owner_confirmed_signed") is not True or not evidence:
                    raise ValueError("Moving to contracted requires owner confirmation and a signed-agreement reference")
                blockers = self._finance(connection, deal)["contract_blockers"]
                if blockers:
                    raise ValueError("Contract stage blocked: " + "; ".join(blockers))
            if target == "completed" and (data.get("owner_confirmed_closed") is not True or not evidence):
                raise ValueError("Completing a deal requires owner confirmation and closing evidence")
            task_blockers = self._task_blockers(connection, str(deal["id"]), target)
            if task_blockers:
                raise ValueError("Resolve operation tasks first: " + "; ".join(task_blockers))
            connection.execute(
                "UPDATE deals SET stage=?,updated_at=? WHERE id=?", (target, now, str(deal["id"]))
            )
            connection.execute(
                "INSERT INTO deal_events(id,deal_id,stage_before,stage_after,note,evidence_reference,created_at) "
                "VALUES(?,?,?,?,?,?,?)",
                (str(uuid4()), str(deal["id"]), current, target, note, evidence, now),
            )
        return {"id": str(deal["id"]), "stage_before": current, "stage": target, "updated_at": now}

    def underwrite(self, deal_id, data):
        notes = text_field(data, "basis", 2000)
        values = {name: number_field(data, name, nonnegative=True) for name in UNDERWRITING_FIELDS}
        for name, value in values.items():
            cents(value, name)
        property_type = text_field(data, "property_type", 60).lower().replace(" ", "_")
        if values["expected_exit_price"] <= 0:
            raise ValueError("expected_exit_price must be greater than zero")
        if values["desired_owner_net"] <= 0:
            raise ValueError("desired_owner_net must be greater than zero")
        now = utc_now().isoformat()
        with self.database.session(write=True) as (connection, _):
            deal = deal_exists(connection, deal_id)
            if deal["stage"] in {"contracted", "disposition", "closing", "completed", "lost"}:
                raise ValueError("Underwriting cannot be added after contract or deal closure")
            scenarios = {}
            for name, exit_factor, cost_factor in (
                ("downside", 0.90, 1.25), ("base", 1.00, 1.00), ("upside", 1.05, 1.00),
            ):
                case = dict(values)
                case["expected_exit_price"] *= exit_factor
                for key in ("buyer_repairs", "buyer_funding_holding", "buyer_closing", "buyer_selling_costs"):
                    case[key] *= cost_factor
                scenarios[name] = calculate_scenario(deal["strategy"], case)
            inputs = {**values, "property_type": property_type, "basis": notes}
            evidence = property_evidence(connection, deal["property_id"])
            fact_ids = [fact["id"] for fact in evidence["facts"]]
            snapshot = {
                "strategy": deal["strategy"], "scenarios": scenarios,
                "fact_ids": fact_ids, "formula_version": "assignment-resale-v1",
                "evidence_digest": evidence["digest"],
                "sale_evidence": sale_snapshot(connection, deal["property_id"]),
                "warning": (
                    "Manual scenario analysis; exit price and costs are unverified inputs, not an appraisal or offer."
                ),
            }
            uw_id = str(uuid4())
            connection.execute(
                "INSERT INTO underwritings(id,deal_id,inputs_json,result_json,created_at) VALUES(?,?,?,?,?)",
                (uw_id, str(deal["id"]), json.dumps(inputs, allow_nan=False),
                 json.dumps(snapshot, allow_nan=False), now),
            )
            self._complete_system_task(connection, str(deal["id"]), "underwriting", "Underwriting saved", uw_id)
            self._reopen_system_task(connection, str(deal["id"]), "money_plan", "Underwriting changed; refresh financial plan")
            connection.execute("UPDATE deals SET stage='underwriting',updated_at=? WHERE id=?", (now, str(deal["id"])))
            connection.execute(
                "INSERT INTO deal_events(id,deal_id,stage_before,stage_after,note,evidence_reference,created_at) VALUES(?,?,?,?,?,?,?)",
                (str(uuid4()), str(deal["id"]), deal["stage"], "underwriting",
                 "Underwriting version recorded; no offer was made.", "uw:" + uw_id, now),
            )
        return {"id": uw_id, "deal_id": str(deal["id"]), "inputs": inputs, "result": snapshot, "created_at": now}

    def create_buyer(self, data):
        name = text_field(data, "name", 120)
        company = text_field(data, "company", 160, required=False)
        locations = list_field(data, "locations", max_items=30)
        locations = sorted({" ".join(part.strip().lower().split()) for part in locations})
        strategies = list_field(data, "strategies", allowed=STRATEGIES, max_items=2)
        types = list_field(data, "property_types", max_items=30, required=False)
        types = sorted({value.lower().replace(" ", "_") for value in types})
        price = number_field(data, "max_total_price", nonnegative=True)
        repairs = number_field(data, "max_repairs", nonnegative=True)
        status = text_field(data, "funding_status", 30)
        if status not in {"unverified", "owner_reviewed", "verified"}:
            raise ValueError("funding_status must be unverified, owner_reviewed, or verified")
        verified_at = text_field(data, "verified_at", 40, required=status == "verified")
        reference = text_field(data, "verification_reference", 300, required=status == "verified")
        now = utc_now().isoformat()
        buyer_id = str(uuid4())
        with self.database.session(write=True) as (connection, _):
            connection.execute(
                "INSERT INTO buyers(id,name,company,locations_json,strategies_json,property_types_json,"
                "max_total_price,max_repairs,funding_status,verified_at,verification_reference,status,created_at) "
                "VALUES(?,?,?,?,?,?,?,?,?,?,?,?,?)",
                (buyer_id, name, company, json.dumps(locations), json.dumps(strategies),
                 json.dumps(types), price, repairs, status, verified_at, reference, "active", now),
            )
        return {"id": buyer_id, "name": name, "company": company, "locations": locations,
                "strategies": strategies, "property_types": types, "max_total_price": price,
                "max_repairs": repairs, "funding_status": status, "verified_at": verified_at,
                "verification_reference": reference, "status": "active", "created_at": now}

    def _compare_buyers(self, connection, deal):
        prop = connection.execute("SELECT * FROM properties WHERE id=?", (deal["property_id"],)).fetchone()
        uw = connection.execute(
            "SELECT inputs_json,result_json FROM underwritings WHERE deal_id=? ORDER BY created_at DESC,id LIMIT 1",
            (str(deal["id"]),),
        ).fetchone()
        if uw is None:
            raise ValueError("Record an underwriting before matching buyers")
        inputs, result = json.loads(uw["inputs_json"]), json.loads(uw["result_json"])
        location = " ".join(f"{prop['city']}, {prop['state']}".lower().split())
        evidence = property_evidence(connection, deal["property_id"])
        sales = sale_snapshot(connection, deal["property_id"])
        value = evidence["values"].get("property_type")
        observed_type = canonical(value) if value is not None else None
        plan = self._finance(connection, deal)["plan"]
        if deal["strategy"] == "resale":
            total = inputs["expected_exit_price"]
            price_basis = "entered resale exit price"
        elif plan and plan["current_underwriting"]:
            total = plan["seller_price"] + plan["assignment_fee"]
            price_basis = "proposed seller price plus assignment fee"
        else:
            total = result["scenarios"]["base"]["buyer_acquisition_ceiling"]
            price_basis = "underwriting buyer ceiling; no proposed terms yet"
        repair = inputs["buyer_repairs"]
        candidates = []
        for row in connection.execute("SELECT * FROM buyers WHERE status='active' ORDER BY created_at DESC,id"):
            buyer = self._buyer_json(row)
            reasons = []
            if result.get("evidence_digest") != evidence["digest"]:
                reasons.append("underwriting evidence changed or was not tracked; review underwriting")
            if result.get("sale_evidence", {}).get("digest") != sales["digest"]:
                reasons.append("comparable-sale evidence changed or was not tracked; review underwriting")
            if "property_type" in evidence["conflicts"]:
                reasons.append("property type has conflicting current evidence")
            if location not in buyer["locations"]:
                reasons.append("market does not match")
            if deal["strategy"] not in buyer["strategies"]:
                reasons.append("strategy does not match")
            if total > buyer["max_total_price"]:
                reasons.append("price exceeds buyer limit")
            if repair > buyer["max_repairs"]:
                reasons.append("repair estimate exceeds buyer limit")
            if buyer["property_types"] and (observed_type is None or observed_type not in buyer["property_types"]):
                reasons.append("property type is unknown or outside buyer criteria")
            verification_recent = False
            if buyer["funding_status"] == "verified" and buyer["verified_at"]:
                try:
                    verified = datetime.fromisoformat(buyer["verified_at"].replace("Z", "+00:00"))
                    if verified.tzinfo is None:
                        verified = verified.replace(tzinfo=timezone.utc)
                    age = (datetime.now(timezone.utc) - verified).total_seconds()
                    verification_recent = 0 <= age <= 30 * 24 * 60 * 60
                except (ValueError, TypeError):
                    pass
            if not verification_recent:
                reasons.append("funding evidence requires current owner verification")
            candidates.append({
                "buyer_id": buyer["id"], "name": buyer["name"], "company": buyer["company"],
                "eligible_on_recorded_criteria": not any(r != "funding evidence requires current owner verification" for r in reasons),
                "funding_verified_currently": verification_recent,
                "reasons": reasons,
                "comparison": {"market": location, "buyer_total_price": money(total), "price_basis": price_basis,
                               "buyer_max_total_price": buyer["max_total_price"],
                               "repair_estimate": money(repair), "buyer_max_repairs": buyer["max_repairs"]},
            })
        candidates.sort(key=lambda item: (not item["eligible_on_recorded_criteria"], not item["funding_verified_currently"], item["name"].lower()))
        return candidates

    def match_buyers(self, deal_id):
        now = utc_now().isoformat()
        with self.database.session(write=True) as (connection, _):
            deal = deal_exists(connection, deal_id)
            candidates = self._compare_buyers(connection, deal)
            run_id = str(uuid4())
            connection.execute(
                "INSERT INTO buyer_match_runs(id,deal_id,matches_json,created_at) VALUES(?,?,?,?)",
                (run_id, str(deal["id"]), json.dumps(candidates, allow_nan=False), now),
            )
        return {"id": run_id, "deal_id": str(deal["id"]), "matches": candidates, "created_at": now}
