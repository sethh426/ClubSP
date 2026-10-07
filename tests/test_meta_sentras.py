from app.meta_sentras import (
    SourceCandidate,
    dedupe_candidates,
    infer_capabilities,
    normalize_apify_store_item,
    normalize_ckan_dataset,
    quarantine_record,
    usefulness_score,
)


def test_infer_capabilities_from_source_description():
    found = infer_capabilities("County GIS parcel assessor foreclosure sale records")
    assert "parcel_identity" in found
    assert "assessment" in found
    assert "foreclosure" in found
    assert "geospatial" in found


def test_government_candidate_scores_higher_and_stays_quarantined():
    candidate = SourceCandidate(
        discovery_provider="data_gov",
        external_id="x",
        name="County Parcel and Tax Sale Data",
        source_url="https://example.gov/data",
        description="GIS parcel assessor tax sale dataset",
        jurisdiction_hint="Example County",
        capabilities_hint=("parcel_identity", "assessment", "tax_sale"),
    )
    assessment = usefulness_score(candidate)
    assert assessment.score >= 70
    record = quarantine_record(candidate)
    assert record["activation_authorized"] is False
    assert record["assessment"]["status"] == "promote_for_review"


def test_apify_actor_is_discovered_not_activated():
    candidate = normalize_apify_store_item({
        "id": "abc",
        "username": "vendor",
        "name": "county-parcel-scraper",
        "title": "County Parcel Scraper",
        "description": "Find assessor parcel GIS records",
        "pricingModel": "PAY_PER_USAGE",
    })
    record = quarantine_record(candidate)
    assert record["discovery_provider"] == "apify_store"
    assert record["activation_authorized"] is False


def test_ckan_uses_https_resource_when_dataset_url_missing():
    candidate = normalize_ckan_dataset({
        "id": "dataset-1",
        "title": "Property sales",
        "notes": "Recorded property sale records",
        "resources": [{"url": "https://data.example.gov/sales.csv"}],
        "organization": {"title": "Example County"},
    })
    assert candidate.source_url == "https://data.example.gov/sales.csv"


def test_dedupe_candidates_uses_stable_fingerprint():
    candidate = SourceCandidate(
        discovery_provider="data_gov",
        external_id="same",
        name="A",
        source_url="https://example.gov/a",
    )
    assert dedupe_candidates([candidate, candidate]) == [candidate]
