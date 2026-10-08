# Buyer research

Open `/buyer-intent` from the property research page. Public evidence stays separate from qualified buyers and executable search mandates.

The CLI starts a background worker that checks three selected official company pages at most once per 24 hours: Simple Quarters, Buy My House Indiana and Indiana Home Solutions LLC. The worker saves a short buying-claim excerpt and page-level market mentions. It does not assert funds, numeric buying criteria, willingness to buy sourced deals, or current acquisition capacity. Pause/resume is persisted. Programmatic test servers do not start the worker.

The same worker now discovers posts from Indiana Home Solutions LLC's publisher-owned RSS feed. No additional setup is required. It parses at most 50 entries per bounded one-megabyte feed response, retains same-publisher HTTPS links, and saves short local buying claims with their post title, original link, feed provenance and publication date. Generic education, third-party references and claims outside the selected local markets are skipped. Publisher marketing remains a company claim, never a qualified buyer request. Discovery run counts describe posts examined/retained/skipped, not new buyers or confirmed demand; repeat scans can retain the same post without duplicating its evidence.

RSS/Atom feeds reject DTD/entity declarations and malformed XML. The existing public-source transport pins public DNS, rejects private targets and redirects, and applies byte/time limits. Atom update timestamps are not treated as publication dates. The new `buyer_post_discovery` schema component registers the durable run ledger without changing or erasing existing buyer evidence. Failures retain saved findings and are shown alongside source status; pause and persisted daily attempts cover the feed too. Social API approval is a separate prerequisite; there is no unauthenticated social scraper or login workaround.

Posts and acquisition findings can be captured with an author, HTTPS URL, relevant text and optional publication date. Capture does not fetch a submitted URL. Classification distinguishes self-reported buying intent, company buying claims, acquisition history, buyer solicitation and unclear intent. Classification is a conservative rule-based aid for human review, not verified demand. Some phrasing will remain unclear; mixed claims need source review.

Publication date is separate from observation time. Unknown dates stay unknown, including newly checked company pages. Dated buying statements rank first when published within 30 days; undated/older statements follow, then company claims and other findings. Acquisition history never supplies a current mandate. Criteria remain source text rather than silently becoming property search settings.

Signals deduplicate normalized URLs while retaining identity query parameters. Tracking parameters and fragments are removed. Use a distinct comment URL for a distinct author; a different author on an existing URL is rejected. Changed evidence creates an immutable observation; the UI shows five recent versions. Refresh retains dismissed/shortlisted status and prior observations. Attempts and failures are durable, including across restart; unchanged pages do not duplicate observations. Failure does not advance last successful check or erase evidence.

Preparing a buyer prospect records a source-linked investor relationship with unknown contact permission and prospect status. It does not create a buyer, mandate, outgoing message or paid provider request. Repeated requests reuse the prepared relationship. An existing exact name/company is surfaced for review without overwriting that relationship. The existing relationship desk still requires current incoming buying criteria and explicit confirmation for buyer qualification.

## HTTP surface

- `GET /api/buyer-intent`: signals, summary, source status and coverage.
- `POST /api/buyer-intent`: capture `{name,url,kind,text,published_on?}`.
- `POST /api/buyer-intent/refresh`: empty object; checks due catalog sources only.
- `POST /api/buyer-intent/settings`: `{enabled: boolean}`.
- `POST /api/buyer-intent/{id}/review`: `{status: new|shortlisted|dismissed}`.
- `POST /api/buyer-intent/{id}/relationship`: empty object; prepares a prospect or returns possible existing relationship IDs.

All mutations use the workspace origin and existing owner authentication contract. The monitor fetches only hardcoded reviewed HTTPS pages, rejects redirects, uses a ten-second timeout and one-megabyte decoded response ceiling, and executes no remote JavaScript. It does not automatically search social platforms or bypass login/access restrictions. No paid data APIs are called. Additive schema component `buyer_intent` version 1 preserves existing operational data.

Tests use synthetic evidence and offline transports. Live verification checks source responses and saved queue state; it does not contact prospects or spend property-provider requests.
