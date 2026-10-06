"""Meta-Sentras: discover, quarantine, probe and propose new source candidates.

Discovery never activates a source. Candidates remain quarantined until an
operator/policy review explicitly promotes them into the production Sentra registry.
"""
from __future__ import annotations

from dataclasses import dataclass, field
from hashlib import sha256
from typing import Any, Iterable, Mapping
from urllib.parse import urlparse
import json
import re
import time


DISCOVERY_PROVIDERS = {
    "apify_store": {
        "kind": "actor_catalog",
        "endpoint": "https://api.apify.com/v2/store",
        "auth_required": False,
    },
    "arcgis_hub": {
        "kind": "open_data_catalog",
        "endpoint": "https://hub.arcgis.com/api/search/v1",
        "auth_required": False,
    },
    "data_gov": {
        "kind": "government_catalog",
        "endpoint": "https://api.datagov-catalog-dev.app.cloud.gov/search",
        "auth_required": False,
    },
    "ckan": {
        "kind": "open_data_catalog",
        "endpoint_template": "{base}/api/3/action/package_search",
        "auth_required": False,
    },
}

ALLOWED_SCHEMES = {"https"}
USEFUL_TERMS = {
    "parcel", "assessor", "property", "sale", "sales", "auction", "foreclosure",
    "sheriff", "tax", "land bank", "surplus", "permit", "code enforcement",
    "recorder", "deed", "gis", "housing", "real estate", "zoning",
}


@dataclass(frozen=True)
class SourceCandidate:
    discovery_provider: str
    external_id: str
    name: str
    source_url: str
    description: str = ""
    jurisdiction_hint: str = ""
    capabilities_hint: tuple[str, ...] = ()
    metadata: Mapping[str, Any] = field(default_factory=dict)

    @property
    def fingerprint(self) -> str:
        payload = json.dumps({
            "provider": self.discovery_provider,
            "external_id": self.external_id,
            "url": self.source_url,
        }, sort_keys=True, separators=(",", ":"))
        return sha256(payload.encode("utf-8")).hexdigest()


@dataclass(frozen=True)
class QuarantineAssessment:
    candidate_fingerprint: str
    score: int
    status: str
    reasons: tuple[str, ...]
    blockers: tuple[str, ...]
    recommended_next_step: str


def _safe_https_url(value: str) -> bool:
    try:
        parsed = urlparse(value)
    except ValueError:
        return False
    return parsed.scheme in ALLOWED_SCHEMES and bool(parsed.netloc)


def infer_capabilities(text: str) -> tuple[str, ...]:
    normalized = re.sub(r"\s+", " ", str(text or "").lower())
    matches = []
    capability_map = {
        "parcel": "parcel_identity",
        "assessor": "assessment",
        "auction": "auction",
        "foreclosure": "foreclosure",
        "sheriff": "sheriff_sale",
        "tax sale": "tax_sale",
        "land bank": "public_property",
        "surplus": "public_property",
        "permit": "permit",
        "code enforcement": "code_enforcement",
        "recorder": "recorded_document",
        "deed": "recorded_document",
        "gis": "geospatial",
        "sale": "sale_event",
        "sales": "sale_event",
        "housing": "housing",
        "zoning": "zoning",
    }
    for needle, capability in capability_map.items():
        if needle in normalized and capability not in matches:
            matches.append(capability)
    return tuple(matches)


def usefulness_score(candidate: SourceCandidate) -> QuarantineAssessment:
    reasons: list[str] = []
    blockers: list[str] = []
    score = 0

    if _safe_https_url(candidate.source_url):
        score += 15
        reasons.append("HTTPS source URL")
    else:
        blockers.append("Source URL is missing or not HTTPS")

    text = f"{candidate.name} {candidate.description}".lower()
    matched_terms = sorted(term for term in USEFUL_TERMS if term in text)
    if matched_terms:
        score += min(30, 5 * len(matched_terms))
        reasons.append("Relevant real-estate/public-record terms: " + ", ".join(matched_terms[:6]))

    capabilities = candidate.capabilities_hint or infer_capabilities(text)
    if capabilities:
        score += min(25, 5 * len(capabilities))
        reasons.append("Potential capabilities: " + ", ".join(capabilities[:6]))
    else:
        blockers.append("No useful capability inferred yet")

    provider_bonus = {
        "data_gov": 20,
        "arcgis_hub": 20,
        "ckan": 15,
        "apify_store": 5,
    }.get(candidate.discovery_provider, 0)
    if provider_bonus:
        score += provider_bonus
        reasons.append(f"Discovery provenance bonus: {candidate.discovery_provider}")

    if candidate.jurisdiction_hint:
        score += 10
        reasons.append("Jurisdiction metadata available")

    score = min(score, 100)
    if blockers:
        status = "needs_probe"
        next_step = "probe_metadata"
    elif score >= 70:
        status = "promote_for_review"
        next_step = "schema_probe"
    elif score >= 45:
        status = "needs_probe"
        next_step = "schema_probe"
    else:
        status = "low_priority"
        next_step = "hold"

    return QuarantineAssessment(
        candidate_fingerprint=candidate.fingerprint,
        score=score,
        status=status,
        reasons=tuple(reasons),
        blockers=tuple(blockers),
        recommended_next_step=next_step,
    )


def normalize_apify_store_item(item: Mapping[str, Any]) -> SourceCandidate:
    actor_id = str(item.get("id") or item.get("name") or "").strip()
    username = str(item.get("username") or "").strip()
    name = str(item.get("title") or item.get("name") or actor_id).strip()
    if not actor_id or not name:
        raise ValueError("Apify Store item is missing identity")
    actor_name = str(item.get("name") or actor_id).strip()
    slug = f"{username}~{actor_name}" if username else actor_id
    description = str(item.get("description") or "").strip()
    return SourceCandidate(
        discovery_provider="apify_store",
        external_id=slug,
        name=name,
        source_url=f"https://apify.com/{username}/{actor_name}" if username else "https://apify.com/store",
        description=description,
        capabilities_hint=infer_capabilities(f"{name} {description}"),
        metadata={
            "username": username,
            "actor_name": actor_name,
            "pricing_model": item.get("pricingModel"),
        },
    )


def normalize_arcgis_item(item: Mapping[str, Any]) -> SourceCandidate:
    item_id = str(item.get("id") or item.get("itemId") or "").strip()
    name = str(item.get("name") or item.get("title") or "").strip()
    links = item.get("links") if isinstance(item.get("links"), Mapping) else {}
    url = str(item.get("url") or links.get("self") or links.get("site") or "").strip()
    if not item_id or not name or not url:
        raise ValueError("ArcGIS candidate is missing id, name, or URL")
    description = str(item.get("description") or item.get("snippet") or "").strip()
    return SourceCandidate(
        discovery_provider="arcgis_hub",
        external_id=item_id,
        name=name,
        source_url=url,
        description=description,
        capabilities_hint=infer_capabilities(f"{name} {description}"),
        metadata={"type": item.get("type"), "access": item.get("access")},
    )


def normalize_ckan_dataset(dataset: Mapping[str, Any], *, provider: str = "ckan") -> SourceCandidate:
    dataset_id = str(dataset.get("id") or dataset.get("name") or "").strip()
    name = str(dataset.get("title") or dataset.get("name") or dataset_id).strip()
    source_url = str(dataset.get("url") or "").strip()
    if not source_url:
        resources = dataset.get("resources") if isinstance(dataset.get("resources"), list) else []
        for resource in resources:
            if isinstance(resource, Mapping) and _safe_https_url(str(resource.get("url") or "")):
                source_url = str(resource["url"])
                break
    if not dataset_id or not name or not source_url:
        raise ValueError("CKAN candidate is missing id, name, or usable URL")
    description = str(dataset.get("notes") or "").strip()
    org = dataset.get("organization") if isinstance(dataset.get("organization"), Mapping) else {}
    return SourceCandidate(
        discovery_provider=provider,
        external_id=dataset_id,
        name=name,
        source_url=source_url,
        description=description,
        jurisdiction_hint=str(org.get("title") or "").strip(),
        capabilities_hint=infer_capabilities(f"{name} {description}"),
        metadata={"organization": org.get("title"), "license": dataset.get("license_title")},
    )


def dedupe_candidates(candidates: Iterable[SourceCandidate]) -> list[SourceCandidate]:
    seen: set[str] = set()
    output: list[SourceCandidate] = []
    for candidate in candidates:
        key = candidate.fingerprint
        if key in seen:
            continue
        seen.add(key)
        output.append(candidate)
    return output


def quarantine_record(candidate: SourceCandidate) -> dict[str, Any]:
    assessment = usefulness_score(candidate)
    return {
        "fingerprint": candidate.fingerprint,
        "discovery_provider": candidate.discovery_provider,
        "external_id": candidate.external_id,
        "name": candidate.name,
        "source_url": candidate.source_url,
        "description": candidate.description[:4000],
        "jurisdiction_hint": candidate.jurisdiction_hint,
        "capabilities_hint": list(candidate.capabilities_hint),
        "metadata": dict(candidate.metadata),
        "assessment": {
            "score": assessment.score,
            "status": assessment.status,
            "reasons": list(assessment.reasons),
            "blockers": list(assessment.blockers),
            "recommended_next_step": assessment.recommended_next_step,
        },
        "quarantined_at_epoch_ms": time.time_ns() // 1_000_000,
        "activation_authorized": False,
    }
