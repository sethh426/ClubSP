from uuid import uuid4

from core.memory import (
    Fact,
    LearningEngine,
    MemoryStore,
    Observation,
    Prediction,
    SourceRecord,
)


def test_fact_retains_provenance() -> None:
    store = MemoryStore()
    source = store.add_source(
        SourceRecord(source_type="api", provider="example", external_id="123")
    )

    subject_id = uuid4()
    fact = store.add_fact(
        Fact(
            subject_type="property",
            subject_id=subject_id,
            attribute="sqft",
            value=1800,
            value_type="integer",
            source_id=source.id,
            confidence=0.95,
        )
    )

    assert store.facts_for("property", subject_id)[0].source_id == source.id
    assert fact.confidence == 0.95


def test_prediction_outcome_learning_loop() -> None:
    store = MemoryStore()
    prediction = store.add_prediction(
        Prediction(
            subject_type="property",
            subject_id=uuid4(),
            prediction_type="repair_estimate",
            predicted_value=30000,
            confidence=0.7,
            model_version="repair-v1",
        )
    )
    observation = store.add_observation(
        Observation(
            subject_type="property",
            subject_id=prediction.subject_id,
            observation_type="actual_repair_cost",
            actual_value=33000,
        )
    )

    resolved, learning = LearningEngine(store).resolve_prediction(
        prediction.id, observation
    )

    assert resolved.actual_value == 33000
    assert resolved.error is not None
    assert learning.evidence_refs == [prediction.id, observation.id]
    assert learning.sample_count == 1
