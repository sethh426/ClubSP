from __future__ import annotations

from dataclasses import dataclass, field
from datetime import datetime, timezone
from typing import Any
from uuid import UUID, uuid4


def utc_now() -> datetime:
    return datetime.now(timezone.utc)


@dataclass(slots=True)
class SourceRecord:
    source_type: str
    provider: str
    external_id: str | None = None
    url: str | None = None
    retrieved_at: datetime = field(default_factory=utc_now)
    published_at: datetime | None = None
    raw_reference: str | None = None
    content_hash: str | None = None
    reliability_score: float | None = None
    id: UUID = field(default_factory=uuid4)


@dataclass(slots=True)
class Fact:
    subject_type: str
    subject_id: UUID
    attribute: str
    value: Any
    value_type: str
    source_id: UUID
    observed_at: datetime = field(default_factory=utc_now)
    confidence: float = 0.5
    status: str = "active"
    supersedes_fact_id: UUID | None = None
    id: UUID = field(default_factory=uuid4)


@dataclass(slots=True)
class Prediction:
    subject_type: str
    subject_id: UUID
    prediction_type: str
    predicted_value: Any
    confidence: float
    model_version: str
    feature_snapshot: dict[str, Any] = field(default_factory=dict)
    created_at: datetime = field(default_factory=utc_now)
    resolved_at: datetime | None = None
    actual_value: Any | None = None
    error: float | None = None
    id: UUID = field(default_factory=uuid4)


@dataclass(slots=True)
class Observation:
    subject_type: str
    subject_id: UUID
    observation_type: str
    actual_value: Any
    source_id: UUID | None = None
    observed_at: datetime = field(default_factory=utc_now)
    confidence: float = 1.0
    id: UUID = field(default_factory=uuid4)


@dataclass(slots=True)
class LearningRecord:
    domain: str
    pattern: str
    evidence_refs: list[UUID] = field(default_factory=list)
    sample_count: int = 0
    confidence: float = 0.0
    learned_at: datetime = field(default_factory=utc_now)
    model_version: str = "initial"
    status: str = "active"
    id: UUID = field(default_factory=uuid4)


@dataclass(slots=True)
class WorkflowEvent:
    deal_id: UUID
    stage_before: str | None
    stage_after: str | None
    actor: str
    action: str
    evidence_refs: list[UUID] = field(default_factory=list)
    confidence: float = 1.0
    rule_version: str | None = None
    created_at: datetime = field(default_factory=utc_now)
    id: UUID = field(default_factory=uuid4)
