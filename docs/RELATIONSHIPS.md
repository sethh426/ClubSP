# Relationship Desk

Open `/relationships` from the property workspace. Record investors, agents, closing partners, or other relationships before any property exists. This module complements the buyer registry and proposed Commitment Graph; it does not duplicate mandates, budget limits, readiness scores, or property matching. Link an existing buyer to see its current name/status and review quantitative criteria in the buyer registry.

Profiles contain owner-entered identity, source references, qualitative needs, areas of interest, relationship status, permission review, responsible owner, and next action/date. Profile versions and conversation events are append-only SQLite records. UUID retry keys preserve identical retries; changed retries and stale profile/event snapshots are rejected. Nonempty email addresses are normalized and unique across current relationship profiles. Missing email, buying criteria, or funding are not inferred from a name or relationship status.

## Follow-ups

The queue uses the application's Indiana business date. Due and overdue records exclude paused, closed, and blocked relationships. Today's focus includes at most ten due records ordered by due date. A new conversation event replaces the previous follow-up schedule: blank date clears it, and a new dated action explicitly schedules the next step. A later profile revision can update the schedule. Interactions must be recorded in chronological date order; historical evidence can be included as an internal note without pretending to be the newest conversation.

The queue does not run a background job, send notifications, or select recipients for actual transmission. Due records with unknown permission remain research/review tasks, not permission to contact. An owner-reviewed permission reference and email allow a local editable template for eligible records. Templates contain no invented transaction, identity, sender signature, financial commitment, or property claim. Edits in the template fields are not persisted or sent.

## Stops and shared suppression

Stop and wrong-person outcomes, plus recognized incoming stop phrases, append a suppression record. They remove the relationship from follow-up focus and local message templates. Historical versions and conversations remain available. Changing email or editing a profile cannot clear the relationship's stop history.

Existing property contact suppressions are checked by the relationship view. A relationship stop propagates to matching existing property contacts, records their permission events, and voids reply drafts in the same transaction. Property contact creation, permission changes, and draft review also check relationship email suppressions; this covers stops recorded before a property contact exists. A manually selected `blocked` profile is scoped to that relationship and is distinct from an irreversible recorded stop. Stop records have no clearance workflow in this release.

Email connections and Gmail scopes are unchanged. No network calls, email sending, crawling, contact enrichment, proof-of-funds checks, signatures, offers, payments, or stage advances occur. This release is the CRM foundation for a separately implemented, owner-reviewed sending queue.

## Verification and operation

Run `python -m pytest -q` and `npx playwright test -c playwright.relationships.config.js`. Synthetic tests cover no-property onboarding, retries, stale forms, invalid dates, buyer links, queue ordering, shared suppression, restarts, HTTP origin guards, DOM injection, and desktop/mobile workflows.

The new tables live in the existing SQLite database and therefore follow its backup process. Initializing the book uses transactional `execute` calls, avoiding an implicit commit during memory reload. No existing schema is rewritten. Back up the database before deployment; older backups cannot contain newer relationship history. Production's private Gmail/HTTPS patches must be retained when integrating the additive server routes.
