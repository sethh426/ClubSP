from app.sentras import SENTRAS, sentra_catalog, sentras_for_capability


def test_sentra_ids_match_registry_keys():
    assert SENTRAS
    assert all(key == value.id for key, value in SENTRAS.items())


def test_catalog_is_json_safe_and_does_not_expose_secret_values():
    rows = sentra_catalog()
    assert len(rows) == len(SENTRAS)
    rentcast = next(row for row in rows if row["id"] == "rentcast_sale_listings")
    assert rentcast["credential_configured_by"] == "RENTCAST_API_KEY"
    assert "api_key" not in rentcast
    assert "token" not in rentcast


def test_capability_routing_excludes_planned_by_default():
    active = sentras_for_capability("foreclosure_notice")
    assert {item.id for item in active} == {"allen_county_sheriff_sales"}

    expanded = sentras_for_capability("foreclosure_notice", include_planned=True)
    assert {item.id for item in expanded} == {
        "allen_county_sheriff_sales",
        "apify_public_foreclosure",
    }
