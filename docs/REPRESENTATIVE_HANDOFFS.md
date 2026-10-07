# Property representative handoffs

ClubSP supplies acquisition research and workflow. The intended client or buyer and a separately engaged property representative establish representation, fees, authority and transaction responsibilities. A draft packet does not establish that relationship.

From a displayed saved acquisition brief, select one to three property cards, enter the intended client and proposed representative company, optionally enter a business contact and notes, and choose **Prepare representative handoff**. The packet is saved privately and can be previewed or downloaded as UTF-8 text. No source calls, messaging, provider-key exposure, offers, payments or deal creation occur.

The packet contains a frozen copy of the selected cards, criteria, rental economics, source references and retrieval times, limitations, open items and requested work. Missing rent evidence and below-target yields remain explicit. Briefs older than 24 hours are marked for refresh. Source references identify retained evidence without linking to the private owner workspace. Contact details are owner-entered and unverified; no representative is marked licensed, engaged or accepted by this workflow.

Packets are immutable, retained in the additive `representative_handoffs` schema component version 1, and protected from duplicate POSTs by a unique request key and request-body hash. Conflicting reuse is refused; an identical retry returns the same packet. Snapshot digests are checked on read. Existing owner authentication, when configured, protects the endpoints; preparing a packet also requires matching Origin. The existing VPN boundary remains in place. This is not an external partner portal.

## API

- `POST /api/sentras/handoffs/prepare`: `request_key`, `brief_id`, `card_indices`, `client_name`, `representative_company`, optional `representative_contact` and `notes`.
- `GET /api/sentras/handoffs`: latest 20 draft packets.
- `GET /api/sentras/handoffs/{id}`: saved text export and safe filename.

Functional tests cover no-provider execution, selection validation, idempotency, concurrent saves, restart persistence, stale/missing evidence, integrity checks and HTTP Origin enforcement. Desktop/mobile browser tests cover the actual backend, preview, download and reload.

Partnership prospects require separate qualification. A company website describing representation is not a license-registry verification or agreement to partner. Before any transaction, establish the actual client/buyer, funding, authority, representative scope, conflicts and fees. Negotiations and legal commitments must follow the separately agreed mandate and approved terms.
