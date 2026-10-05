"""Fixed-provider JSON transport. Callers own durable budgets and evidence review."""
import json
import time

import httpx


def reject_nonfinite(value):
    raise ValueError("Nonfinite JSON number")


def unique_object(pairs):
    result = {}
    for key, value in pairs:
        if key in result:
            raise ValueError("Provider JSON has duplicate keys")
        result[key] = value
    return result


class BoundedJSONTransport:
    def __init__(self, endpoint, *, transport=None, max_bytes=524288):
        self.endpoint = endpoint
        self.transport = transport
        self.max_bytes = max_bytes

    def fetch(self, params):
        # No environment proxies, redirects, retries or caller-supplied URLs.
        started = time.monotonic()
        try:
            with httpx.Client(transport=self.transport, trust_env=False,
                              follow_redirects=False, timeout=httpx.Timeout(10, connect=5),
                              limits=httpx.Limits(max_connections=1, max_keepalive_connections=0)) as client:
                with client.stream("GET", self.endpoint, params=params,
                                   headers={"Accept": "application/json", "Accept-Encoding": "identity",
                                            "User-Agent": "ClubSP/0.1 bounded parcel research"}) as response:
                    if response.status_code != 200:
                        raise ValueError("Provider request failed; no evidence was imported")
                    if response.headers.get("content-type", "").split(";", 1)[0].strip().lower() not in {"application/json", "text/json"}:
                        raise ValueError("Provider response is not JSON")
                    if response.headers.get("content-encoding", "identity").lower() != "identity":
                        raise ValueError("Provider ignored the uncompressed response requirement")
                    body = bytearray()
                    for chunk in response.iter_bytes():
                        if time.monotonic() - started > 15:
                            raise ValueError("Provider response exceeded the total time limit")
                        body.extend(chunk)
                        if len(body) > self.max_bytes:
                            raise ValueError("Provider response exceeds the single-record size limit")
                    if time.monotonic() - started > 15:
                        raise ValueError("Provider response exceeded the total time limit")
                    payload = json.loads(body, parse_constant=reject_nonfinite, object_pairs_hook=unique_object)
                    if not isinstance(payload, dict):
                        raise ValueError("Provider response must be an object")
                    return str(response.url), payload
        except (httpx.HTTPError, UnicodeError, json.JSONDecodeError) as error:
            # Never surface provider bodies, URLs, credentials or response details.
            raise ValueError("Provider transport failed; no evidence was imported") from error
