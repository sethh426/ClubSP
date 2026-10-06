import pytest

from app.sentra_execution import (
    MAX_EXECUTION_BYTES,
    SentraExecutionRequest,
    execute_sentra,
    result_from_payload,
)
from app.sentras import SENTRAS


def test_execution_request_rejects_unknown_sentra():
    with pytest.raises(ValueError, match="unknown Sentra"):
        SentraExecutionRequest("missing")


def test_execution_request_rejects_oversized_limit():
    with pytest.raises(ValueError, match="max_bytes"):
        SentraExecutionRequest("allen_county_accdc", max_bytes=MAX_EXECUTION_BYTES + 1)


def test_result_has_stable_payload_digest():
    definition = SENTRAS["allen_county_accdc"]
    first = result_from_payload(definition, {"b": 2, "a": 1})
    second = result_from_payload(definition, {"a": 1, "b": 2})
    assert first.payload_sha256 == second.payload_sha256
    assert first.event_key == second.event_key


def test_result_rejects_oversized_payload():
    definition = SENTRAS["allen_county_accdc"]
    with pytest.raises(ValueError, match="payload limit"):
        result_from_payload(definition, {"blob": "x" * 100}, max_bytes=20)


def test_planned_sentra_cannot_execute():
    request = SentraExecutionRequest(
        "apify_public_foreclosure",
        input_data={"approved_actor_id": "example/actor", "actor_approved": True},
    )
    with pytest.raises(ValueError, match="not enabled"):
        execute_sentra(request)
