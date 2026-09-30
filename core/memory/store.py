from __future__ import annotations

from collections import defaultdict
from math import isfinite
from typing import TypeVar
from uuid import UUID

from .models import (
    Fact,
    LearningRecord,
    Observation,
    Prediction,
    SourceRecord,
    WorkflowEvent,
)

T = TypeVar("T")


class MemoryStore:
    """Small in-memory reference implementation.

    Persistence adapters can implement the same conceptual operations later
    without changing the domain models.
    """

    def __init__(self) -> None:
        self.sources: dict[UUID, SourceRecord] = {}
        self.facts: dict[UUID, Fact] = {}
        self.predictions: dict[UUID, Prediction] = {}
        self.observations: dict[UUID, Observation] = {}
        self.learning_records: dict[UUID, LearningRecord] = {}
        self.workflow_events: dict[UUID, WorkflowEvent] = {}

        self._by_subject: dict[tuple[str, UUID], set[UUID]] = defaultdict(set)

    @staticmethod
    def _validate_confidence(value: float) -> None:
        if isinstance(value, bool) or not isinstance(value, (int, float)) or not isfinite(value) or not 0 <= value <= 1:
            raise ValueError("Confidence must be a finite number between 0 and 1")

    @staticmethod
    def _require_new(records: dict, record_id: UUID) -> None:
        if record_id in records:
            raise ValueError("Record ID already exists")

    def _require_source(self, source_id: UUID | None) -> None:
        if source_id is not None and source_id not in self.sources:
            raise ValueError("Source record does not exist")

    def add_source(self, source: SourceRecord) -> SourceRecord:
        self._require_new(self.sources, source.id)
        if source.reliability_score is not None:
            self._validate_confidence(source.reliability_score)
        self.sources[source.id] = source
        return source

    def add_fact(self, fact: Fact) -> Fact:
        self._require_new(self.facts, fact.id)
        if fact.source_id is None:
            raise ValueError("Facts require a source record")
        self._require_source(fact.source_id)
        self._validate_confidence(fact.confidence)
        if fact.supersedes_fact_id is not None:
            previous = self.facts.get(fact.supersedes_fact_id)
            if previous is None or (previous.subject_type, previous.subject_id, previous.attribute) != (fact.subject_type, fact.subject_id, fact.attribute):
                raise ValueError("Superseded fact must exist and describe the same subject and attribute")
        self.facts[fact.id] = fact
        self._by_subject[(fact.subject_type, fact.subject_id)].add(fact.id)
        return fact

    def add_prediction(self, prediction: Prediction) -> Prediction:
        self._require_new(self.predictions, prediction.id)
        self._validate_confidence(prediction.confidence)
        self.predictions[prediction.id] = prediction
        return prediction

    def add_observation(self, observation: Observation) -> Observation:
        self._require_new(self.observations, observation.id)
        self._validate_confidence(observation.confidence)
        self._require_source(observation.source_id)
        self.observations[observation.id] = observation
        return observation

    def add_learning(self, learning: LearningRecord) -> LearningRecord:
        self._require_new(self.learning_records, learning.id)
        self._validate_confidence(learning.confidence)
        self.learning_records[learning.id] = learning
        return learning

    def add_workflow_event(self, event: WorkflowEvent) -> WorkflowEvent:
        self._require_new(self.workflow_events, event.id)
        self._validate_confidence(event.confidence)
        self.workflow_events[event.id] = event
        return event

    def facts_for(self, subject_type: str, subject_id: UUID) -> list[Fact]:
        ids = self._by_subject.get((subject_type, subject_id), set())
        return [self.facts[i] for i in ids]

    def unresolved_predictions(self) -> list[Prediction]:
        return [p for p in self.predictions.values() if p.resolved_at is None]
