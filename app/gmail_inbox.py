"""Bounded Gmail previews, explicitly linked by the owner; never email transport."""
from datetime import datetime, timezone
from email.header import decode_header, make_header
from email.utils import getaddresses
import html
import re
from urllib.parse import urlencode
from uuid import uuid4

from .communications import contact_exists
from .validation import text_field
from .schema import assert_component_compatible, ensure_component

ROOT = "https://gmail.googleapis.com/gmail/v1/users/me/messages"
DEFAULT_QUERY = "in:inbox newer_than:30d"
ID_PATTERN = re.compile(r"[A-Za-z0-9_-]{1,128}\Z")


def clean(value, limit):
    if not isinstance(value, str):
        raise ValueError("Google returned invalid message metadata")
    return " ".join(html.unescape(value).split())[:limit]


def preview(message, mailbox, message_id):
    if message.get("id") != message_id or not ID_PATTERN.fullmatch(str(message.get("threadId", ""))):
        raise ValueError("Google returned invalid message identity")
    try:
        timestamp = int(message["internalDate"])
        if timestamp < 0:
            raise ValueError()
        received = datetime.fromtimestamp(timestamp / 1000, timezone.utc).isoformat()
        headers = message.get("payload", {}).get("headers", [])
        if not isinstance(headers, list):
            raise ValueError()
        values = {}
        for header in headers:
            name = str(header.get("name", "")).lower()
            if name in {"from", "to", "subject", "date"}:
                if name in values:
                    raise ValueError()
                values[name] = clean(str(make_header(decode_header(header["value"]))), 1000)
    except (ValueError, TypeError, KeyError, AttributeError, OverflowError, OSError, LookupError):
        raise ValueError("Google returned invalid message headers or date") from None
    addresses = getaddresses([values.get("from", "")])
    sender = addresses[0][1].lower() if len(addresses) == 1 else ""
    if not re.fullmatch(r"[^\s@<>]+@[^\s@<>]+\.[^\s@<>]+", sender):
        sender = ""
    return {"mailbox": mailbox, "gmail_id": message_id, "thread_id": message["threadId"],
            "sender": clean(values.get("from", ""), 1000), "sender_email": sender,
            "recipient": clean(values.get("to", ""), 1000),
            "subject": clean(values.get("subject", ""), 500),
            "snippet": clean(message.get("snippet", ""), 1000),
            "received_at": received, "received_ms": timestamp}


def sync_previews(gmail, app, data):
    if set(data) - {"query", "limit", "page_token"}:
        raise ValueError("Unsupported sync field")
    query = text_field(data, "query", 500, required=False) or DEFAULT_QUERY
    limit = data.get("limit", 5)
    if type(limit) is not int or not 1 <= limit <= 10:
        raise ValueError("Sync limit must be an integer between 1 and 10")
    page_token = text_field(data, "page_token", 2048, required=False)
    if not gmail.sync_lock.acquire(blocking=False):
        raise ValueError("A Gmail sync is already running")
    try:
        with gmail.lock:
            generation = gmail.generation
        access = gmail.ensure_access_token()
        profile = gmail.request("https://gmail.googleapis.com/gmail/v1/users/me/profile", token=access)
        if str(profile.get("emailAddress", "")).lower() != gmail.expected_email:
            raise ValueError("The selected mailbox does not match the configured ClubSP mailbox")
        params = {"q": query, "maxResults": limit, "includeSpamTrash": "false"}
        if page_token:
            params["pageToken"] = page_token
        listing = gmail.request(ROOT + "?" + urlencode(params), token=access)
        entries = listing.get("messages", [])
        if not isinstance(entries, list) or len(entries) > limit:
            raise ValueError("Google returned an invalid message list")
        next_token = listing.get("nextPageToken", "")
        if not isinstance(next_token, str) or len(next_token) > 2048:
            raise ValueError("Google returned an invalid page token")
        batch = []
        seen = set()
        for entry in entries:
            message_id = entry.get("id") if isinstance(entry, dict) else None
            if not isinstance(message_id, str) or not ID_PATTERN.fullmatch(message_id) or message_id in seen:
                raise ValueError("Google returned an invalid message identity")
            seen.add(message_id)
            params = [("format", "metadata"), ("fields", "id,threadId,snippet,internalDate,payload/headers")]
            params += [("metadataHeaders", name) for name in ("From", "To", "Subject", "Date")]
            message = gmail.request(ROOT + "/" + message_id + "?" + urlencode(params), token=access)
            batch.append(preview(message, gmail.expected_email, message_id))
        with gmail.lock:
            if generation != gmail.generation or not gmail.path.exists():
                raise ValueError("Connection was removed during sync; no previews saved")
            result = app.save_gmail_previews(batch)
        return {**result, "fetched": len(batch), "next_page_token": next_token,
                "query": query, "sending_enabled": False, "automatic_sync": False}
    finally:
        gmail.sync_lock.release()


class GmailInboxMixin:
    def _initialize_gmail_inbox(self):
        with self.database.session(write=True) as (connection, _):
            assert_component_compatible(connection, "gmail_inbox")
            connection.execute("""
                CREATE TABLE IF NOT EXISTS gmail_previews (
                    id TEXT PRIMARY KEY, mailbox TEXT NOT NULL, gmail_id TEXT NOT NULL,
                    thread_id TEXT NOT NULL, sender TEXT NOT NULL, sender_email TEXT NOT NULL,
                    recipient TEXT NOT NULL, subject TEXT NOT NULL, snippet TEXT NOT NULL,
                    received_at TEXT NOT NULL, received_ms INTEGER NOT NULL,
                    fetched_at TEXT NOT NULL, contact_id TEXT REFERENCES contacts(id),
                    linked_at TEXT, UNIQUE(mailbox,gmail_id)
                );
            """)
            connection.execute("""
                CREATE TABLE IF NOT EXISTS gmail_preview_relationship_links (
                    preview_id TEXT PRIMARY KEY REFERENCES gmail_previews(id),
                    relationship_id TEXT NOT NULL,
                    interaction_id TEXT,
                    linked_at TEXT NOT NULL,
                    imported_at TEXT
                );
            """)
            ensure_component(connection, "gmail_inbox")

    def save_gmail_previews(self, batch):
        inserted = 0
        now = datetime.now(timezone.utc).isoformat()
        with self.database.session(write=True) as (connection, _):
            count = connection.execute("SELECT COUNT(*) FROM gmail_previews").fetchone()[0]
            for item in batch:
                if connection.execute("SELECT id FROM gmail_previews WHERE mailbox=? AND gmail_id=?",
                                      (item["mailbox"], item["gmail_id"])).fetchone():
                    continue
                if count + inserted >= 1000:
                    raise ValueError("Preview storage is full; remove old previews before syncing")
                record = {"id": str(uuid4()), **item, "fetched_at": now}
                connection.execute("INSERT INTO gmail_previews(" + ",".join(record) + ") VALUES(" +
                                   ",".join("?" for _ in record) + ")", tuple(record.values()))
                inserted += 1
        return {"inserted": inserted, "duplicates": len(batch) - inserted}

    def gmail_inbox(self):
        with self.database.session() as (connection, _):
            rows = connection.execute("""SELECT g.*,c.property_id,c.name AS contact_name,
                p.address AS property_address FROM gmail_previews g
                LEFT JOIN contacts c ON c.id=g.contact_id LEFT JOIN properties p ON p.id=c.property_id
                ORDER BY received_ms DESC,g.id LIMIT 100""").fetchall()
            messages = [dict(row) for row in rows]
            has_relationships = connection.execute(
                "SELECT 1 FROM sqlite_master WHERE type='table' AND name='relationship_profiles'"
            ).fetchone()
            profiles = {}
            if has_relationships:
                latest = connection.execute(
                    """SELECT p.* FROM relationship_profiles p
                    JOIN (SELECT relationship_id,MAX(rowid) AS rowid FROM relationship_profiles GROUP BY relationship_id) x
                    ON x.rowid=p.rowid"""
                ).fetchall()
                for row in latest:
                    import json
                    payload = json.loads(row["payload"])
                    profiles[row["relationship_id"]] = {
                        "id": row["relationship_id"], "profile_id": row["id"],
                        "name": payload.get("name", ""), "email": payload.get("email", "").lower(),
                        "status": payload.get("status", ""), "permission": payload.get("permission", ""),
                    }
            for message in messages:
                link = connection.execute(
                    "SELECT * FROM gmail_preview_relationship_links WHERE preview_id=?",
                    (message["id"],),
                ).fetchone()
                message["relationship_id"] = link["relationship_id"] if link else None
                message["relationship_interaction_id"] = link["interaction_id"] if link else None
                message["relationship_linked_at"] = link["linked_at"] if link else None
                message["relationship_imported_at"] = link["imported_at"] if link else None
                profile = profiles.get(message["relationship_id"]) if link else None
                message["relationship_name"] = profile["name"] if profile else None
                message["relationship_candidates"] = [
                    p for p in profiles.values()
                    if message["sender_email"] and p["email"] == message["sender_email"]
                ]
            return {"messages": messages,
                    "total": connection.execute("SELECT COUNT(*) FROM gmail_previews").fetchone()[0]}

    def review_gmail_preview(self, preview_id, data):
        action = text_field(data, "action", 20)
        if action not in {"link", "unlink", "remove"}:
            raise ValueError("Unsupported preview review action")
        with self.database.session(write=True) as (connection, _):
            row = connection.execute("SELECT * FROM gmail_previews WHERE id=?", (preview_id,)).fetchone()
            if row is None:
                raise LookupError("Gmail preview not found")
            if action == "remove":
                relationship_link = connection.execute(
                    "SELECT interaction_id FROM gmail_preview_relationship_links WHERE preview_id=?", (preview_id,)
                ).fetchone()
                if relationship_link and relationship_link["interaction_id"]:
                    raise ValueError("Imported Gmail replies are retained as relationship evidence")
                connection.execute("DELETE FROM gmail_preview_relationship_links WHERE preview_id=?", (preview_id,))
                connection.execute("DELETE FROM gmail_previews WHERE id=?", (preview_id,))
            elif action == "unlink":
                connection.execute("UPDATE gmail_previews SET contact_id=NULL,linked_at=NULL WHERE id=?", (preview_id,))
            else:
                contact = contact_exists(connection, data.get("contact_id"))
                if not row["sender_email"] or contact["email"].lower() != row["sender_email"]:
                    raise ValueError("Choose a contact whose email matches the sender")
                connection.execute("UPDATE gmail_previews SET contact_id=?,linked_at=? WHERE id=?",
                                   (contact["id"], datetime.now(timezone.utc).isoformat(), preview_id))
        return {"action": action, "id": preview_id}
