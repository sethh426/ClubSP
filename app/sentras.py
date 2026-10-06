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
        capabilities=("parcel_identity", "owner_of_record", "site_address", "reported_transfer_date"),
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
    ),
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


# These are source collectors. Meta-Sentras and unconfigured Actor families are
# deliberately excluded from the first-25 milestone.
_PUBLIC_MONITORS = (
    ("allen_county_tax_sale", "Allen County tax-sale notices", "tax_sale", "Allen County, Indiana", "https://www.allencounty.in.gov/270/Tax-Sale", ("tax_sale_notice_index", "document_links")),
    ("allen_county_assessor_resources", "Allen County assessor resource monitor", "parcel_assessor", "Allen County, Indiana", "https://www.allencounty.in.gov/164/Assessor", ("assessor_resource_index", "document_links")),
    ("allen_county_treasurer_resources", "Allen County treasurer resource monitor", "tax", "Allen County, Indiana", "https://www.allencounty.in.gov/284/Treasurer", ("tax_resource_index", "document_links")),
    ("allen_county_recorder_resources", "Allen County recorder resource monitor", "recorded_documents", "Allen County, Indiana", "https://www.allencountyrecorder.us/resources", ("recorder_resource_index", "document_links")),
    ("allen_county_building_resources", "Allen County building-permit resource monitor", "permits", "Allen County, Indiana", "https://www.allencounty.in.gov/234/Building-Department", ("permit_resource_index", "document_links")),
    ("allen_county_planning_hearings", "Allen County planning-hearing document index", "planning", "Allen County, Indiana", "https://www.allencounty.in.gov/1255/Public-Hearing-Documents", ("planning_notice_index", "document_links")),
    ("allen_county_zoning_resources", "Allen County zoning-map resource monitor", "planning", "Allen County, Indiana", "https://www.allencounty.in.gov/1320/Zoning-Map", ("zoning_resource_index", "document_links")),
    ("fort_wayne_code_resources", "Fort Wayne code-compliance resource monitor", "code_enforcement", "Fort Wayne, Indiana", "https://www.cityoffortwayne.in.gov/256/Neighborhood-Code-Compliance", ("code_resource_index", "document_links")),
    ("fort_wayne_redevelopment", "Fort Wayne redevelopment notices and agendas", "public_property", "Fort Wayne, Indiana", "https://www.cityoffortwayne.in.gov/475/Redevelopment-Commission", ("redevelopment_notice_index", "document_links")),
    ("indiana_surplus_property", "Indiana surplus real-estate notice index", "public_property", "Indiana", "https://www.in.gov/idoa/state-resource-management/state-and-federal-surplus/real-estate-sales/surplus-property-information/", ("surplus_notice_index", "document_links")),
    ("hud_home_resources", "HUD home-sale resource monitor", "reo", "United States", "https://www.hud.gov/topics/buying_a_home", ("reo_resource_index", "document_links")),
    ("gsa_property_disposition", "GSA real-property disposition resource monitor", "public_property", "United States", "https://www.gsa.gov/real-estate/real-property-disposition", ("federal_property_resource_index", "document_links")),
)
for _id, _name, _family, _jurisdiction, _url, _capabilities in _PUBLIC_MONITORS:
    SENTRAS[_id] = SentraDefinition(
        id=_id, name=_name, family=_family, acquisition_mode="direct_http",
        capabilities=(*_capabilities, "page_change_detection"),
        jurisdiction=_jurisdiction, status="standby", source_url=_url,
        freshness_target_hours=24, estimated_cost_class="free",
        rights_note="Bounded public-page monitoring only. Linked records require separate access and identity review; no bulk record access is assumed.",
    )

SENTRAS["census_address_geocoder"] = SentraDefinition(
    id="census_address_geocoder", name="Census single-address geocoder",
    family="identity", acquisition_mode="official_api",
    capabilities=("address_match", "geocode"), jurisdiction="United States and supported territories",
    status="standby", source_url="https://geocoding.geo.census.gov/geocoder/locations/onelineaddress",
    freshness_target_hours=720, estimated_cost_class="free",
    rights_note="One supplied address per lookup. Address-range coordinates do not prove parcel identity or ownership.",
)
SENTRAS["census_county_housing"] = SentraDefinition(
    id="census_county_housing", name="Census ACS county housing context",
    family="market_context", acquisition_mode="official_api",
    capabilities=("county_housing_context", "aggregate_rent", "aggregate_value", "margin_of_error"),
    jurisdiction="One US county per query", status="standby",
    source_url="https://api.census.gov/data/2024/acs/acs5", freshness_target_hours=8760,
    estimated_cost_class="free",
    rights_note="2024 ACS five-year survey estimates with margins of error. County context only; never property-level comps or current pricing.",
)
_RENTCAST_COLLECTORS = (
    ("rentcast_property_record", "RentCast single-property record", "/properties", "parcel_assessor", ("provider_property_attributes", "provider_tax_history", "provider_sale_history"), 24),
    ("rentcast_rental_listings", "RentCast rental listings", "/listings/rental/long-term", "listing_market", ("active_rental_listing", "asking_rent", "property_attributes"), 6),
    ("rentcast_value_estimate", "RentCast property value estimate", "/avm/value", "market_estimate", ("provider_value_estimate", "provider_estimate_range"), 24),
    ("rentcast_rent_estimate", "RentCast long-term rent estimate", "/avm/rent/long-term", "market_estimate", ("provider_rent_estimate", "provider_estimate_range"), 24),
    ("rentcast_market_statistics", "RentCast ZIP market statistics", "/markets", "market_context", ("zip_market_context", "sale_listing_statistics", "rental_listing_statistics"), 24),
)
for _id, _name, _path, _family, _capabilities, _hours in _RENTCAST_COLLECTORS:
    SENTRAS[_id] = SentraDefinition(
        id=_id, name=_name, family=_family, acquisition_mode="official_api",
        capabilities=_capabilities, jurisdiction="United States where provider coverage applies",
        status="standby", source_url="https://api.rentcast.io/v1" + _path,
        freshness_target_hours=_hours, credential_env="RENTCAST_API_KEY",
        estimated_cost_class="metered",
        rights_note="Connected provider account and current terms required. Estimates and aggregates are provider-reported context, not accepted underwriting facts.",
    )

FIRST_25_SENTRA_IDS = (
    "allen_county_accdc", "allen_county_north_campus", "allen_county_sheriff_sales",
    "allen_county_imap_parcel", "rentcast_sale_listings", "realestateapi_inventory_preflight",
    *(_row[0] for _row in _PUBLIC_MONITORS),
    "census_address_geocoder", "census_county_housing",
    *(_row[0] for _row in _RENTCAST_COLLECTORS),
)


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
