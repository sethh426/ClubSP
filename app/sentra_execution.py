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


MAX_EXECUTION_BYTES = 2_000_000


@dataclass(frozen=True)
class SentraExecutionRequest:
    sentra_id: str
    operation: str = "observe"
    input_data: Mapping[str, Any] = field(default_factory=dict)
    idempotency_key: str | None = None
    max_bytes: int = MAX_EXECUTION_BYTES

    def __post_init__(self) -> None:
        if self.sentra_id not in SENTRAS:
            raise ValueError("unknown Sentra")
        if self.operation not in {"observe", "search", "lookup", "refresh"}:
            raise ValueError("unsupported Sentra operation")
        if not 1 <= self.max_bytes <= MAX_EXECUTION_BYTES:
            raise ValueError("max_bytes is outside the supported range")


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
        return json.dumps(payload, sort_keys=True, separators=(",", ":"), ensure_ascii=False).encode("utf-8")
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

        headers = {"Accept": "application/json", "User-Agent": "ClubSP/0.1 Sentra"}
        if definition.credential_env:
            credential = os.environ.get(definition.credential_env, "").strip()
            if not credential:
                raise ValueError(f"{definition.credential_env} is not configured")
            # Provider-specific authentication stays in dedicated adapters. The generic
            # executor refuses to guess header semantics for credentials.
            raise ValueError("credentialed official API Sentras require a provider-specific executor")

        started = time.time_ns()
        with httpx.Client(timeout=10.0, follow_redirects=False) as client:
            response = client.get(definition.source_url, headers=headers)
        if response.status_code != 200:
            raise ValueError(f"Sentra source returned HTTP {response.status_code}")
        if len(response.content) > request.max_bytes:
            raise ValueError("Sentra source response exceeded the configured payload limit")
        payload = response.json()
        return result_from_payload(
            definition,
            payload,
            source_url=str(response.url),
            started_ns=started,
            metadata={"http_status": response.status_code},
            max_bytes=request.max_bytes,
        )


class ApifyActorExecutor(SentraExecutor):
    """Apify Actor execution boundary.

    This executor intentionally requires an approved actor ID supplied by the
    Sentra configuration/input. Actor discovery and approval are separate policy
    steps; arbitrary actors are not executed from user input.
    """

    acquisition_mode = "apify_actor"

    def execute(self, definition: SentraDefinition, request: SentraExecutionRequest) -> SentraExecutionResult:
        token = os.environ.get(definition.credential_env or "APIFY_TOKEN", "").strip()
        if not token:
            raise ValueError("APIFY_TOKEN is not configured")
        actor_id = str(request.input_data.get("approved_actor_id") or "").strip()
        if not actor_id:
            raise ValueError("approved_actor_id is required for an Apify Sentra")
        if request.input_data.get("actor_approved") is not True:
            raise ValueError("Apify actor must be explicitly approved before execution")

        try:
            import httpx
        except ImportError as exc:
            raise ValueError("Apify Sentra execution requires httpx") from exc

        actor_input = request.input_data.get("actor_input") or {}
        max_total_charge_usd = request.input_data.get("max_total_charge_usd")
        params = {"token": token, "waitForFinish": 30}
        if max_total_charge_usd is not None:
            params["maxTotalChargeUsd"] = max_total_charge_usd

        started = time.time_ns()
        url = f"https://api.apify.com/v2/acts/{actor_id}/runs"
        with httpx.Client(timeout=35.0, follow_redirects=False) as client:
            response = client.post(url, params=params, json=actor_input)
        if response.status_code not in {200, 201}:
            raise ValueError(f"Apify Actor start failed with HTTP {response.status_code}")
        if len(response.content) > request.max_bytes:
            raise ValueError("Apify run response exceeded the configured payload limit")
        payload = response.json()
        return result_from_payload(
            definition,
            payload,
            source_url=definition.source_url,
            started_ns=started,
            metadata={"actor_id": actor_id, "http_status": response.status_code},
            max_bytes=request.max_bytes,
        )


EXECUTORS: dict[str, SentraExecutor] = {
    "official_api": OfficialJSONExecutor(),
    "apify_actor": ApifyActorExecutor(),
}


def executor_for(definition: SentraDefinition) -> SentraExecutor:
    executor = EXECUTORS.get(definition.acquisition_mode)
    if executor is None:
        raise ValueError(f"no executor registered for acquisition mode {definition.acquisition_mode}")
    return executor


def execute_sentra(request: SentraExecutionRequest) -> SentraExecutionResult:
    definition = SENTRAS[request.sentra_id]
    if definition.status not in {"active", "standby"}:
        raise ValueError("Sentra is not enabled for execution")
    return executor_for(definition).execute(definition, request)
