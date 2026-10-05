# Transaction coordination

ClubSP's transaction file supports Operations 13–15 as an evidence and review workflow. It does **not** sign documents, provide legal advice, clear title, transmit wire instructions, move money, or certify a closing.

## Document versions

Each saved transaction document records:

- document kind and private reference
- exact version/hash reference
- current underwriting and financial-plan IDs
- draft/reviewed/executed/voided status
- signature status separately from document status
- title status separately from signature status
- owner review confirmation
- professional review/title references where required
- effective/expiry dates and notes

Reviewed or executed legal/title documents require a professional review reference. An executed document requires a fully-signed status. A professional title-clearance status requires a separate title review reference.

If underwriting or the financial plan changes, previous documents remain in history but are marked as stale context. They are not reused as current closing evidence.

## Conditions

Contract, title, buyer, funding and closing conditions are append-only revisions. Resolved conditions require evidence; satisfied title conditions additionally require the closing/title professional reference.

Open current conditions block later closing milestones such as ready, signed, funded/disbursed and complete.

## Closing milestones

Closing state is recorded separately:

`scheduled → conditions pending → ready per professional → signed → funded/disbursed → recorded/complete`

The workflow allows a return to conditions-pending when new conditions arise, but it does not allow skipping from signed directly to complete.

- **ready per professional** requires the closing professional reference.
- **signed** requires a fully signed executed transaction document tied to the current underwriting/financial plan.
- **funded/disbursed** requires separate evidence and professional reference.
- **recorded/complete** is another distinct evidence event.

A funded/disbursed event is not itself the ClubSP profit ledger. Actual owner receipts and costs must still be recorded in the ledger and reconciled under Operation 16.

## Professional and payment boundaries

ClubSP stores references to professional review. It does not independently verify title clearance or legal validity.

Never update payment or wire instructions from an email alone. Independently verify payment instructions through a known closing-professional contact before acting.

The app intentionally keeps these concepts separate:

- reviewed document
- signed document
- professional closing readiness
- funded/disbursed closing
- actual cash received by the owner
- reconciled net contribution
