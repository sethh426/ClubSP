import pytest

from app.sentra_lifecycle import make_transition, validate_transition


FINGERPRINT = "a" * 64


def test_automation_can_move_discovered_source_into_quarantine():
    transition = make_transition(
        FINGERPRINT,
        "discovered",
        "quarantined",
        automated=True,
        reason="New candidate discovered from Data.gov",
    )
    assert transition.to_state == "quarantined"


def test_automation_cannot_activate_source():
    with pytest.raises(ValueError, match="not allowed"):
        validate_transition("proposed", "approved", automated=True)


def test_explicit_approval_can_promote_proposed_source():
    transition = make_transition(
        FINGERPRINT,
        "proposed",
        "approved",
        automated=False,
        reason="Operator reviewed rights, schema, cost and usefulness",
        evidence_refs=("schema:abc", "rights:def"),
    )
    assert transition.automated is False


def test_active_source_can_be_automatically_requarantined():
    transition = make_transition(
        FINGERPRINT,
        "active",
        "requarantined",
        automated=True,
        reason="Schema drift exceeded threshold",
    )
    assert transition.to_state == "requarantined"
