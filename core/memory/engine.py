from __future__ import annotations

from math import isfinite
from uuid import UUID
from typing import Any

from .models import LearningRecord, Observation, Prediction, utc_now
from .store import MemoryStore


class LearningEngine:
    """Connects predictions to outcomes and produces auditable learning records."""

    def __init__(self, store: MemoryStore) -> None:
        self.store = store

    def resolve_prediction(
        self,
        prediction_id: UUID,
        observation: Observation,
    ) -> tuple[Prediction, LearningRecord]:
        prediction = self.store.predictions[prediction_id]

        if prediction.resolved_at is not None:
            raise ValueError("Prediction has already been resolved")
        if (prediction.subject_type, prediction.subject_id) != (observation.subject_type, observation.subject_id):
            raise ValueError("Observation must describe the prediction subject")
        if observation.observed_at < prediction.created_at:
            raise ValueError("Outcome cannot predate the prediction")
        self.store._validate_confidence(observation.confidence)
        self.store._require_source(observation.source_id)
        existing = self.store.observations.get(observation.id)
        if existing is not None and existing != observation:
            raise ValueError("Observation ID already belongs to different evidence")

        error = self._numeric_error(
            prediction.predicted_value,
            observation.actual_value,
        )

        learning = LearningRecord(
            domain=prediction.prediction_type,
            pattern=(
                f"Observed outcome for {prediction.prediction_type}: "
                f"predicted={prediction.predicted_value!r}, "
                f"actual={observation.actual_value!r}"
            ),
            evidence_refs=[prediction.id, observation.id],
            sample_count=1,
            confidence=observation.confidence * max(0.0, 1.0 - error) if error is not None else 0.0,
            learned_at=utc_now(),
            model_version=prediction.model_version,
        )
        if existing is None:
            self.store.add_observation(observation)
        self.store.add_learning(learning)
        prediction.actual_value = observation.actual_value
        prediction.error = error
        prediction.resolved_at = observation.observed_at
        return prediction, learning

    @staticmethod
    def _numeric_error(predicted: Any, actual: Any) -> float | None:
        if (
            isinstance(predicted, (int, float))
            and isinstance(actual, (int, float))
            and not isinstance(predicted, bool)
            and not isinstance(actual, bool)
        ):
            if not isfinite(predicted) or not isfinite(actual):
                raise ValueError("Numeric outcomes and predictions must be finite")
            denominator = max(abs(actual), 1.0)
            return min(abs(predicted - actual) / denominator, 1.0)
        return None
