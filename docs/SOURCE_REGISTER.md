# Source Register
Research review date: September 30, 2026.
Sources support the playbook; they do not certify a live campaign or transaction.
All provider costs, licensing and jurisdiction applicability need confirmation
before activation.

| Key | Source | Intended use | Limits/status |
| --- | --- | --- | --- |
| S1 | [Zillow 2025 seller survey](https://www.zillow.com/research/sellers-housing-trends-report-2025/) | Seller priorities, discovery questions, transaction friction | Reviewed; primary-residence seller survey, not distressed-owner predictions |
| S2 | [FTC CAN-SPAM guide](https://www.ftc.gov/business-guidance/resources/can-spam-act-compliance-guide-business) | Commercial email rules and suppression design | Reviewed; campaign-specific applicability still requires review |
| S3 | [FTC Telemarketing Sales Rule guide](https://www.ftc.gov/business-guidance/resources/complying-telemarketing-sales-rule) | Telemarketing/DNC policy research | Reviewed; federal scope/exceptions/state rules need application to actual calls |
| S4 | [FCC unwanted calls/texts guidance](https://www.fcc.gov/consumers/guides/stop-unwanted-robocalls-and-texts) | Channel consent/artificial-voice research | Search guidance located; full live page was inaccessible during research; not marked fully verified |
| S5 | [HUD Fair Housing overview](https://www.hud.gov/helping-americans/fair-housing-act-overview) | Anti-discrimination requirements and targeting review | Reviewed overview; additional state/local requirements still need review |
| S6a | [FRED API documentation](https://fred.stlouisfed.org/docs/api/fred/) | Macro market context adapter | Reviewed documentation entry point; credentials/series/use terms need configuration |
| S6b | [Zillow Research data](https://www.zillow.com/research/data/) | Aggregate local-market context | Reviewed catalog; not a substitute for licensed property comps or bulk listing rights |
| S7 | [Postmark inbound parsing docs](https://postmarkapp.com/developer/user-guide/inbound/parse-an-email) | Example inbound email transport design | Reviewed technical capability only; cold-outreach permission NOT established |
| S8 | [CFPB Closing Disclosure explainer](https://www.consumerfinance.gov/owning-a-home/closing-disclosure/) | Settlement review concepts for applicable loans | Reviewed; loan-specific form/timing must not be applied to every cash/assignment transaction |
| S9 | [Allen County iMap](https://www.acimap.us/) / [parcel layer](https://gis.acimap.us/services/rest/services/CFW/Parcels_With_Ownership_Information/MapServer/0) | Exact parcel identity, reported owner-of-record/site address, transfer date and record year | Implemented bounded adapter; metadata and live no-match protocol checked; positive records require owner identity review; no bulk-export rights assumed |
| S10 | [CFPB loan cost explorer](https://www.consumerfinance.gov/owning-a-home/explore-rates/) | Finance education source check | Page located; not an actual lending quote or loan approval |
| S11 | [SBA planning resources](https://www.sba.gov/counseling/plan-your-business/) | General operations/planning source check | Canonical page located after redirect; business-specific applicability still needs review |

## Runtime knowledge source checks

The first fixed registry checks S1, S2, S6b, S8, S9 metadata, S10, S11 and the
Indiana licensing overview below. It retrieves only those configured endpoints,
without following page links, JavaScript or redirects. Limits: up to eight source
pages per explicit run, twenty attempted knowledge-source requests per business
day, six-second timeout, 1 MiB response cap, twenty-four-hour cache, no automatic
retries. Keep content/section hashes, at most eight title words plus twelve preview
words, reported publication date and actual retrieval/cache time. No full article
or restricted dataset is republished by this workflow.

Public pages were located through research tools. Direct runtime requests to
several pages and the later parcel-metadata check were blocked in this workspace;
they do not count as successful verification. Offline extraction/browser tests
use synthetic pages. Inaccessible sources keep prior notes intact. A successful
page check still requires original-source comparison and an owner-authored note;
compliance requires a professional-review reference. No note changes execution
policy or certifies that all jurisdiction requirements are covered.

## Active parcel adapter

The fixed S9 query endpoint requests only the parcel key and eight selected owner/
site-record fields. Mailing addresses and contact details are not requested.
No credential or paid service is used. Limits: one exact Allen County key per
command, 20 attempts per workspace business day, 24-hour cache, 10-second timeout,
512 KiB maximum response, no redirects or retries. Provider failures are retained
and do not import facts. All returned records require manual identity acceptance;
ambiguous/no-match responses remain unaccepted. Save original URL, retrieval
time, content hash and record reference. Positive-record automated tests are
synthetic, not a completed real-property diligence sample.

This is owner-of-record evidence, not a title search, appraisal, comparable-sale
source, seller-authority verification or contact-consent record. Broader source
rights and geography remain setup work.

## Seller research interpretation

The Zillow 2025 survey reports maximizing profit as the top priority for 58% of
sellers, with target timing at 33%. Of sellers reporting a fallen-through offer,
39% identified money/mortgage/financing issues. These are self-reported findings;
the publication explains that reported offer failures include broader situations
than listings returning from pending status.

The study draws approximately 12,200 responses from over 7,400 unique recent
sellers, April–August 2025, and uses weighting. Its population sold a previous
primary residence. Use it to build discovery questions and hypotheses. Do not
infer an individual seller's urgency, preferred price, vulnerability or financing
situation from these percentages.

## Indiana verification still required

Candidate official materials:
- [Indiana Code Title 32](https://iga.in.gov/laws/2025/ic/titles/32)
- [2024 HB1068 enrolled material](https://iga.in.gov/pdf-documents/123/2024/house/bills/HB1068/HB1068.05.ENRS.pdf)
- [Indiana Real Estate licensing information](https://www.in.gov/pla/professions/real-estate-home/real-estate-licensing-information/)

The code/bill pages did not expose usable full text during this research.
Do not mark exact disclosure text, current amendments, remedies, licensing,
solicitation scope or assignment applicability verified. Obtain and review the
current official text with the appropriate professional before launch.
A historical bill or secondary explanation alone is not an active legal policy.

## Source acquisition backlog

Select actual county assessor/recorder/tax/permit sources for the chosen geography;
verify access and use rights. Select licensed comparable-sale/property providers
after coverage/cost comparison. Verify mailbox API/provider rules, e-sign/title
partners, funding availability, required contact policies and relevant privacy,
recording, advertising, reporting and tax requirements.

No paid service has been purchased or integrated. The bounded public S9 adapter
is active only when explicitly requested. No outreach
has been authorized or sent. Actual API budget and source selection remain setup work.

## Refresh policy

Update Knowledge checks selected sources only after a button command. Capture
publication/effective/retrieval dates, URL, publisher, hash, evidence references,
coverage and review result. Failed/inaccessible checks do not replace active
knowledge or certify freshness. Unresolved legal questions remain blockers.
