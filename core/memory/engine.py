from __future__ import annotations

from datetime import datetime
from typing import Any

from .models import LearningRecord, Observation, Prediction, utc_now
from .store import MemoryStore


class LearningEngine:
    """Connects predictions to outcomes and produces auditable learning records."""

    def __init__(self, store: MemoryStore) -> None:
        self.store = store

    def resolve_prediction(
        self,
        prediction_id,
        observation: Observation,
    ) -> tuple[Prediction, LearningRecord]:
        prediction = self.store.predictions[prediction_id]

        error = self._numeric_error(
            prediction.predicted_value,
            observation.actual_value,
        )

        prediction.actual_value = observation.actual_value
        prediction.error = error
        prediction.resolved_at = observation.observed_at

        learning = LearningRecord(
            domain=prediction.prediction_type,
            pattern=(
                f"Observed outcome for {prediction.prediction_type}: "
                f"predicted={prediction.predicted_value!r}, "
                f"actual={observation.actual_value!r}"
            ),
            evidence_refs=[prediction.id, observation.id],
            sample_count=1,
            confidence=max(0.0, 1.0 - error) if error is not None else 0.0,
            learned_at=utc_now(),
            model_version=prediction.model_version,
        )
        self.store.add_learning(learning)
        return prediction, learning

    @staticmethod
    def _numeric_error(predicted: Any, actual: Any) -> float | None:
        if isinstance(predicted, (int, float)) and isinstance(actual, (int, float)):
            denominator = max(abs(actual), 1.0)
            return min(abs(predicted - actual) / denominator, 1.0)
        return None
