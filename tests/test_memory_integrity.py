from dataclasses import replace
from datetime import timedelta
from uuid import uuid4

import pytest

from core.memory import Fact, LearningEngine, MemoryStore, Observation, Prediction, SourceRecord


def make_prediction(store):
    return store.add_prediction(Prediction(
        subject_type="property", subject_id=uuid4(),
        prediction_type="repair_estimate", predicted_value=100,
        confidence=0.7, model_version="repair-v1",
    ))


def make_outcome(prediction, **kwargs):
    return Observation(
        subject_type=prediction.subject_type,
        subject_id=prediction.subject_id,
        observation_type="actual_repair_cost",
        actual_value=100, **kwargs,
    )


def test_missing_source_rejected_without_adding_fact():
    store = MemoryStore()
    with pytest.raises(ValueError, match="Source"):
        store.add_fact(Fact(
            subject_type="property", subject_id=uuid4(), attribute="sqft",
            value=1800, value_type="integer", source_id=uuid4(),
        ))
    assert not store.facts


@pytest.mark.parametrize("confidence", [-0.1, 1.1, float("nan"), float("inf"), True])
def test_invalid_confidence_rejected(confidence):
    store = MemoryStore()
    prediction = Prediction(
        subject_type="property", subject_id=uuid4(), prediction_type="repair_estimate",
        predicted_value=100, confidence=confidence, model_version="v1",
    )
    with pytest.raises(ValueError, match="Confidence"):
        store.add_prediction(prediction)
    assert not store.predictions


def test_duplicate_id_does_not_overwrite_evidence():
    store = MemoryStore()
    source = store.add_source(SourceRecord(source_type="api", provider="original"))
    with pytest.raises(ValueError, match="already exists"):
        store.add_source(replace(source, provider="replacement"))
    assert store.sources[source.id].provider == "original"


@pytest.mark.parametrize("field,value", [("subject_id", uuid4()), ("subject_type", "owner")])
def test_mismatched_subject_does_not_resolve_prediction(field, value):
    store = MemoryStore()
    prediction = make_prediction(store)
    observation = replace(make_outcome(prediction), **{field: value})
    with pytest.raises(ValueError, match="subject"):
        LearningEngine(store).resolve_prediction(prediction.id, observation)
    assert prediction.resolved_at is None
    assert not store.learning_records
    assert not store.observations


def test_outcome_is_saved_and_duplicate_resolution_is_rejected():
    store = MemoryStore()
    prediction = make_prediction(store)
    observation = make_outcome(prediction)
    engine = LearningEngine(store)
    engine.resolve_prediction(prediction.id, observation)
    assert store.observations[observation.id] == observation
    with pytest.raises(ValueError, match="already been resolved"):
        engine.resolve_prediction(prediction.id, observation)
    assert len(store.learning_records) == 1


def test_outcome_before_prediction_is_rejected():
    store = MemoryStore()
    prediction = make_prediction(store)
    observation = make_outcome(
        prediction, observed_at=prediction.created_at - timedelta(seconds=1),
    )
    with pytest.raises(ValueError, match="predate"):
        LearningEngine(store).resolve_prediction(prediction.id, observation)
    assert prediction.resolved_at is None


def test_existing_observation_can_be_resolved():
    store = MemoryStore()
    prediction = make_prediction(store)
    observation = store.add_observation(make_outcome(prediction))
    LearningEngine(store).resolve_prediction(prediction.id, observation)
    assert len(store.observations) == 1


def test_conflicting_observation_id_is_rejected():
    store = MemoryStore()
    prediction = make_prediction(store)
    observation = store.add_observation(make_outcome(prediction))
    with pytest.raises(ValueError, match="different evidence"):
        LearningEngine(store).resolve_prediction(
            prediction.id, replace(observation, actual_value=200),
        )
    assert prediction.resolved_at is None
    assert store.observations[observation.id].actual_value == 100


def test_low_quality_outcome_limits_learning_confidence():
    store = MemoryStore()
    prediction = make_prediction(store)
    _, learning = LearningEngine(store).resolve_prediction(
        prediction.id, make_outcome(prediction, confidence=0.2),
    )
    assert learning.confidence == pytest.approx(0.2)


@pytest.mark.parametrize("actual", [float("nan"), float("inf")])
def test_nonfinite_numeric_outcome_leaves_prediction_unresolved(actual):
    store = MemoryStore()
    prediction = make_prediction(store)
    observation = replace(make_outcome(prediction), actual_value=actual)
    with pytest.raises(ValueError, match="finite"):
        LearningEngine(store).resolve_prediction(prediction.id, observation)
    assert prediction.resolved_at is None
    assert not store.observations


def test_boolean_outcome_is_not_a_numeric_error():
    assert LearningEngine._numeric_error(True, False) is None


def test_supersession_requires_matching_subject_and_attribute():
    store = MemoryStore()
    source = store.add_source(SourceRecord(source_type="api", provider="example"))
    fact = store.add_fact(Fact(
        subject_type="property", subject_id=uuid4(), attribute="sqft",
        value=1800, value_type="integer", source_id=source.id,
    ))
    with pytest.raises(ValueError, match="Superseded"):
        store.add_fact(replace(fact, id=uuid4(), attribute="beds", supersedes_fact_id=fact.id))
    updated = store.add_fact(replace(
        fact, id=uuid4(), value=1900, supersedes_fact_id=fact.id,
    ))
    assert len(store.facts_for("property", fact.subject_id)) == 2
    assert updated.supersedes_fact_id == fact.id
