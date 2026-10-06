"""Canonical Sentra registry for ClubSP source intelligence.

A Sentra describes a governed source capability. Execution is delegated to
provider/extraction adapters; registry metadata must not silently authorize
outreach, deal creation, offers, payments, or other consequential actions.
"""
from __future__ import annotations

from dataclasses import dataclass, asdict
import ipaddress
import re
from typing import Literal
from urllib.parse import urlsplit

SentraStatus = Literal["active", "planned", "standby", "disabled"]
AcquisitionMode = Literal[
    "direct_http", "official_api", "arcgis", "apify_actor",
    "crawlee_http", "playwright", "file_parser", "webhook", "manual_import",
]
ACQUISITION_MODES = frozenset(AcquisitionMode.__args__)
SOURCE_TYPES = frozenset({"public_record", "market_provider", "source_catalog", "page_monitor", "internal", "licensed_import", "unclassified"})


def public_source_url(value: str) -> str:
    """Validate a configured URL without making a request or resolving DNS."""
    if not isinstance(value, str) or any(ord(c) < 33 for c in value):
        raise ValueError("source URL must be a public HTTPS URL")
    try:
        parsed = urlsplit(value)
        port = parsed.port
    except ValueError as exc:
        raise ValueError("source URL must be a public HTTPS URL") from exc
    if parsed.scheme != "https" or not parsed.hostname or parsed.username or parsed.password or port not in (None, 443):
        raise ValueError("source URL must be a public HTTPS URL on port 443")
    host = parsed.hostname.rstrip(".").lower()
    if host == "localhost" or host.endswith((".local", ".localhost", ".internal", ".invalid")):
        raise ValueError("local or reserved source hosts are not allowed")
    try:
        address = ipaddress.ip_address(host)
    except ValueError:
        pass
    else:
        if not address.is_global:
            raise ValueError("private or reserved source addresses are not allowed")
    return value


@dataclass(frozen=True)
class SentraDefinition:
    id: str
    name: str
    family: str
    acquisition_mode: AcquisitionMode
    capabilities: tuple[str, ...]
    jurisdiction: str
    status: SentraStatus
    source_url: str
    freshness_target_hours: int | None
    rights_note: str
    downstream: tuple[str, ...] = ("evidence_review",)
    credential_env: str | None = None
    estimated_cost_class: Literal["free", "low", "metered", "unknown"] = "unknown"
    source_type: str = "unclassified"
    provenance_refs: tuple[str, ...] = ("builtin:app.sentras",)
    schema_fingerprint: str | None = None
    rights_review_ref: str | None = None
    max_cost_per_run_cents: int | None = None
    daily_request_limit: int = 20
    coverage_markets: tuple[str, ...] = ()
    coverage_scope: Literal["market", "country", "global", "internal", "unconfirmed"] = "unconfirmed"
    verified_capabilities: tuple[str, ...] = ()

    def __post_init__(self):
        if not isinstance(self.id, str) or not re.fullmatch(r"[A-Za-z][A-Za-z0-9_]{0,99}", self.id):
            raise ValueError("Sentra id must begin with a letter and contain letters, numbers or underscores")
        for name in ("name", "family", "jurisdiction", "rights_note"):
            value = getattr(self, name)
            if not isinstance(value, str) or not value.strip() or len(value) > 4000:
                raise ValueError(f"Sentra {name} must be nonempty bounded text")
        if self.status not in SentraStatus.__args__ or self.acquisition_mode not in ACQUISITION_MODES:
            raise ValueError("unsupported Sentra status or acquisition mode")
        if self.source_type not in SOURCE_TYPES or self.estimated_cost_class not in {"free", "low", "metered", "unknown"}:
            raise ValueError("unsupported source type or cost class")
        if self.coverage_scope not in {"market", "country", "global", "internal", "unconfirmed"}:
            raise ValueError("unsupported coverage scope")
        for name in ("capabilities", "downstream", "provenance_refs", "coverage_markets", "verified_capabilities"):
            values = getattr(self, name)
            if not isinstance(values, tuple) or len(values) > 100 or any(not isinstance(v, str) or not v.strip() or len(v) > 500 for v in values):
                raise ValueError(f"Sentra {name} must be a bounded tuple of text")
            if len(set(values)) != len(values):
                raise ValueError(f"Sentra {name} must be unique")
        if not self.capabilities or not self.provenance_refs:
            raise ValueError("Sentra capabilities and provenance are required")
        if not set(self.verified_capabilities).issubset(self.capabilities):
            raise ValueError("verified capabilities must be declared capabilities")
        if self.coverage_scope == "market" and not self.coverage_markets:
            raise ValueError("market coverage requires explicit market aliases")
        if self.source_type == "internal":
            if self.coverage_scope != "internal" or not self.source_url.startswith("https://localhost.invalid/meta/"):
                raise ValueError("internal Sentras require an internal scope and endpoint")
        else:
            public_source_url(self.source_url)
        if self.freshness_target_hours is not None and (type(self.freshness_target_hours) is not int or not 1 <= self.freshness_target_hours <= 8760):
            raise ValueError("freshness target must be positive integer hours")
        if type(self.daily_request_limit) is not int or not 1 <= self.daily_request_limit <= 1000:
            raise ValueError("daily request limit must be an integer from 1 to 1000")
        if self.max_cost_per_run_cents is not None and (type(self.max_cost_per_run_cents) is not int or self.max_cost_per_run_cents < 0):
            raise ValueError("cost ceiling must be nonnegative integer cents")
        if self.credential_env is not None and (not isinstance(self.credential_env, str) or not re.fullmatch(r"[A-Z][A-Z0-9_]{0,99}", self.credential_env)):
            raise ValueError("credentials must reference an environment variable name")
        if self.schema_fingerprint is not None and not re.fullmatch(r"[0-9a-f]{64}", self.schema_fingerprint):
            raise ValueError("schema fingerprint must be a sha256 digest")
        if self.rights_review_ref is not None and (not isinstance(self.rights_review_ref, str) or not self.rights_review_ref.strip() or len(self.rights_review_ref) > 4000):
            raise ValueError("rights review reference must be bounded text")

    def catalog_record(self):
        row = asdict(self)
        for name in ("capabilities", "downstream", "provenance_refs", "coverage_markets", "verified_capabilities"):
            row[name] = list(row[name])
        row["credential_configured_by"] = row.pop("credential_env")
        return row


SENTRAS: dict[str, SentraDefinition] = {
    "allen_county_accdc": SentraDefinition(
        id="allen_county_accdc",
        name="Allen County ACCDC availability",
        family="public_property",
        acquisition_mode="direct_http",
        source_type="page_monitor",
        estimated_cost_class="free",
        coverage_scope="market",
        coverage_markets=("Allen County, Indiana", "Fort Wayne, IN", "Fort Wayne, Indiana"),
        capabilities=("availability", "public_sale_notice", "change_detection"),
        jurisdiction="Allen County, Indiana",
        status="active",
        source_url="https://www.allencounty.in.gov/334/ACCDC-Properties",
        freshness_target_hours=24,
        rights_note="Public county page; retain provenance and review current access/use requirements.",
    ),
    "allen_county_north_campus": SentraDefinition(
        id="allen_county_north_campus",
        name="Allen County North Campus sale notice",
        family="public_property",
        acquisition_mode="direct_http",
        source_type="page_monitor",
        estimated_cost_class="free",
        coverage_scope="market",
        coverage_markets=("Allen County, Indiana", "Fort Wayne, IN", "Fort Wayne, Indiana"),
        capabilities=("public_sale_notice", "parcel", "minimum_bid", "bid_window", "change_detection"),
        jurisdiction="Allen County, Indiana",
        status="active",
        source_url="https://www.allencounty.in.gov/1305/Sale-of-North-Campus-Property",
        freshness_target_hours=24,
        rights_note="Public county sale notice; advertised terms remain evidence requiring current verification.",
    ),
    "allen_county_sheriff_sales": SentraDefinition(
        id="allen_county_sheriff_sales",
        name="Allen County sheriff sales",
        family="foreclosure_auction",
        acquisition_mode="file_parser",
        source_type="public_record",
        estimated_cost_class="free",
        coverage_scope="market",
        coverage_markets=("Allen County, Indiana", "Fort Wayne, IN", "Fort Wayne, Indiana"),
        capabilities=("sheriff_sale", "foreclosure_notice", "sale_date", "judgment_amount", "documents"),
        jurisdiction="Allen County, Indiana",
        status="active",
        source_url="https://www.allencountysheriff.org/2026-sheriff-sales/",
        freshness_target_hours=24,
        rights_note="Public sheriff-sale notices; status, cancellation, parcel identity and sale terms require verification.",
    ),
    "allen_county_imap_parcel": SentraDefinition(
        id="allen_county_imap_parcel",
        name="Allen County iMap parcel evidence",
        family="parcel_assessor",
        acquisition_mode="arcgis",
        source_type="public_record",
        estimated_cost_class="free",
        coverage_scope="market",
        coverage_markets=("Allen County, Indiana", "Fort Wayne, IN", "Fort Wayne, Indiana"),
        capabilities=("parcel_identity", "owner_of_record", "assessment", "property_attributes"),
        jurisdiction="Allen County, Indiana",
        status="active",
        source_url="https://gis.acimap.us/services/rest/services/CFW/Parcels_With_Ownership_Information/MapServer/0",
        freshness_target_hours=24,
        rights_note="Public GIS evidence; exact identity review and source provenance are required.",
    ),
    "rentcast_sale_listings": SentraDefinition(
        id="rentcast_sale_listings",
        name="RentCast sale listings",
        family="listing_market",
        acquisition_mode="official_api",
        source_type="market_provider",
        capabilities=("active_listing", "market", "property_type", "price", "beds", "baths", "sqft", "year_built"),
        jurisdiction="United States where provider coverage applies",
        status="active",
        source_url="https://api.rentcast.io/v1/listings/sale",
        freshness_target_hours=6,
        rights_note="Use is subject to the connected provider account and current terms; results are discovery evidence.",
        credential_env="RENTCAST_API_KEY",
        estimated_cost_class="metered",
    ),
    "realestateapi_inventory_preflight": SentraDefinition(
        id="realestateapi_inventory_preflight",
        name="RealEstateAPI inventory preflight",
        family="listing_market",
        acquisition_mode="official_api",
        source_type="market_provider",
        capabilities=("inventory_count", "buyer_demand_preflight"),
        jurisdiction="United States where provider coverage applies",
        status="active",
        source_url="https://api.realestateapi.com/v2/PropertySearch",
        freshness_target_hours=24,
        rights_note="Count-mode planning evidence; billing and permissions depend on the connected provider plan.",
        credential_env="REALESTATEAPI_API_KEY",
        estimated_cost_class="metered",
    ),
    "apify_public_foreclosure": SentraDefinition(
        id="apify_public_foreclosure",
        name="Apify public foreclosure source family",
        family="foreclosure_auction",
        acquisition_mode="apify_actor",
        capabilities=("foreclosure_notice", "sheriff_sale", "tax_sale", "candidate_discovery"),
        jurisdiction="Configured supported US jurisdictions",
        status="planned",
        source_url="https://apify.com/",
        freshness_target_hours=24,
        rights_note="Actor and underlying sources must be vetted for quality, rights, cost and output schema before activation.",
        credential_env="APIFY_TOKEN",
        estimated_cost_class="metered",
    ),
    "apify_arcgis_parcels": SentraDefinition(
        id="apify_arcgis_parcels",
        name="Apify ArcGIS parcel source family",
        family="parcel_assessor",
        acquisition_mode="apify_actor",
        capabilities=("parcel_identity", "assessment", "property_attributes", "source_discovery"),
        jurisdiction="Configured compatible US jurisdictions",
        status="planned",
        source_url="https://apify.com/",
        freshness_target_hours=24,
        rights_note="Actor and each underlying public ArcGIS layer require review before production use.",
        credential_env="APIFY_TOKEN",
        estimated_cost_class="metered",
    ),
    "meta_apify_store_discovery": SentraDefinition(
        id="meta_apify_store_discovery",
        name="Meta-Sentra: Apify Store discovery",
        family="meta_discovery",
        acquisition_mode="official_api",
        source_type="source_catalog",
        coverage_scope="global",
        capabilities=("source_discovery", "actor_discovery", "coverage_gap_search"),
        jurisdiction="Global catalog; downstream source jurisdiction varies",
        status="active",
        source_url="https://api.apify.com/v2/store",
        freshness_target_hours=24,
        rights_note="Public Actor catalog discovery only; discovered Actors remain quarantined until reviewed.",
        downstream=("quarantine",),
        estimated_cost_class="free",
    ),
    "meta_arcgis_hub_discovery": SentraDefinition(
        id="meta_arcgis_hub_discovery",
        name="Meta-Sentra: ArcGIS Hub discovery",
        family="meta_discovery",
        acquisition_mode="official_api",
        source_type="source_catalog",
        coverage_scope="global",
        capabilities=("source_discovery", "dataset_discovery", "coverage_gap_search"),
        jurisdiction="Public ArcGIS Hub content",
        status="active",
        source_url="https://www.arcgis.com/sharing/rest/search",
        freshness_target_hours=24,
        rights_note="Discovery metadata only; each discovered service requires independent rights/schema review.",
        downstream=("quarantine",),
        estimated_cost_class="free",
    ),
    "meta_data_gov_discovery": SentraDefinition(
        id="meta_data_gov_discovery",
        name="Meta-Sentra: Data.gov discovery",
        family="meta_discovery",
        acquisition_mode="official_api",
        source_type="source_catalog",
        coverage_scope="global",
        capabilities=("source_discovery", "dataset_discovery", "government_catalog"),
        jurisdiction="United States government catalog",
        status="active",
        source_url="https://api.gsa.gov/technology/datagov/v4/search",
        freshness_target_hours=24,
        rights_note="Catalog metadata identifies possible datasets/resources; metadata is not the underlying property evidence.",
        downstream=("quarantine",),
        credential_env="DATAGOV_API_KEY",
        estimated_cost_class="free",
    ),
    "meta_schema_probe": SentraDefinition(
        id="meta_schema_probe",
        name="Meta-Sentra: schema probe",
        family="meta_quality",
        acquisition_mode="direct_http",
        source_type="internal",
        coverage_scope="internal",
        capabilities=("schema_probe", "schema_fingerprint", "source_quality"),
        jurisdiction="Quarantined sources only",
        status="active",
        source_url="https://localhost.invalid/meta/schema-probe",
        freshness_target_hours=None,
        rights_note="Internal bounded probe capability; never promotes or imports a source automatically.",
        downstream=("quarantine",),
        estimated_cost_class="low",
    ),
    "meta_source_health": SentraDefinition(
        id="meta_source_health",
        name="Meta-Sentra: source health and drift",
        family="meta_quality",
        acquisition_mode="manual_import",
        source_type="internal",
        coverage_scope="internal",
        capabilities=("source_health", "schema_drift", "freshness_monitoring", "re_quarantine"),
        jurisdiction="All registered Sentras",
        status="active",
        source_url="https://localhost.invalid/meta/source-health",
        freshness_target_hours=1,
        rights_note="Internal telemetry only; may recommend or trigger re-quarantine but cannot activate a source.",
        downstream=("quarantine", "operator_alert"),
        estimated_cost_class="low",
    )
}


def sentra_catalog() -> list[dict]:
    """Return JSON-safe, non-secret Sentra metadata for APIs/UI."""
    return [item.catalog_record() for item in SENTRAS.values()]


def sentras_for_capability(capability: str, *, include_planned: bool = False) -> list[SentraDefinition]:
    """Return Sentras declaring a capability, without running external requests."""
    allowed = {"active", "standby"} | ({"planned"} if include_planned else set())
    return [
        item for item in SENTRAS.values()
        if item.status in allowed and capability in item.capabilities
    ]
