import httpx
import pytest

from app.meta_source_transport import arcgis_sample, fetch_source, source_schema


def fixture_fetch(handler):
    transport = httpx.MockTransport(handler)
    return lambda url, **kwargs: fetch_source(url, transport=transport, **kwargs)


def layer_schema(count=2):
    fields = [{"name": "OBJECTID", "type": "esriFieldTypeOID"}]
    fields += [{"name": f"field_{i}", "type": "esriFieldTypeString"} for i in range(count-1)]
    fetch = fixture_fetch(lambda r: httpx.Response(200, json={"fields": fields}))
    return source_schema(fetch("https://example.gov/MapServer/0"))[0]


def test_arcgis_wide_contract_remains_bounded():
    assert len(layer_schema(117)["fields"]) == 117
    with pytest.raises(ValueError, match="excessive"):
        layer_schema(257)


def test_legacy_samples_selected_ids_without_pagination():
    requests = []
    def handle(request):
        params = request.url.params
        requests.append(dict(params))
        if params.get("returnIdsOnly"):
            return httpx.Response(200, json={"objectIdFieldName":"OBJECTID", "objectIds":list(range(100,0,-1))})
        assert "resultRecordCount" not in params
        assert params["objectIds"] == ",".join(map(str,range(1,26)))
        return httpx.Response(200,json={"features":[{"attributes":{"OBJECTID":i,"field_0":"x"}} for i in range(1,26)]})
    _,payload = arcgis_sample("https://example.gov/MapServer/0",layer_schema(),
        {"advancedQueryCapabilities":{"supportsPagination":False}},fetch=fixture_fetch(handle))
    assert len(payload["features"]) == 25 and len(requests) == 2


@pytest.mark.parametrize("ids", [[True], ["1"], [-1], None])
def test_legacy_rejects_malformed_inventory(ids):
    fetch = fixture_fetch(lambda r: httpx.Response(200,json={"objectIdFieldName":"OBJECTID","objectIds":ids}))
    with pytest.raises(ValueError,match="inventory"):
        arcgis_sample("https://example.gov/MapServer/0",layer_schema(),
            {"advancedQueryCapabilities":{"supportsPagination":False}},fetch=fetch)


@pytest.mark.parametrize("rows", [26, 1])
def test_legacy_rejects_excess_rows_or_unrequested_identity(rows):
    def handle(r):
        if r.url.params.get("returnIdsOnly"):
            return httpx.Response(200,json={"objectIdFieldName":"OBJECTID","objectIds":[1]})
        return httpx.Response(200,json={"features":[{"attributes":{"OBJECTID":99,"field_0":"x"}}]*rows})
    with pytest.raises(ValueError):
        arcgis_sample("https://example.gov/MapServer/0",layer_schema(),
            {"advancedQueryCapabilities":{"supportsPagination":False}},fetch=fixture_fetch(handle))


def test_empty_legacy_inventory_cannot_trigger_full_export():
    def handle(r):
        if r.url.params.get("returnIdsOnly"):
            return httpx.Response(200,json={"objectIdFieldName":"OBJECTID","objectIds":[]})
        assert r.url.params["where"] == "1=0"
        return httpx.Response(200,json={"features":[]})
    _,p=arcgis_sample("https://example.gov/MapServer/0",layer_schema(),
        {"advancedQueryCapabilities":{"supportsPagination":False}},fetch=fixture_fetch(handle))
    assert p["features"] == []


def test_geometry_attribute_may_be_omitted_but_other_fields_cannot():
    schema = layer_schema()
    schema['fields'].append('SHAPE')
    schema['field_types']['SHAPE'] = ['esriFieldTypeGeometry']
    schema['fields'].append('Shape.STArea()')
    schema['field_types']['Shape.STArea()'] = ['esriFieldTypeDouble']
    for attrs, valid in [({'OBJECTID':1,'field_0':'x'},True), ({'OBJECTID':1},False)]:
        fetch = fixture_fetch(lambda r: httpx.Response(200,json={'features':[{'attributes':attrs}]}))
        if valid:
            arcgis_sample('https://example.gov/MapServer/0',schema,{},fetch=fetch)
        else:
            with pytest.raises(ValueError,match='drifted'):
                arcgis_sample('https://example.gov/MapServer/0',schema,{},fetch=fetch)
