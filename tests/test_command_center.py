from copy import deepcopy

import pytest

from app.command_center import build_command_center


def fixtures():
    workspace = {
        "opportunities": {"items": [
            {"deal_id": "ready", "opportunity_score": 95, "decision": "owner_review"},
            {"deal_id": "blocked", "opportunity_score": 61, "decision": "research"},
        ]},
        "commitment_graph": {
            "deal_readiness": [
                {"deal_id": "ready", "score": 88, "label": "close-ready"},
                {"deal_id": "blocked", "score": 55, "label": "needs work"},
            ],
            "reverse_opportunities": [{
                "property_id": "candidate-1", "address": "300 Candidate Ave",
                "commitment_match_count": 2, "best_commitment_score": 87,
                "candidate_score": 74,
            }],
        },
    }
    funding = {
        "pipeline": {"shortlist": [
            {"deal_id": "ready", "address": "100 Ready St", "next_action": "Owner review"},
            {"deal_id": "blocked", "address": "200 Blocked St", "next_action": "Refresh evidence"},
        ]},
        "deals": [
            {"id": "ready", "address": "100 Ready St", "ended": False,
             "action_status": "owner_review", "pipeline_next_action": "Owner review",
             "downside_net": 12000, "current_criteria_fit_buyers": 2, "action_items": []},
            {"id": "blocked", "address": "200 Blocked St", "ended": False,
             "action_status": "needs_action", "pipeline_next_action": "Refresh evidence",
             "downside_net": 8000, "current_criteria_fit_buyers": 1,
             "action_items": [{"text": "Refresh evidence"}]},
        ],
    }
    relationships = {
        "daily_focus": ["overdue", "due"],
        "relationships": [
            {"id": "overdue", "blocked": False, "paused": False, "due": True, "overdue": True,
             "follow_up_on": "2026-10-04", "next_action": "Call investor",
             "profile": {"name": "Overdue Investor", "company": "", "buyer_id": "buyer-1"},
             "saved_drafts": []},
            {"id": "due", "blocked": False, "paused": False, "due": True, "overdue": False,
             "follow_up_on": "2026-10-05", "next_action": "Follow up",
             "profile": {"name": "Approved Investor", "company": "Synthetic", "buyer_id": "buyer-2"},
             "saved_drafts": [{"id": "draft-1", "sending_enabled": True}]},
        ],
    }
    return workspace, funding, relationships


def test_command_center_prioritizes_current_revenue_actions_without_inventing_probability():
    workspace, funding, relationships = fixtures()
    result = build_command_center(workspace, funding, relationships)
    assert [item["kind"] for item in result["items"]] == [
        "deal_owner_review", "relationship_follow_up", "approved_outreach",
        "deal_action", "buyer_matched_candidate",
    ]
    ready = result["items"][0]
    assert ready["opportunity_score"] == 95
    assert ready["deal_readiness_score"] == 88
    assert ready["downside_net"] == 12000
    assert result["execution_authorized"] is False
    assert "not a profit/closing probability" in result["scope"]


def test_command_center_excludes_blocked_relationships_and_ended_deals():
    workspace, funding, relationships = fixtures()
    relationships["daily_focus"].insert(0, "blocked-rel")
    relationships["relationships"].append({
        "id": "blocked-rel", "blocked": True, "paused": False, "due": True, "overdue": True,
        "follow_up_on": "2026-10-01", "next_action": "Do not use",
        "profile": {"name": "Suppressed", "company": "", "buyer_id": None},
        "saved_drafts": [{"id": "bad", "sending_enabled": True}],
    })
    funding["pipeline"]["shortlist"].insert(0, {"deal_id": "ended", "address": "Ended", "next_action": "None"})
    funding["deals"].append({
        "id": "ended", "address": "Ended", "ended": True, "action_status": "historical",
        "pipeline_next_action": "None", "downside_net": None, "current_criteria_fit_buyers": 0,
        "action_items": [],
    })
    result = build_command_center(workspace, funding, relationships)
    ids = {item["id"] for item in result["items"]}
    assert "relationship:blocked-rel" not in ids
    assert "deal:ended" not in ids


def test_command_center_is_bounded_and_does_not_mutate_source_states():
    workspace, funding, relationships = fixtures()
    originals = deepcopy((workspace, funding, relationships))
    result = build_command_center(workspace, funding, relationships, limit=2)
    assert len(result["items"]) == 2
    assert (workspace, funding, relationships) == originals
    with pytest.raises(ValueError):
        build_command_center(workspace, funding, relationships, limit=0)
