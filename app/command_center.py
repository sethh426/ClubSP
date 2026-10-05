"""Read-only daily priority aggregation for ClubSP's existing money workflow."""
from __future__ import annotations


PRIORITY = {
    "review_reply": 10,
    "send_approved": 20,
    "relationship_follow_up": 30,
    "deal_owner_review": 40,
    "deal_research": 50,
    "candidate_review": 60,
    "buyer_demand_search": 70,
}


def _action(kind, title, why, next_action, href, **extra):
    return {
        "kind": kind,
        "priority": PRIORITY[kind],
        "title": title,
        "why": why,
        "next_action": next_action,
        "href": href,
        "external_action": kind in {"send_approved", "buyer_demand_search"},
        **extra,
    }


def build_command_center(application, relationships):
    """Aggregate existing state only; never execute, send, spend, or infer new facts."""
    state = application.state()
    relationship_state = relationships.state()
    inbox = application.gmail_inbox()
    actions = []
    reply_relationships = set()
    send_relationships = set()

    for message in inbox.get("messages", []):
        if message.get("relationship_interaction_id"):
            continue
        rid = message.get("relationship_id")
        candidates = message.get("relationship_candidates") or []
        if not rid and len(candidates) != 1:
            continue
        rid = rid or candidates[0]["id"]
        reply_relationships.add(rid)
        sender = message.get("sender_email") or message.get("sender") or "Gmail sender"
        actions.append(_action(
            "review_reply",
            "Review Gmail reply from " + sender,
            "A saved Gmail preview has exactly one current Relationship Desk match and has not been imported.",
            "Open the original Gmail message, confirm its meaning, then import the reviewed outcome.",
            "#gmail-workspace",
            relationship_id=rid,
            gmail_preview_id=message.get("id"),
            received_at=message.get("received_at"),
        ))

    for record in relationship_state.get("relationships", []):
        rid = record["id"]
        if rid in reply_relationships:
            continue
        ready = next((draft for draft in record.get("saved_drafts", []) if draft.get("sending_enabled")), None)
        if ready:
            send_relationships.add(rid)
            actions.append(_action(
                "send_approved",
                "Approved email ready for " + record["profile"]["name"],
                "The exact saved draft has a current approval and the relationship is currently eligible.",
                "Recheck the recipient and exact text, then explicitly authorize the Gmail send.",
                "/relationships#relationship-" + rid,
                relationship_id=rid,
                draft_id=ready["id"],
            ))

    for record in relationship_state.get("relationships", []):
        rid = record["id"]
        if not record.get("due") or rid in reply_relationships or rid in send_relationships:
            continue
        actions.append(_action(
            "relationship_follow_up",
            "Follow up with " + record["profile"]["name"],
            "The recorded next-action date is due" + (" and overdue." if record.get("overdue") else "."),
            record.get("next_action") or "Review the relationship and record the next action.",
            "/relationships#relationship-" + rid,
            relationship_id=rid,
            due_on=record.get("follow_up_on"),
        ))

    for item in state.get("opportunities", {}).get("items", []):
        if item.get("decision") not in {"owner_review", "research"}:
            continue
        score = item.get("opportunity_score", 0)
        kind = "deal_owner_review" if item["decision"] == "owner_review" else "deal_research"
        actions.append(_action(
            kind,
            ("Owner review: " if kind == "deal_owner_review" else "Advance evidence: ") + item.get("address", "Deal"),
            ("Evidence/readiness score %s/100. " % score)
            + (item.get("score_type") or "This score is readiness evidence, not a closing probability."),
            item.get("next_action") or "Review current evidence and blockers.",
            "#opportunity-title",
            deal_id=item.get("deal_id"),
            property_id=item.get("property_id"),
            readiness_score=score,
        ))

    for item in state.get("discovery", {}).get("items", []):
        if item.get("decision") != "research_candidate":
            continue
        actions.append(_action(
            "candidate_review",
            "Review candidate " + item.get("address", ""),
            "%s current standing buyer mandate(s) fit this research candidate." % item.get("commitment_match_count", 0),
            "Review source identity and evidence before creating or advancing a deal.",
            "#sourcing-panel",
            property_id=item.get("property_id"),
            candidate_score=item.get("score", 0),
            buyer_match_count=item.get("commitment_match_count", 0),
        ))

    graph = state.get("commitment_graph") or {}
    intents = {item.get("intent_id"): item for item in graph.get("search_intents", [])}
    for item in (state.get("provider_integrations") or {}).get("search_queue", []):
        if item.get("action") not in {"search", "preflight", "refresh_search"}:
            continue
        intent = intents.get((item.get("search_intent_ids") or [None])[0]) or {}
        actions.append(_action(
            "buyer_demand_search",
            "Run buyer-demand search" + (": " + intent["market"] if intent.get("market") else ""),
            "%s demand path(s), %s open buyer slot(s), budget priority %s/100."
            % (item.get("demand_count", 0), item.get("available_reservation_slots", 0), item.get("budget_priority_score", 0)),
            "Review the provider, request cap, and buyer-derived criteria; explicitly authorize the search if still appropriate.",
            "#commitment-title",
            search_intent_ids=item.get("search_intent_ids", []),
            provider_id=item.get("provider_id"),
            budget_priority_score=item.get("budget_priority_score", 0),
        ))

    def sort_key(item):
        secondary = -(
            item.get("readiness_score")
            or item.get("buyer_match_count")
            or item.get("budget_priority_score")
            or item.get("candidate_score")
            or 0
        )
        return item["priority"], secondary, item["title"].casefold()

    actions.sort(key=sort_key)
    counts = {kind: sum(action["kind"] == kind for action in actions) for kind in PRIORITY}
    return {
        "generated_from_current_state": True,
        "execution_authorized": False,
        "scope": "Read-only prioritization of recorded ClubSP state; no send, spend, offer, provider request, or transaction is executed.",
        "actions": actions[:20],
        "counts": counts,
        "total_actions": len(actions),
        "top_action": actions[0] if actions else None,
    }
