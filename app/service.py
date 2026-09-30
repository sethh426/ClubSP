from __future__ import annotations

from dataclasses import asdict
from math import isfinite
from uuid import UUID, uuid4

from core.memory import Fact, LearningEngine, Observation, Prediction, SourceRecord
from core.memory.models import utc_now
from .database import COLLECTIONS, Database


def text_field(data, key, limit=300, required=True):
    value = data.get(key, "")
    if not isinstance(value, str):
        raise ValueError(f"{key} must be text")
    value = value.strip()
    if required and not value:
        raise ValueError(f"{key} is required")
    if len(value) > limit:
        raise ValueError(f"{key} is too long")
    return value


def number_field(data, key, default=None, nonnegative=False):
    value = data.get(key, default)
    if isinstance(value, bool) or not isinstance(value, (int, float)) or not isfinite(value):
        raise ValueError(f"{key} must be a finite number")
    if nonnegative and value < 0:
        raise ValueError(f"{key} cannot be negative")
    return value


def property_exists(connection, property_id):
    property_id = UUID(str(property_id))
    if connection.execute("SELECT id FROM properties WHERE id=?", (str(property_id),)).fetchone() is None:
        raise LookupError("Property not found")
    return property_id


class Application:
    def __init__(self, path):
        self.database = Database(path)

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
            return result

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
            source = memory.add_source(SourceRecord(
                source_type="manual", provider=provider, url=url or None,
            ))
            supersedes = data.get("supersedes_fact_id")
            fact = memory.add_fact(Fact(
                subject_type="property", subject_id=property_id,
                attribute=attribute, value=value,
                value_type=("boolean" if isinstance(value, bool) else
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
                prediction_type=prediction_type,
                predicted_value=predicted_value, confidence=confidence,
                model_version="manual-v1",
                feature_snapshot={
                    "fact_ids": [str(fact.id) for fact in memory.facts_for("property", property_id)],
                },
            ))
        return asdict(prediction)

    def resolve(self, prediction_id, data):
        prediction_id = UUID(prediction_id)
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
        return {
            "prediction": asdict(resolved),
            "observation": asdict(observation),
            "learning": asdict(learning),
        }
