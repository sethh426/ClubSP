"""Explicit, owner-approved Gmail delivery for reviewed relationship drafts."""
from __future__ import annotations

import base64
from email.message import EmailMessage
from email.policy import SMTP
import re

SEND_URL = "https://gmail.googleapis.com/gmail/v1/users/me/messages/send"
ID_RE = re.compile(r"^[A-Za-z0-9_-]{1,256}$")


def encoded_message(sender, recipient, subject, body):
    message = EmailMessage(policy=SMTP)
    message["From"] = sender
    message["To"] = recipient
    message["Subject"] = subject
    message.set_content(body)
    return base64.urlsafe_b64encode(message.as_bytes()).decode().rstrip("=")


def send_approved_draft(gmail, relationships, relationship_id, data):
    """Send exactly one current approved draft; never retry an ambiguous provider result."""
    token = gmail.ensure_access_token()
    reserved = relationships.reserve_send(relationship_id, data, gmail.expected_email)
    send = reserved["send"]
    if not reserved["created"]:
        return send
    raw = encoded_message(send["sender"], send["recipient"], send["subject"], send["body"])
    try:
        response = gmail.request(SEND_URL, token=token, json_body={"raw": raw})
        message_id = response.get("id")
        thread_id = response.get("threadId")
        if not isinstance(message_id, str) or not ID_RE.fullmatch(message_id):
            raise ValueError("Invalid Gmail message identity")
        if not isinstance(thread_id, str) or not ID_RE.fullmatch(thread_id):
            raise ValueError("Invalid Gmail thread identity")
    except Exception:
        relationships.record_send_result(
            send["id"], "unknown",
            note="Gmail delivery result is unknown. Verify the Sent mailbox before any further outreach.",
        )
        raise ValueError(
            "Gmail delivery result is unknown. Check the Sent mailbox before creating any new outreach."
        ) from None
    return relationships.record_send_result(
        send["id"], "sent", provider_message_id=message_id, provider_thread_id=thread_id,
        note="Gmail confirmed the message and thread identifiers.",
    )
