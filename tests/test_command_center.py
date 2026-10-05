from app.command_center import build_command_center


class FakeApplication:
    def __init__(self):
        self._state = {
            "opportunities": {"items": [
                {"decision":"owner_review","address":"10 Ready St","opportunity_score":92,
                 "score_type":"evidence-and-readiness score; not a probability of profit or closing",
                 "next_action":"Owner review terms","deal_id":"deal-ready","property_id":"prop-ready"},
                {"decision":"research","address":"20 Research St","opportunity_score":61,
                 "next_action":"Refresh one comparable","deal_id":"deal-research","property_id":"prop-research"},
            ]},
            "discovery": {"items": [
                {"decision":"research_candidate","address":"30 Candidate St","property_id":"prop-candidate",
                 "score":80,"commitment_match_count":2},
            ]},
            "commitment_graph": {"search_intents": [
                {"intent_id":"intent-1","market":"Fort Wayne, IN"},
            ]},
            "provider_integrations": {"search_queue": [
                {"action":"search","search_intent_ids":["intent-1"],"demand_count":3,
                 "available_reservation_slots":2,"budget_priority_score":88,"provider_id":"rentcast"},
            ]},
        }
        self._inbox = {"messages": [
            {"id":"preview-1","sender_email":"reply@example.test","relationship_id":"rel-reply",
             "relationship_candidates":[{"id":"rel-reply"}],"relationship_interaction_id":None,
             "received_at":"2026-10-05T12:00:00Z"},
        ]}

    def state(self):
        return self._state

    def gmail_inbox(self):
        return self._inbox


class FakeRelationships:
    def state(self):
        return {"relationships": [
            {"id":"rel-reply","profile":{"name":"Reply Person"},"due":True,"overdue":True,
             "next_action":"Old follow-up","follow_up_on":"2026-10-04","saved_drafts":[
                 {"id":"draft-reply","sending_enabled":True},
             ]},
            {"id":"rel-send","profile":{"name":"Send Person"},"due":True,"overdue":False,
             "next_action":"Send approved note","follow_up_on":"2026-10-05","saved_drafts":[
                 {"id":"draft-send","sending_enabled":True},
             ]},
            {"id":"rel-follow","profile":{"name":"Follow Person"},"due":True,"overdue":False,
             "next_action":"Call about criteria","follow_up_on":"2026-10-05","saved_drafts":[]},
        ]}


def test_daily_money_center_prioritizes_and_deduplicates_existing_actions():
    result = build_command_center(FakeApplication(), FakeRelationships())
    kinds = [action["kind"] for action in result["actions"]]
    assert kinds == [
        "review_reply", "send_approved", "relationship_follow_up",
        "deal_owner_review", "deal_research", "candidate_review", "buyer_demand_search",
    ]
    assert result["actions"][0]["relationship_id"] == "rel-reply"
    assert result["actions"][1]["relationship_id"] == "rel-send"
    assert result["actions"][2]["relationship_id"] == "rel-follow"
    assert result["counts"]["review_reply"] == 1
    assert result["counts"]["send_approved"] == 1
    assert result["total_actions"] == 7
    assert result["execution_authorized"] is False
    assert all("next_action" in action and "why" in action for action in result["actions"])


def test_reply_without_unique_relationship_match_is_not_promoted():
    app = FakeApplication()
    app._inbox["messages"][0].update(
        relationship_id=None,
        relationship_candidates=[{"id":"a"},{"id":"b"}],
    )
    result = build_command_center(app, FakeRelationships())
    assert "review_reply" not in [action["kind"] for action in result["actions"]]
    # The relationship's ordinary due/send state remains visible instead of guessing the recipient mapping.
    assert any(action.get("relationship_id") == "rel-reply" for action in result["actions"])


def test_command_center_caps_display_but_reports_total():
    app = FakeApplication()
    app._state["discovery"]["items"] = [
        {"decision":"research_candidate","address":f"{i} Candidate St","property_id":f"p-{i}",
         "score":i,"commitment_match_count":1} for i in range(30)
    ]
    result = build_command_center(app, FakeRelationships())
    assert len(result["actions"]) == 20
    assert result["total_actions"] > len(result["actions"])
