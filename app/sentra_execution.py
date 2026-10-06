"""Transport-neutral execution contracts for ClubSP Sentras.

Executors acquire bounded source payloads and return provenance-rich results.
They do not normalize business facts or authorize downstream actions.
"""
from __future__ import annotations

from dataclasses import dataclass, field
from hashlib import sha256
from typing import Any, Mapping
import json
import os
import time

from .sentras import SENTRAS, SentraDefinition
from .meta_source_transport import fetch_source, source_schema
from .sentra_registry import SentraRegistry, execution_blockers


MAX_EXECUTION_BYTES = 2_000_000


@dataclass(frozen=True)
class SentraExecutionRequest:
    sentra_id: str
    operation: str = "observe"
    input_data: Mapping[str, Any] = field(default_factory=dict)
    idempotency_key: str | None = None
    max_bytes: int = MAX_EXECUTION_BYTES

    def __post_init__(self) -> None:
        if not isinstance(self.sentra_id, str) or not self.sentra_id.strip():
            raise ValueError("Sentra id is required")
        if self.operation not in {"observe", "search", "lookup", "refresh"}:
            raise ValueError("unsupported Sentra operation")
        if type(self.max_bytes) is not int or not 1 <= self.max_bytes <= MAX_EXECUTION_BYTES:
            raise ValueError("max_bytes is outside the supported range")
        if not isinstance(self.input_data, Mapping):
            raise ValueError("Sentra input must be a mapping")


@dataclass(frozen=True)
class SentraExecutionResult:
    sentra_id: str
    acquisition_mode: str
    status: str
    source_url: str
    payload: Any
    payload_sha256: str
    observed_at_epoch_ms: int
    duration_ms: int
    metadata: Mapping[str, Any] = field(default_factory=dict)

    @property
    def event_key(self) -> str:
        return f"{self.sentra_id}:{self.payload_sha256}"


class SentraExecutor:
    acquisition_mode: str

    def execute(self, definition: SentraDefinition, request: SentraExecutionRequest) -> SentraExecutionResult:
        raise NotImplementedError


def canonical_payload_bytes(payload: Any) -> bytes:
    try:
        return json.dumps(payload, sort_keys=True, separators=(",", ":"), ensure_ascii=False, allow_nan=False).encode("utf-8")
    except (TypeError, ValueError) as exc:
        raise ValueError("Sentra payload must be JSON serializable") from exc


def result_from_payload(
    definition: SentraDefinition,
    payload: Any,
    *,
    source_url: str | None = None,
    started_ns: int | None = None,
    metadata: Mapping[str, Any] | None = None,
    max_bytes: int = MAX_EXECUTION_BYTES,
) -> SentraExecutionResult:
    raw = canonical_payload_bytes(payload)
    if len(raw) > max_bytes:
        raise ValueError("Sentra result exceeded the configured payload limit")
    now_ns = time.time_ns()
    start = now_ns if started_ns is None else started_ns
    return SentraExecutionResult(
        sentra_id=definition.id,
        acquisition_mode=definition.acquisition_mode,
        status="success",
        source_url=source_url or definition.source_url,
        payload=payload,
        payload_sha256=sha256(raw).hexdigest(),
        observed_at_epoch_ms=now_ns // 1_000_000,
        duration_ms=max(0, (now_ns - start) // 1_000_000),
        metadata=dict(metadata or {}),
    )


class OfficialJSONExecutor(SentraExecutor):
    """Bounded JSON HTTP executor for approved official/provider API Sentras."""

    acquisition_mode = "official_api"

    def execute(self, definition: SentraDefinition, request: SentraExecutionRequest) -> SentraExecutionResult:
        try:
            import httpx
        except ImportError as exc:
            raise ValueError("HTTP Sentra execution requires httpx") from exc

        if definition.credential_env:
            credential = os.environ.get(definition.credential_env, "").strip()
            if not credential:
                raise ValueError(f"{definition.credential_env} is not configured")
            # Provider-specific authentication stays in dedicated adapters. The generic
            # executor refuses to guess header semantics for credentials.
            raise ValueError("credentialed official API Sentras require a provider-specific executor")

        started = time.time_ns()
        if request.input_data or request.operation not in {"observe", "refresh"}:
            raise ValueError("this executor supports configured source observation only")
        response = fetch_source(definition.source_url, max_bytes=request.max_bytes)
        schema, payload = source_schema(response)
        if "json" not in response.content_type:
            raise ValueError("official JSON executor requires a JSON source")
        return result_from_payload(
            definition,
            payload,
            source_url=definition.source_url,
            started_ns=started,
            metadata={"http_status": 200, "schema_fingerprint": schema["schema_fingerprint"],
                      "raw_sha256": response.payload_hash, "evidence_scope": "raw_source"},
            max_bytes=request.max_bytes,
        )


class DirectHTTPExecutor(SentraExecutor):
    acquisition_mode = "direct_http"

    def execute(self, definition, request):
        if definition.credential_env or request.input_data or request.operation not in {"observe", "refresh"}:
            raise ValueError("this executor supports uncredentialed configured source observation only")
        started = time.time_ns()
        response = fetch_source(definition.source_url, max_bytes=request.max_bytes)
        schema, payload = source_schema(response)
        return result_from_payload(definition, payload, started_ns=started, max_bytes=request.max_bytes,
                                   metadata={"schema_fingerprint": schema["schema_fingerprint"],
                                             "raw_sha256": response.payload_hash,
                                             "evidence_scope": "raw_source"})


class ApifyActorExecutor(SentraExecutor):
    """Apify Actor execution boundary.

    This executor intentionally requires an approved actor ID supplied by the
    Sentra configuration/input. Actor discovery and approval are separate policy
    steps; arbitrary actors are not executed from user input.
    """

    acquisition_mode = "apify_actor"

    def execute(self, definition: SentraDefinition, request: SentraExecutionRequest) -> SentraExecutionResult:
        # A caller-supplied actor_approved flag is not a persisted registry approval.
        # Actor launch/dataset collection and durable billing are section 2 gates.
        raise ValueError("Apify execution requires a registry-backed Actor contract and durable cost accounting")


EXECUTORS: dict[str, SentraExecutor] = {
    "direct_http": DirectHTTPExecutor(),
    "official_api": OfficialJSONExecutor(),
    "apify_actor": ApifyActorExecutor(),
}


def executor_for(definition: SentraDefinition) -> SentraExecutor:
    executor = EXECUTORS.get(definition.acquisition_mode)
    if executor is None:
        raise ValueError(f"no executor registered for acquisition mode {definition.acquisition_mode}")
    return executor


def execute_sentra(request: SentraExecutionRequest, *, registry=None) -> SentraExecutionResult:
    registry = registry or SentraRegistry(SENTRAS.values())
    definition = registry.get(request.sentra_id)
    if definition.status != "active":
        raise ValueError("Sentra is not enabled for execution")
    if definition.source_type == "internal":
        raise ValueError("internal Sentras run through their application service")
    blockers = execution_blockers(definition)
    if blockers:
        raise ValueError("Sentra execution blocked: " + ", ".join(blockers))
    return executor_for(definition).execute(definition, request)
