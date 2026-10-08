# Relationship Desk

Open `/relationships` from the property workspace. Record investors, agents, closing partners, or other relationships before any property exists. This module complements the buyer registry and proposed Commitment Graph; it does not duplicate mandates, budget limits, readiness scores, or property matching. Link an existing buyer to see its current name/status and review quantitative criteria in the buyer registry.

Profiles contain owner-entered identity, source references, qualitative needs, areas of interest, relationship status, permission review, responsible owner, and next action/date. Profile versions and conversation events are append-only SQLite records. UUID retry keys preserve identical retries; changed retries and stale profile/event snapshots are rejected. Nonempty email addresses are normalized and unique across current relationship profiles. Missing email, buying criteria, or funding are not inferred from a name or relationship status.

## Follow-ups

The queue uses the application's Indiana business date. Due and overdue records exclude paused, closed, and blocked relationships. Today's focus includes at most ten due records ordered by due date. A new conversation event replaces the previous follow-up schedule: blank date clears it, and a new dated action explicitly schedules the next step. A later profile revision can update the schedule. Interactions must be recorded in chronological date order; historical evidence can be included as an internal note without pretending to be the newest conversation.

The queue does not run a background job, send notifications, or select recipients for actual transmission. Due records with unknown permission remain research/review tasks, not permission to contact. An owner-reviewed permission reference and email allow an editable template for eligible records. Templates contain no invented transaction, identity, sender signature, financial commitment, or property claim. Use Save message draft to persist edited text; unsaved edits are lost on reload. Saving or approving a draft never sends it.

## Saved drafts and owner review

Each saved message is an immutable version linked to the exact relationship profile and latest conversation event. Its recipient comes from that saved profile, so changing an address cannot silently retarget an old approval. Subject and body are bounded; multiline subjects are rejected. Retry keys prevent duplicate saves and reviews, while expected draft/review IDs reject conflicting edits.

Owner reviews record approved or rejected, reviewer name, notes, and timestamp. Approval concerns exact saved text, not Gmail authorization or recipient permission. Saving another draft requires a new review. Profile edits, new conversations, newer drafts, paused/closed status, or either workspace's email suppression block effective approval. Earlier approval records remain visible as history. Rejected and stale drafts can be rejected again with a current review snapshot, but stale or blocked drafts cannot be approved. Draft versions and reviews persist in two additive SQLite tables. There is no transport or send queue in this release.

## Stops and shared suppression

Stop and wrong-person outcomes, plus recognized incoming stop phrases, append a suppression record. They remove the relationship from follow-up focus and local message templates. Historical versions and conversations remain available. Changing email or editing a profile cannot clear the relationship's stop history.

Existing property contact suppressions are checked by the relationship view. A relationship stop propagates to matching existing property contacts, records their permission events, and voids reply drafts in the same transaction. Property contact creation, permission changes, and draft review also check relationship email suppressions; this covers stops recorded before a property contact exists. A manually selected `blocked` profile is scoped to that relationship and is distinct from an irreversible recorded stop. Stop records have no clearance workflow in this release.

Email connections and Gmail scopes are unchanged. No network calls, email sending, crawling, contact enrichment, proof-of-funds checks, signatures, offers, payments, or stage advances occur. This release is the CRM foundation for a separately implemented, owner-reviewed sending queue.

## Verification and operation

### Current buying requests

Promote only a reviewed, incoming criteria reply to confirmed demand. Public posts and company claims stay findings until that reply exists. The confirmation expires from the conversation date; delayed entry preserves only the remaining window. Funding remains unverified unless separate evidence is recorded.

A new incoming reply enables **Renew from a new buyer reply**. Renewal updates the same buyer, creates a new time-bounded request, and pauses prior requests from this relationship. Prior criteria and confirmation evidence remain in history. The same reply cannot renew itself, and stale forms or changed retry payloads are rejected.

Use the incoming outcome **Buyer paused / withdrew buying request** when the buyer actually stops buying. This pauses the relationship's confirmed requests. A rejection of one conversation does not withdraw all buying demand. A fresh criteria reply can reactivate demand; an enduring contact stop cannot. Paused/closed profiles, blocked permission, and stop/wrong-person records also pause confirmed requests. Unrelated requests belonging to other relationships are preserved.

Each saved request displays current, expired, paused, or blocked. **Current confirmed requests** counts only active, unexpired demand. Expired or paused requests require fresh evidence before renewal; their saved history is never counted as a new confirmation. This does not establish interest in a specific property or an accepted operator handoff.

Run `python -m pytest -q` and `npx playwright test -c playwright.relationships.config.js`. Synthetic tests cover no-property onboarding, retries, stale forms, invalid dates, buyer links, queue ordering, shared suppression, restarts, HTTP origin guards, DOM injection, and desktop/mobile workflows.

The new tables live in the existing SQLite database and therefore follow its backup process. Initializing the book uses transactional `execute` calls, avoiding an implicit commit during memory reload. No existing schema is rewritten. Back up the database before deployment; older backups cannot contain newer relationship history. Production's private Gmail/HTTPS patches must be retained when integrating the additive server routes.
