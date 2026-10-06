"""Canonical Sentra registry for ClubSP source intelligence.

A Sentra describes a governed source capability. Execution is delegated to
provider/extraction adapters; registry metadata must not silently authorize
outreach, deal creation, offers, payments, or other consequential actions.
"""
from __future__ import annotations

from dataclasses import dataclass
from typing import Literal

SentraStatus = Literal["active", "planned", "standby", "disabled"]
AcquisitionMode = Literal[
    "direct_http", "official_api", "arcgis", "apify_actor",
    "crawlee_http", "playwright", "file_parser", "webhook", "manual_import",
]


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


SENTRAS: dict[str, SentraDefinition] = {
    "allen_county_accdc": SentraDefinition(
        id="allen_county_accdc",
        name="Allen County ACCDC availability",
        family="public_property",
        acquisition_mode="direct_http",
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
    ),,
    "meta_apify_store_discovery": SentraDefinition(
        id="meta_apify_store_discovery",
        name="Meta-Sentra: Apify Store discovery",
        family="meta_discovery",
        acquisition_mode="official_api",
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
        capabilities=("source_discovery", "dataset_discovery", "coverage_gap_search"),
        jurisdiction="Public ArcGIS Hub content",
        status="active",
        source_url="https://hub.arcgis.com/api/search/v1",
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
        capabilities=("source_discovery", "dataset_discovery", "government_catalog"),
        jurisdiction="United States government catalog",
        status="active",
        source_url="https://api.datagov-catalog-dev.app.cloud.gov/search",
        freshness_target_hours=24,
        rights_note="Catalog metadata identifies possible datasets/resources; metadata is not the underlying property evidence.",
        downstream=("quarantine",),
        estimated_cost_class="free",
    ),
    "meta_schema_probe": SentraDefinition(
        id="meta_schema_probe",
        name="Meta-Sentra: schema probe",
        family="meta_quality",
        acquisition_mode="direct_http",
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
    return [
        {
            "id": item.id,
            "name": item.name,
            "family": item.family,
            "acquisition_mode": item.acquisition_mode,
            "capabilities": list(item.capabilities),
            "jurisdiction": item.jurisdiction,
            "status": item.status,
            "source_url": item.source_url,
            "freshness_target_hours": item.freshness_target_hours,
            "rights_note": item.rights_note,
            "downstream": list(item.downstream),
            "credential_configured_by": item.credential_env,
            "estimated_cost_class": item.estimated_cost_class,
        }
        for item in SENTRAS.values()
    ]


def sentras_for_capability(capability: str, *, include_planned: bool = False) -> list[SentraDefinition]:
    """Return Sentras declaring a capability, without running external requests."""
    allowed = {"active", "standby"} | ({"planned"} if include_planned else set())
    return [
        item for item in SENTRAS.values()
        if item.status in allowed and capability in item.capabilities
    ]
