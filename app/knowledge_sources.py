"""Fixed public source checks with bounded extraction, not research synthesis."""
from datetime import date
import hashlib
from html.parser import HTMLParser
import json
import re
from urllib.request import Request, build_opener

from .providers import COUNTY_LAYER, NoRedirect


SOURCES = [
    {"id": "seller_research", "domain": "sales", "name": "Seller priorities survey", "publisher": "Zillow Research",
     "url": "https://www.zillow.com/research/sellers-housing-trends-report-2025/",
     "limits": "Historical survey of recent primary-residence sellers; not individual seller urgency or distressed-owner evidence."},
    {"id": "market_catalog", "domain": "market", "name": "Aggregate housing data catalog", "publisher": "Zillow Research",
     "url": "https://www.zillow.com/research/data/",
     "limits": "Catalog/methodology check only; no local time series, licensed comps or property valuation is imported."},
    {"id": "email_rules", "domain": "compliance", "name": "Commercial email guidance", "publisher": "Federal Trade Commission",
     "url": "https://www.ftc.gov/business-guidance/resources/can-spam-act-compliance-guide-business",
     "limits": "Federal overview only. Actual campaign purpose, jurisdiction, provider rules and current applicability require professional review."},
    {"id": "funding_context", "domain": "finance", "name": "Mortgage cost scenarios", "publisher": "Consumer Financial Protection Bureau",
     "url": "https://www.consumerfinance.gov/owning-a-home/explore-rates/",
     "limits": "Education about loan-cost factors; not a financing quote, investment loan approval or actual funding verification."},
    {"id": "settlement_context", "domain": "closing", "name": "Loan settlement form overview", "publisher": "Consumer Financial Protection Bureau",
     "url": "https://www.consumerfinance.gov/owning-a-home/closing-disclosure/",
     "limits": "Applies to relevant loan disclosures; do not apply every form or deadline to cash or assignment transactions."},
    {"id": "business_planning", "domain": "operations", "name": "Business planning resources", "publisher": "Small Business Administration",
     "url": "https://www.sba.gov/counseling/plan-your-business/",
     "limits": "General planning resource; no claim that a process, script or transaction has been validated for this business."},
    {"id": "parcel_service", "domain": "property", "name": "Parcel service metadata", "publisher": "Allen County iMap",
     "url": COUNTY_LAYER + "?f=json", "format": "json",
     "limits": "Schema metadata only; no new property facts, comps, title, authority or contact permission."},
    {"id": "indiana_licensing", "domain": "compliance", "name": "Indiana real-estate licensing overview", "publisher": "Indiana Professional Licensing Agency",
     "url": "https://www.in.gov/pla/professions/real-estate-home/real-estate-licensing-information/",
     "limits": "Licensing overview only; current assignment/disclosure statutes and actual applicability remain unresolved until reviewed."},
]
SOURCE_MAP = {s["id"]: s for s in SOURCES}
GAPS = ["Licensed comparable sales and local market time series",
        "Current Indiana assignment/disclosure text and transaction-specific applicability",
        "Transaction tax/accounting requirements and professional review",
        "Mailbox provider, sender identity and campaign policy",
        "Independent title, seller authority and buyer funding verification"]
DAILY_SOURCE_LIMIT = 20
CACHE_HOURS = 24
FETCH_TIMEOUT = 6
MAX_RESPONSE = 1048576


def digest(value):
    return hashlib.sha256(value.encode()).hexdigest()


class PageText(HTMLParser):
    """Ignore executable/navigation content and prefer a main/article region."""
    blocked = {"script", "style", "noscript", "nav", "header", "footer", "aside", "iframe", "svg", "form"}
    voids = {"area", "base", "br", "col", "embed", "hr", "img", "input", "link", "meta", "param", "source", "track", "wbr"}

    def __init__(self):
        super().__init__(convert_charrefs=True)
        self.stack = []
        self.all_parts = []
        self.main_parts = []
        self.title = []
        self.published_on = ""

    def handle_starttag(self, tag, attrs):
        attrs = dict(attrs)
        if tag == "meta" and (attrs.get("property") or attrs.get("name", "")).lower() in {"article:published_time", "datepublished", "date"}:
            raw = attrs.get("content", "")[:10]
            try:
                if date.fromisoformat(raw).isoformat() == raw:
                    self.published_on = raw
            except ValueError:
                pass
        if tag not in self.voids:
            self.stack.append(tag)
        if tag in {"p", "li", "h1", "h2", "h3", "h4", "div", "section", "br"}:
            self.all_parts.append("\n")
            if "main" in self.stack or "article" in self.stack:
                self.main_parts.append("\n")

    def handle_endtag(self, tag):
        if tag in self.stack:
            index = len(self.stack) - 1 - self.stack[::-1].index(tag)
            del self.stack[index:]
        if tag in {"p", "li", "h1", "h2", "h3", "h4", "div", "section"}:
            self.all_parts.append("\n")
            self.main_parts.append("\n")

    def handle_data(self, data):
        if "title" in self.stack:
            self.title.append(data)
            return
        if any(tag in self.blocked for tag in self.stack):
            return
        self.all_parts.append(data)
        if "main" in self.stack or "article" in self.stack:
            self.main_parts.append(data)

    def snapshot(self):
        selected = "".join(self.main_parts).strip() or "".join(self.all_parts).strip()
        sections = [" ".join(part.split()) for part in selected.splitlines() if part.strip()]
        text = "\n".join(sections)
        if len(text) < 100:
            raise ValueError("Source page has too little readable content")
        lowered = text[:2000].lower()
        if any(marker in lowered for marker in ("access denied", "verify you are human", "just a moment", "enable javascript and cookies", "checking your browser")):
            raise ValueError("Source returned an access/challenge page")
        return {"content_hash": digest(text), "word_count": len(text.split()),
                "section_hashes": [digest(part) for part in sections[:500]],
                "title": " ".join(" ".join(self.title).split()[:8]),
                "excerpt": " ".join(text.split()[:12]), "reported_published_on": self.published_on}


class KnowledgeSourceAdapter:
    def fetch(self, source_id):
        if source_id not in SOURCE_MAP:
            raise ValueError("Unknown knowledge source")
        source = SOURCE_MAP[source_id]
        request = Request(source["url"], headers={"User-Agent": "ClubSP/0.1 bounded source review", "Accept": "application/json" if source.get("format") == "json" else "text/html"})
        with build_opener(NoRedirect()).open(request, timeout=FETCH_TIMEOUT) as response:
            body = response.read(MAX_RESPONSE + 1)
            if len(body) > MAX_RESPONSE:
                raise ValueError("Source exceeds the review response limit")
            content_type = response.headers.get_content_type()
            encoding = response.headers.get_content_charset() or "utf-8"
        if source.get("format") == "json":
            payload = json.loads(body)
            if not isinstance(payload, dict) or payload.get("error") or not isinstance(payload.get("fields"), list) or not 1 <= len(payload["fields"]) <= 200:
                raise ValueError("Parcel metadata response is malformed")
            if any(not isinstance(f, dict) or not isinstance(f.get("name"), str) or not isinstance(f.get("type"), str) for f in payload["fields"]):
                raise ValueError("Parcel metadata fields are malformed")
            # Metadata only; never fetch records or follow links returned in data.
            metadata = {"fields": [{k: f.get(k) for k in ("name", "type")}
                                   for f in payload["fields"] if isinstance(f, dict)],
                        "query_formats": payload.get("supportedQueryFormats"),
                        "maximum_records": payload.get("maxRecordCount")}
            body_text = json.dumps(metadata, sort_keys=True, allow_nan=False)
            return {"content_hash": digest(body_text), "word_count": len(body_text.split()),
                    "section_hashes": [digest(body_text)], "title": "Parcel query schema",
                    "excerpt": "Schema metadata checked; no individual property record requested.", "reported_published_on": ""}
        if content_type not in {"text/html", "application/xhtml+xml"}:
            raise ValueError("Source content format is unsupported")
        parser = PageText()
        try:
            parser.feed(body.decode(encoding))
            parser.close()
        except (LookupError, UnicodeError) as exc:
            raise ValueError("Source text encoding is unsupported") from exc
        return parser.snapshot()


def compare_snapshots(previous, current):
    if previous is None:
        return {"kind": "first_check", "previous_word_count": None, "current_word_count": current["word_count"], "changed_section_fingerprints": None}
    old = previous["section_hashes"]
    new = current["section_hashes"]
    return {"kind": "unchanged" if previous["content_hash"] == current["content_hash"] else "changed",
            "previous_word_count": previous["word_count"], "current_word_count": current["word_count"],
            "changed_section_fingerprints": sum(a != b for a, b in zip(old, new)) + abs(len(old) - len(new))}
