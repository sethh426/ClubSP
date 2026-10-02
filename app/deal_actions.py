"""Combine recorded reviews into an advisory action list without changing deal state."""


def attach_actions(funding_deals, workspace):
    opportunities = workspace.get("opportunities")
    items = {item["deal_id"]: item for item in (opportunities or {}).get("items", [])}
    source = {deal["id"]: deal for deal in workspace["deals"]}
    for row in funding_deals:
        deal = source[row["id"]]
        opportunity = items.get(row["id"])
        plan = deal["finance"]["plan"]
        reconciliation = deal["finance"]["reconciliation"]
        gate = "completed" if row["stage"] == "closing" else "closing" if row["stage"] in {"contracted", "disposition"} else "contracted"
        tasks = [task for task in deal["tasks"] if task["status"] == "open"]
        tasks.sort(key=lambda task: (not task["overdue"], not bool(task["due_on"]), task["due_on"], task["title"], task["id"]))
        actions = []
        if row["ended"]:
            if not reconciliation or not reconciliation["current"]:
                actions.append({"category": "reconciliation", "text": "Reconcile the ended deal against its current recorded cash ledger"})
        else:
            if opportunity:
                actions.extend({"category": "opportunity", "text": reason} for reason in opportunity["reasons"])
            elif row["stage"] not in {"contracted", "disposition", "closing"}:
                actions.append({"category": "evidence", "text": "Review current property evidence, buyer fit, and risk policy in the property workspace; opportunity checks are unavailable"})
            actions.extend({"category": "funding", "text": reason} for reason in row["blockers"])
            actions.extend({"category": "task", "text": task["title"], "task_id": task["id"]}
                           for task in tasks if task["blocking_stage"] in {gate, "any"} or task["overdue"])
        seen = set()
        actions = [action for action in actions if not (action["text"] in seen or seen.add(action["text"]))]
        row.update(action_items=actions, open_tasks=tasks, next_stage_review=gate,
                   opportunity_decision=opportunity["decision"] if opportunity else None,
                   opportunity_checks_available=opportunity is not None,
                   current_criteria_fit_buyers=opportunity["current_criteria_fit_buyers"] if opportunity else None,
                   downside_net=plan["forecasts"]["downside"]["net_contribution"] if plan else None,
                   unrecovered_cash=deal["finance"]["summary"]["unrecovered_cash"],
                   reconciled_net=reconciliation["actual_net"] if reconciliation and reconciliation["current"] else None,
                   action_status="historical" if row["ended"] else "needs_action" if actions else "owner_review",
                   pipeline_next_action=actions[0]["text"] if actions else
                       "Review the closing file with the funder and closing professional" if not row["ended"] else "Current ledger reconciliation recorded")
    # Owner-review candidates first, then actionable work; unknown forecasts remain unknown.
    funding_deals.sort(key=lambda row: (row["ended"], row["action_status"] != "owner_review",
        row["opportunity_decision"] == "outside_buy_box", row["downside_net"] is None,
        -(row["downside_net"] or 0), row["address"].casefold(), row["id"]))
    return {"execution_authorized": False,
            "opportunity_integration_available": opportunities is not None,
            "owner_review_candidates": sum(row["action_status"] == "owner_review" for row in funding_deals),
            "action_count": sum(len(row["action_items"]) for row in funding_deals),
            "shortlist": [{"deal_id": row["id"], "address": row["address"],
                           "next_action": row["pipeline_next_action"]}
                          for row in funding_deals if not row["ended"]][:5]}
