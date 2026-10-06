import socket

import httpx
import pytest

from app.meta_source_transport import PublicHTTPTransport, fetch_source, source_schema, validate_public_url


@pytest.mark.parametrize("url", [
    "http://example.gov/data", "https://localhost/data", "https://127.0.0.1/data",
    "https://169.254.169.254/latest/meta-data", "https://10.0.0.1/data",
    "https://[::1]/data", "https://example.gov:8443/data", "https://user:password@example.gov/data",
    "https://host.internal/data", "https://host.local/data",
])
def test_probe_rejects_nonpublic_or_credentialed_targets(url):
    with pytest.raises(ValueError):
        validate_public_url(url)


def test_public_transport_rejects_dns_rebinding_before_network(monkeypatch):
    monkeypatch.setattr(socket, "getaddrinfo", lambda *a, **k: [
        (socket.AF_INET, socket.SOCK_STREAM, 6, "", ("169.254.169.254", 443)),
    ])
    transport = PublicHTTPTransport()
    try:
        with pytest.raises(ValueError, match="non-public address"):
            transport.handle_request(httpx.Request("GET", "https://example.gov/data"))
    finally:
        transport.close()


def test_public_transport_pins_ip_and_preserves_tls_hostname(monkeypatch):
    monkeypatch.setattr(socket, "getaddrinfo", lambda *a, **k: [
        (socket.AF_INET, socket.SOCK_STREAM, 6, "", ("8.8.8.8", 443)),
    ])
    requests = []
    transport = PublicHTTPTransport()
    transport._inner.close()
    transport._inner = httpx.MockTransport(lambda request: requests.append(request) or httpx.Response(200, json={"id": 1}))
    try:
        transport.handle_request(httpx.Request("GET", "https://example.gov/data"))
        assert requests[0].url.host == "8.8.8.8"
        assert requests[0].headers["host"] == "example.gov"
        assert requests[0].extensions["sni_hostname"] == "example.gov"
    finally:
        transport.close()


def test_transport_rejects_oversized_stream_before_full_body():
    consumed = []
    class Stream(httpx.SyncByteStream):
        def __iter__(self):
            for i in range(100):
                consumed.append(i)
                yield b"x" * 8192
    transport = httpx.MockTransport(lambda request: httpx.Response(200, stream=Stream(),
                                      headers={"Content-Type": "application/json"}))
    with pytest.raises(ValueError, match="bounded sample"):
        fetch_source("https://example.gov/data", max_bytes=16384, transport=transport)
    assert len(consumed) < 5


@pytest.mark.parametrize("body", [b'{"id":1,"id":2}', b'{"id":NaN}', b'{bad'])
def test_transport_rejects_ambiguous_or_invalid_json(body):
    transport = httpx.MockTransport(lambda request: httpx.Response(200, content=body,
                                      headers={"Content-Type": "application/json"}))
    response = fetch_source("https://example.gov/data", transport=transport)
    with pytest.raises(ValueError, match="invalid JSON"):
        source_schema(response)


def test_schema_tracks_field_type_drift_but_ignores_values():
    def sample(payload):
        transport = httpx.MockTransport(lambda request: httpx.Response(200, json=payload))
        return source_schema(fetch_source("https://example.gov/data", transport=transport))[0]
    first, second = sample([{"id": 1, "price": 2}]), sample([{"id": 3, "price": 4.0}])
    assert first["schema_fingerprint"] == second["schema_fingerprint"]
    assert sample([{"id": 1, "price": "changed"}])["schema_fingerprint"] != first["schema_fingerprint"]


@pytest.mark.parametrize("body", [b"id,id\n1,2\n", b"id,value\n1\n", b"id\n1,2\n"])
def test_csv_rejects_ambiguous_or_incomplete_records(body):
    transport = httpx.MockTransport(lambda request: httpx.Response(200, content=body,
                                      headers={"Content-Type": "text/csv"}))
    with pytest.raises(ValueError):
        source_schema(fetch_source("https://example.gov/data", transport=transport))
