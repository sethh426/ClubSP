from __future__ import annotations

from collections import defaultdict
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

    def add_source(self, source: SourceRecord) -> SourceRecord:
        self.sources[source.id] = source
        return source

    def add_fact(self, fact: Fact) -> Fact:
        self.facts[fact.id] = fact
        self._by_subject[(fact.subject_type, fact.subject_id)].add(fact.id)
        return fact

    def add_prediction(self, prediction: Prediction) -> Prediction:
        self.predictions[prediction.id] = prediction
        return prediction

    def add_observation(self, observation: Observation) -> Observation:
        self.observations[observation.id] = observation
        return observation

    def add_learning(self, learning: LearningRecord) -> LearningRecord:
        self.learning_records[learning.id] = learning
        return learning

    def add_workflow_event(self, event: WorkflowEvent) -> WorkflowEvent:
        self.workflow_events[event.id] = event
        return event

    def facts_for(self, subject_type: str, subject_id: UUID) -> list[Fact]:
        ids = self._by_subject.get((subject_type, subject_id), set())
        return [self.facts[i] for i in ids]

    def unresolved_predictions(self) -> list[Prediction]:
        return [p for p in self.predictions.values() if p.resolved_at is None]
