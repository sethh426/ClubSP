"""Read-only owner focus queue composed from existing ClubSP evidence.

This module does not score profit probability, discover new facts, send messages,
advance deal stages, reserve buyers, or authorize spending. It preserves each
source module's own status and next action.
"""


def build_command_center(workspace, funding_state, relationship_state, limit=12):
    if type(limit) is not int or not 1 <= limit <= 50:
        raise ValueError("limit must be an integer from 1 to 50")

    opportunities = {
        item["deal_id"]: item
        for item in (workspace.get("opportunities") or {}).get("items", [])
    }
    readiness = {
        item["deal_id"]: item
        for item in (workspace.get("commitment_graph") or {}).get("deal_readiness", [])
    }
    focus = []

    for rank, deal in enumerate((funding_state.get("pipeline") or {}).get("shortlist", [])):
        deal_id = deal["deal_id"]
        funding = next((row for row in funding_state.get("deals", []) if row["id"] == deal_id), None)
        if not funding or funding.get("ended"):
            continue
        opportunity = opportunities.get(deal_id)
        close_path = readiness.get(deal_id)
        ready = funding.get("action_status") == "owner_review"
        focus.append({
            "id": "deal:" + deal_id,
            "kind": "deal_owner_review" if ready else "deal_action",
            "source": "Funding Desk + Opportunity Queue + Commitment Graph",
            "title": funding["address"],
            "status": "owner_review" if ready else "needs_action",
            "priority_band": 0 if ready else 3,
            "source_rank": rank,
            "next_action": funding["pipeline_next_action"],
            "href": "/funding",
            "opportunity_score": opportunity.get("opportunity_score") if opportunity else None,
            "opportunity_decision": opportunity.get("decision") if opportunity else None,
            "deal_readiness_score": close_path.get("score") if close_path else None,
            "deal_readiness_label": close_path.get("label") if close_path else None,
            "downside_net": funding.get("downside_net"),
            "criteria_fit_buyers": funding.get("current_criteria_fit_buyers"),
            "blockers": [item["text"] for item in funding.get("action_items", [])],
        })

    relationships = relationship_state.get("relationships", [])
    by_id = {row["id"]: row for row in relationships}
    for rank, rid in enumerate(relationship_state.get("daily_focus", [])):
        row = by_id.get(rid)
        if not row or row.get("blocked") or row.get("paused") or not row.get("due"):
            continue
        drafts = row.get("saved_drafts", [])
        sendable = next((draft for draft in drafts if draft.get("sending_enabled")), None)
        overdue = bool(row.get("overdue"))
        profile = row.get("profile") or {}
        qualification = row.get("qualification")
        if sendable:
            relationship_kind = "approved_outreach"
        elif qualification:
            relationship_kind = "buyer_criteria_reconfirmation"
        elif profile.get("kind") == "investor" and not profile.get("buyer_id"):
            relationship_kind = "buyer_criteria_confirmation"
        else:
            relationship_kind = "relationship_follow_up"
        focus.append({
            "id": "relationship:" + rid,
            "kind": relationship_kind,
            "source": "Relationship Desk",
            "title": row["profile"]["name"] + (
                " · " + row["profile"]["company"] if row["profile"].get("company") else ""
            ),
            "status": "overdue" if overdue else "due",
            "priority_band": 1 if overdue else 2,
            "source_rank": rank,
            "next_action": (
                "Review and explicitly send the current approved draft"
                if sendable else
                "Confirm the investor's current buy box, funding evidence and closing capacity"
                if relationship_kind == "buyer_criteria_confirmation" else
                "Reconfirm the buyer's current criteria before the recorded mandate expires"
                if relationship_kind == "buyer_criteria_reconfirmation" else
                row.get("next_action") or "Review the relationship and record a next action"
            ),
            "href": "/relationships#relationship-" + rid,
            "follow_up_on": row.get("follow_up_on"),
            "buyer_id": profile.get("buyer_id"),
            "qualification_id": qualification.get("id") if qualification else None,
            "mandate_id": qualification.get("mandate_id") if qualification else None,
            "draft_id": sendable.get("id") if sendable else None,
            "blockers": [],
        })

    for rank, candidate in enumerate((workspace.get("commitment_graph") or {}).get("reverse_opportunities", [])):
        focus.append({
            "id": "candidate:" + candidate["property_id"],
            "kind": "buyer_matched_candidate",
            "source": "Commitment Graph + Sourcing",
            "title": candidate["address"],
            "status": "research_candidate",
            "priority_band": 4,
            "source_rank": rank,
            "next_action": "Review source evidence and candidate identity before starting a deal",
            "href": "/#sourcing-workspace",
            "commitment_match_count": candidate.get("commitment_match_count", 0),
            "best_commitment_score": candidate.get("best_commitment_score"),
            "candidate_score": candidate.get("candidate_score"),
            "blockers": [],
        })

    kind_order = {
        "deal_owner_review": 0,
        "approved_outreach": 1,
        "buyer_criteria_confirmation": 2,
        "buyer_criteria_reconfirmation": 3,
        "relationship_follow_up": 4,
        "deal_action": 5,
        "buyer_matched_candidate": 6,
    }
    focus.sort(key=lambda item: (
        item["priority_band"],
        kind_order[item["kind"]],
        item["source_rank"],
        item["title"].casefold(),
        item["id"],
    ))
    focus = focus[:limit]
    return {
        "execution_authorized": False,
        "scope": (
            "Read-only focus queue composed from current recorded ClubSP state. "
            "It is not a profit/closing probability and does not send, offer, spend, "
            "reserve, sign, or advance a deal."
        ),
        "items": focus,
        "summary": {
            "owner_review_deals": sum(item["kind"] == "deal_owner_review" for item in focus),
            "deal_actions": sum(item["kind"] == "deal_action" for item in focus),
            "due_relationships": sum(item["kind"] in {
                "approved_outreach", "buyer_criteria_confirmation",
                "buyer_criteria_reconfirmation", "relationship_follow_up",
            } for item in focus),
            "buyer_criteria_confirmations": sum(item["kind"] == "buyer_criteria_confirmation" for item in focus),
            "buyer_criteria_reconfirmations": sum(item["kind"] == "buyer_criteria_reconfirmation" for item in focus),
            "buyer_matched_candidates": sum(item["kind"] == "buyer_matched_candidate" for item in focus),
            "focus_items": len(focus),
        },
    }
