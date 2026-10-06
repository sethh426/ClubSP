"""Lifecycle rules for automatically discovered Sentra candidates.

The lifecycle deliberately separates discovery from activation. Automated
Meta-Sentras can advance through bounded evidence-gathering states, but only an
explicit approval event can move a candidate into approved/active production use.
"""
from __future__ import annotations

from dataclasses import dataclass
from typing import Literal

CandidateState = Literal[
    "discovered",
    "quarantined",
    "metadata_probed",
    "schema_probed",
    "proposed",
    "approved",
    "active",
    "rejected",
    "requarantined",
]

AUTOMATED_TRANSITIONS: dict[str, set[str]] = {
    "discovered": {"quarantined"},
    "quarantined": {"metadata_probed", "rejected"},
    "metadata_probed": {"schema_probed", "rejected"},
    "schema_probed": {"proposed", "rejected"},
    "proposed": set(),
    "approved": {"active"},
    "active": {"requarantined"},
    "requarantined": {"metadata_probed", "rejected"},
    "rejected": set(),
}

APPROVAL_TRANSITIONS: dict[str, set[str]] = {
    "proposed": {"approved", "rejected"},
    "requarantined": {"approved", "rejected"},
}


@dataclass(frozen=True)
class CandidateTransition:
    candidate_fingerprint: str
    from_state: CandidateState
    to_state: CandidateState
    automated: bool
    reason: str
    evidence_refs: tuple[str, ...] = ()


def validate_transition(
    from_state: CandidateState,
    to_state: CandidateState,
    *,
    automated: bool,
) -> None:
    allowed = AUTOMATED_TRANSITIONS if automated else APPROVAL_TRANSITIONS
    if to_state not in allowed.get(from_state, set()):
        mode = "automated" if automated else "approval"
        raise ValueError(f"{mode} transition {from_state}->{to_state} is not allowed")


def make_transition(
    candidate_fingerprint: str,
    from_state: CandidateState,
    to_state: CandidateState,
    *,
    automated: bool,
    reason: str,
    evidence_refs: tuple[str, ...] = (),
) -> CandidateTransition:
    if not candidate_fingerprint or len(candidate_fingerprint) != 64:
        raise ValueError("candidate fingerprint must be a sha256 hex digest")
    validate_transition(from_state, to_state, automated=automated)
    if not reason.strip():
        raise ValueError("transition reason is required")
    return CandidateTransition(
        candidate_fingerprint=candidate_fingerprint,
        from_state=from_state,
        to_state=to_state,
        automated=automated,
        reason=reason.strip(),
        evidence_refs=evidence_refs,
    )
