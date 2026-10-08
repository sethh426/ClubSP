"""Public buyer research, bounded source monitoring, and a separate qualification queue."""
from datetime import date, datetime, timedelta, timezone
from html.parser import HTMLParser
import json
import logging
import re
import threading
from urllib.parse import urlsplit, urlunsplit, parse_qsl, urlencode
from uuid import uuid4, uuid5, NAMESPACE_URL

import httpx

from .schema import assert_component_compatible, ensure_component
from .validation import text_field

# Only these independently reviewed public pages are fetched by the server.
SOURCES = (
    {"id": "simple-quarters", "name": "Simple Quarters", "url": "https://www.simplequarters.com/"},
    {"id": "buy-my-house-indiana", "name": "Buy My House Indiana", "url": "https://www.bmhindiana.com/how-we-buy-houses/"},
    {"id": "indiana-home-solutions", "name": "Indiana Home Solutions LLC", "url": "https://buysasis.com/"},
)
LABELS = {"buying_request": "Self-reported buying intent", "company_claim": "Company buying claim",
          "acquisition_history": "Past acquisition", "buyer_solicitation": "Looking for buyers",
          "unclear": "Intent unclear"}


def now_utc():
    return datetime.now(timezone.utc)


def source_url(value):
    parts = urlsplit(value)
    if (parts.scheme != "https" or not parts.hostname or parts.username or parts.password
            or parts.port not in (None, 443) or any(c.isspace() for c in value)
            or any(ord(c) < 32 for c in value)):
        raise ValueError("Use a public HTTPS source URL without credentials")
    query = [(k, v) for k, v in parse_qsl(parts.query, keep_blank_values=True)
             if not k.lower().startswith("utm_") and k.lower() not in {"fbclid", "gclid"}]
    return urlunsplit(("https", parts.hostname.lower(), parts.path.rstrip("/") or "/", urlencode(query), ""))


def classify(text, kind):
    value = text.casefold()
    # Soliciting end buyers is not evidence that the author buys.
    if re.search(r"(?:looking for|seeking|need|wanted|building).{0,35}(?:cash buyers|buyer network|buyers list|buyer list)", value):
        return "buyer_solicitation"
    if re.search(r"\b(?:we|i)\b\s+(?:(?:are|am|have)\s+)?(?:do not|don't|don’t|not currently|no longer|stopped)\s+(?:(?:currently|actively|now)\s+)?(?:buy(?:ing)?|acquir(?:ing|e))\b", value):
        return "unclear"
    if kind == "acquisition":
        return "acquisition_history"
    if kind == "company":
        return "company_claim" if re.search(r"\b(?:we|i)\b.{0,35}(?:buy houses|buy homes|cash offers|buying.{0,20}home)", value) else "unclear"
    if re.search(r"\b(?:we|i|i'm|we're|i am)\b.{0,25}(?:buying|buy houses|buy homes|cash buyer|looking to buy|seeking to acquire)", value):
        return "buying_request"
    return "unclear"


class PageText(HTMLParser):
    def __init__(self):
        super().__init__()
        self.hidden = 0
        self.parts = []

    def handle_starttag(self, tag, attrs):
        if tag in {"head", "script", "style", "noscript"}:
            self.hidden += 1

    def handle_endtag(self, tag):
        if tag in {"head", "script", "style", "noscript"}:
            self.hidden = max(0, self.hidden - 1)
        elif tag in {"p", "div", "h1", "h2", "li", "br"}:
            self.parts.append("\n")

    def handle_data(self, data):
        if not self.hidden:
            self.parts.append(data)


def fetch_page(source):
    # No submitted URLs, redirect targets, cookies, credentials, or JS execution.
    with httpx.Client(timeout=10, follow_redirects=False, trust_env=False) as client:
        with client.stream("GET", source["url"], headers={"User-Agent": "ClubSP-PublicResearch/1.0", "Accept": "text/html"}) as response:
            if response.is_redirect or "text/html" not in response.headers.get("content-type", ""):
                raise ValueError("Source did not return a public HTML page")
            response.raise_for_status()
            chunks, size = [], 0
            for chunk in response.iter_bytes():
                size += len(chunk)
                if size > 1_000_000:
                    raise ValueError("Source exceeded the reading limit")
                chunks.append(chunk)
    parser = PageText()
    parser.feed(b"".join(chunks).decode("utf-8", errors="replace"))
    return " ".join(" ".join(parser.parts).split())


class BuyerIntentBook:
    def __init__(self, application, relationships):
        self.database = application.database
        self.relationships = relationships
        self.lock = threading.Lock()
        self.fetch = fetch_page
        with self.database.session(write=True) as (c, _):
            assert_component_compatible(c, "buyer_intent")
            c.execute("CREATE TABLE IF NOT EXISTS buyer_intent_signals (id TEXT PRIMARY KEY, url TEXT NOT NULL UNIQUE, payload TEXT NOT NULL, first_seen TEXT NOT NULL, last_seen TEXT NOT NULL, status TEXT NOT NULL, relationship_id TEXT)")
            c.execute("CREATE TABLE IF NOT EXISTS buyer_intent_observations (id TEXT PRIMARY KEY, signal_id TEXT NOT NULL REFERENCES buyer_intent_signals(id), payload TEXT NOT NULL, observed_at TEXT NOT NULL)")
            c.execute("CREATE TABLE IF NOT EXISTS buyer_intent_sources (id TEXT PRIMARY KEY, attempted_at TEXT, checked_at TEXT, error TEXT)")
            c.execute("CREATE TABLE IF NOT EXISTS buyer_intent_settings (id INTEGER PRIMARY KEY CHECK(id=1), enabled INTEGER NOT NULL)")
            c.execute("INSERT OR IGNORE INTO buyer_intent_settings VALUES(1,1)")
            ensure_component(c, "buyer_intent")

    def capture(self, data, observed=None):
        timestamp = (observed or now_utc()).isoformat()
        url = source_url(text_field(data, "url", 500))
        payload = {"name": text_field(data, "name", 160), "text": text_field(data, "text", 4000),
                   "kind": text_field(data, "kind", 20), "published_on": text_field(data, "published_on", 10, required=False)}
        if payload["kind"] not in {"post", "company", "acquisition"}:
            raise ValueError("Source kind must be post, company, or acquisition")
        if payload["published_on"]:
            try:
                day = date.fromisoformat(payload["published_on"])
                if day.isoformat() != payload["published_on"] or day > (observed or now_utc()).date():
                    raise ValueError()
            except ValueError:
                raise ValueError("Source publication date must be YYYY-MM-DD, not in the future") from None
        payload["category"] = classify(payload["text"], payload["kind"])
        payload["markets"] = [market for market in ("Fort Wayne", "Indiana", "Indianapolis", "New Haven", "Ohio", "Kentucky")
                              if market.casefold() in payload["text"].casefold()]
        encoded = json.dumps(payload, sort_keys=True)
        with self.database.session(write=True) as (c, _):
            old = c.execute("SELECT * FROM buyer_intent_signals WHERE url=?", (url,)).fetchone()
            if old and json.loads(old["payload"])["name"].casefold() != payload["name"].casefold():
                raise ValueError("This source already belongs to another author; use a distinct post or comment URL")
            sid = old["id"] if old else str(uuid4())
            if old:
                c.execute("UPDATE buyer_intent_signals SET payload=?,last_seen=? WHERE id=?", (encoded, timestamp, sid))
            else:
                c.execute("INSERT INTO buyer_intent_signals VALUES(?,?,?,?,?,'new',NULL)", (sid, url, encoded, timestamp, timestamp))
            if not old or old["payload"] != encoded:
                c.execute("INSERT INTO buyer_intent_observations VALUES(?,?,?,?)", (str(uuid4()), sid, encoded, timestamp))
        return {"id": sid}

    def refresh(self):
        if not self.lock.acquire(blocking=False):
            return {"status": "already_running"}
        try:
            result = []
            for source in SOURCES:
                stamp = now_utc()
                with self.database.session(write=True) as (c, _):
                    if not c.execute("SELECT enabled FROM buyer_intent_settings WHERE id=1").fetchone()[0]:
                        return {"status": "paused", "sources": result}
                    row = c.execute("SELECT * FROM buyer_intent_sources WHERE id=?", (source["id"],)).fetchone()
                    # The attempt is persisted before I/O: restart or repeated clicks cannot hammer a source.
                    if row and row["attempted_at"] and stamp - datetime.fromisoformat(row["attempted_at"]) < timedelta(hours=24):
                        result.append({"id": source["id"], "status": "not_due"})
                        continue
                    c.execute("INSERT INTO buyer_intent_sources VALUES(?,?,NULL,NULL) ON CONFLICT(id) DO UPDATE SET attempted_at=excluded.attempted_at", (source["id"], stamp.isoformat()))
                try:
                    page = self.fetch(source)
                    category = classify(page, "company")
                    if category != "company_claim":
                        raise ValueError("No clear buying claim found; last evidence retained")
                    match = re.search(r"\b(?:we|i)\b.{0,35}(?:buy houses|buy homes|cash offers|buying.{0,20}home)", page, re.I)
                    excerpt = " ".join(page[max(0, match.start() - 30):].split()[:24])
                    markets = [m for m in ("Fort Wayne", "Indiana", "Indianapolis", "New Haven") if m.casefold() in page.casefold()]
                    # Short excerpt, separate page-level market observation, never invented numeric criteria.
                    self.capture({"url": source["url"], "name": source["name"], "kind": "company",
                                  "text": excerpt + "\nMarkets mentioned on page: " + ", ".join(markets)}, stamp)
                    error = None
                except Exception:
                    # Do not expose fetched text, sensitive environment, or remote error material.
                    error = "Public page could not be checked or its buying claim could not be recognized. Previous evidence retained."
                with self.database.session(write=True) as (c, _):
                    c.execute("UPDATE buyer_intent_sources SET checked_at=CASE WHEN ? IS NULL THEN ? ELSE checked_at END,error=? WHERE id=?",
                              (error, stamp.isoformat(), error, source["id"]))
                result.append({"id": source["id"], "status": "failed" if error else "checked"})
            return {"status": "completed", "sources": result}
        finally:
            self.lock.release()

    def settings(self, data):
        if set(data) != {"enabled"} or type(data["enabled"]) is not bool:
            raise ValueError("Provide enabled as true or false")
        with self.database.session(write=True) as (c, _):
            c.execute("UPDATE buyer_intent_settings SET enabled=? WHERE id=1", (int(data["enabled"]),))
        return {"enabled": data["enabled"]}

    def review(self, sid, data):
        if set(data) != {"status"} or data["status"] not in {"new", "shortlisted", "dismissed"}:
            raise ValueError("Choose new, shortlisted, or dismissed")
        with self.database.session(write=True) as (c, _):
            if not c.execute("SELECT 1 FROM buyer_intent_signals WHERE id=?", (sid,)).fetchone():
                raise LookupError("Signal not found")
            c.execute("UPDATE buyer_intent_signals SET status=? WHERE id=?", (data["status"], sid))
        return {"id": sid, "status": data["status"]}

    def relationship(self, sid, data):
        if data:
            raise ValueError("Relationship preparation request must be empty")
        with self.database.session() as (c, _):
            row = c.execute("SELECT * FROM buyer_intent_signals WHERE id=?", (sid,)).fetchone()
        if not row:
            raise LookupError("Signal not found")
        if row["relationship_id"]:
            return {"relationship_id": row["relationship_id"]}
        p = json.loads(row["payload"])
        if p["category"] not in {"buying_request", "company_claim"} or row["status"] == "dismissed":
            raise ValueError("Only an undismissed buying request or company claim can become a prospect")
        # Existing name is surfaced for review, not silently merged or changed.
        candidates = [r for r in self.relationships.state()["relationships"]
                      if p["name"].casefold() in {r["profile"]["name"].casefold(), r["profile"]["company"].casefold()}]
        if candidates:
            return {"existing_relationships": [r["id"] for r in candidates]}
        saved = self.relationships.save({"request_key": str(uuid5(NAMESPACE_URL, "clubsp:buyer-intent:" + sid)),
                 "name": p["name"], "company": p["name"] if p["kind"] == "company" else "",
                 "kind": "investor", "status": "prospect", "permission": "unknown", "owner": "ClubSP owner",
                 "source_reference": row["url"], "needs": p["text"], "markets": p["markets"],
                 "next_action": "Confirm identity, current buying markets, price range, property type, closing capacity and permission before qualifying."})
        with self.database.session(write=True) as (c, _):
            c.execute("UPDATE buyer_intent_signals SET relationship_id=? WHERE id=?", (saved["relationship_id"], sid))
        return {"relationship_id": saved["relationship_id"]}

    def state(self):
        today = now_utc()
        with self.database.session() as (c, _):
            enabled = bool(c.execute("SELECT enabled FROM buyer_intent_settings WHERE id=1").fetchone()[0])
            rows = c.execute("SELECT * FROM buyer_intent_signals ORDER BY last_seen DESC").fetchall()
            signals = []
            for row in rows:
                p = json.loads(row["payload"])
                age = (today.date() - date.fromisoformat(p["published_on"])).days if p["published_on"] else None
                freshness = "Date unknown" if age is None else "Older than 30 days" if age > 30 else "Published within 30 days"
                recent_check = today - datetime.fromisoformat(row["last_seen"]) <= timedelta(hours=48)
                priority = 0 if p["category"] == "buying_request" and age is not None and age <= 30 else 1 if p["category"] == "buying_request" else 2 if p["category"] == "company_claim" else 3
                action = ("Review the buying request, then confirm criteria and capacity." if p["category"] == "buying_request" else
                          "Confirm this company is buying now and accepts sourced deals." if p["category"] == "company_claim" else
                          "Research current intent; past purchases do not establish present demand." if p["category"] == "acquisition_history" else
                          "This author is looking for buyers; do not count them as a buyer." if p["category"] == "buyer_solicitation" else
                          "Read the source and establish whether the author actually buys.")
                history = c.execute("SELECT payload,observed_at FROM buyer_intent_observations WHERE signal_id=? ORDER BY rowid DESC LIMIT 5", (row["id"],)).fetchall()
                signals.append({**dict(row), **p, "label": LABELS[p["category"]], "freshness": freshness,
                                "recent_check": recent_check, "priority": priority, "next_action": action,
                                "history": [{"observed_at": h["observed_at"], **json.loads(h["payload"])} for h in history]})
            sources = []
            for s in SOURCES:
                row = c.execute("SELECT * FROM buyer_intent_sources WHERE id=?", (s["id"],)).fetchone()
                state = dict(row) if row else {"attempted_at": None, "checked_at": None, "error": None}
                sources.append({**s, **state, "next_check": (datetime.fromisoformat(state["attempted_at"]) + timedelta(hours=24)).isoformat() if state["attempted_at"] else None})
        signals.sort(key=lambda s: (s["status"] == "dismissed", s["priority"]))
        return {"enabled": enabled, "signals": signals, "sources": sources,
                "summary": {"total": len(signals), "shortlisted": sum(s["status"] == "shortlisted" for s in signals),
                            "buying_requests": sum(s["category"] == "buying_request" and s["status"] != "dismissed" for s in signals)},
                "coverage": "Three selected company websites checked at most once per 24 hours. Posts and outside findings can be saved below; social networks are not automatically searched."}


class BuyerIntentScheduler:
    def __init__(self, book):
        self.book = book
        self.event = threading.Event()
        self.thread = threading.Thread(target=self.run, name="buyer-intent", daemon=True)

    def run(self):
        while not self.event.is_set():
            try:
                self.book.refresh()
            except Exception:
                logging.getLogger(__name__).exception("Buyer source cycle failed")
            self.event.wait(60)

    def start(self):
        self.thread.start()

    def stop(self):
        self.event.set()
        self.thread.join(timeout=35)
