"""Bounded discovery from selected publisher-owned feeds, not social scraping."""
from datetime import datetime, timezone
from email.utils import parsedate_to_datetime
import re
from urllib.parse import urlsplit
from xml.etree import ElementTree as ET

from .meta_source_transport import fetch_source, validate_public_url

FEEDS = ({"id": "indiana-home-solutions-posts", "name": "Indiana Home Solutions LLC",
          "url": "https://buysasis.com/feed/", "type": "publisher_feed"},)


def publication_day(raw):
    if not raw:
        return ""
    try:
        try:
            stamp = parsedate_to_datetime(raw)
        except (ValueError, TypeError):
            stamp = datetime.fromisoformat(raw.replace("Z", "+00:00"))
        if stamp.tzinfo is None:
            stamp = stamp.replace(tzinfo=timezone.utc)
        day = stamp.astimezone(timezone.utc).date()
        return day.isoformat() if day <= datetime.now(timezone.utc).date() else ""
    except (ValueError, TypeError, OverflowError):
        return ""


def parse_posts(body, source):
    # Reject UTF-16/32 documents too: a byte-level declaration check must not be
    # bypassed with interleaved NULs. Selected feeds publish UTF-8 XML.
    if len(body) > 1_000_000 or b"\x00" in body or re.search(br"<!\s*(?:DOCTYPE|ENTITY)", body, re.I):
        raise ValueError("Unsafe or oversized feed")
    try:
        root = ET.fromstring(body)
    except ET.ParseError:
        raise ValueError("Publisher returned invalid XML") from None
    if root.tag == "rss":
        entries = root.findall("./channel/item")
        atom = False
    elif root.tag == "{http://www.w3.org/2005/Atom}feed":
        entries = root.findall("{http://www.w3.org/2005/Atom}entry")
        atom = True
    else:
        raise ValueError("Publisher did not return RSS or Atom")
    posts, seen = [], set()
    for entry in entries[:50]:
        if atom:
            ns = "{http://www.w3.org/2005/Atom}"
            links = entry.findall(ns + "link")
            url = next((e.get("href", "") for e in links if e.get("rel", "alternate") == "alternate"), "")
            title = entry.findtext(ns + "title") or ""
            content = entry.findtext(ns + "content") or entry.findtext(ns + "summary") or ""
            published = entry.findtext(ns + "published") or ""
        else:
            url = entry.findtext("link") or ""
            title = entry.findtext("title") or ""
            content = entry.findtext("{http://purl.org/rss/1.0/modules/content/}encoded") or entry.findtext("description") or ""
            published = entry.findtext("pubDate") or ""
        try:
            url = validate_public_url(url.strip())
            parts = urlsplit(url)
            if parts.hostname != urlsplit(source["url"]).hostname or parts.query or len(url) > 500:
                continue
        except ValueError:
            continue
        if not url or url in seen:
            continue
        seen.add(url)
        posts.append({"url": url, "title": title.strip()[:300], "html": content[:100_000],
                      "published_on": publication_day(published)})
    return posts


def fetch_posts(source):
    response = fetch_source(source["url"], max_bytes=1_000_000,
                            headers={"Accept": "application/rss+xml,application/atom+xml,application/xml"})
    if response.content_type not in {"application/rss+xml", "application/atom+xml", "application/xml", "text/xml"}:
        raise ValueError("Publisher did not return a feed")
    return parse_posts(response.body, source)
