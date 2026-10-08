# Operator acquisition briefs

The Sentra workspace can build a saved Fort Wayne single-family rental shortlist in one click. The visible defaults are screening assumptions, not an operator's verified buying commitment. Criteria and the completed result persist in SQLite, with the latest 20 briefs available in the workspace.

A build requests at most three active sale listings and one address-verified rent estimate for each qualifying listing: at most four RentCast requests. All calls use the existing shared monthly cap, cache, source health, credential guard and durable request reservations. Identical criteria reuse their completed brief for 24 hours, including incomplete results. A concurrent duplicate build is refused; a stopped worker can resume after 15 minutes using the same child request keys. No scheduler is added for paid searches.

Only active Fort Wayne, Indiana single-family records with known positive asking prices within the ceiling and known bedroom counts meeting the minimum are screened. Missing criteria exclude records. Missing or inconsistent rent evidence produces an unscored card; it never becomes zero rent or a fabricated estimate. The three-record sample may contain no qualifying properties and is not the whole market. RentCast is the source, not a direct ClubSP MLS connection.

## Economics

Cash basis = asking price × (1 + closing percentage) + repair reserve.

Annual operating income = lower provider rent range × 12 × (1 − vacancy percentage) × (1 − expense percentage).

Operating yield = annual operating income ÷ cash basis. The expense percentage is an assumed allowance for taxes, insurance, maintenance and management. These costs, property condition, future capital work, listing availability, restrictions and title require operator verification. Debt, income taxes and resale proceeds are excluded. This is neither leveraged cash flow nor projected transaction profit. The provider range is not a confidence interval or rent guarantee.

Each card includes asking price, property attributes, rent estimate/range, assumed cash basis and yield, screening outcome and specific operator checks. Saved source-run links preserve retrieval time and evidence. The copy button creates an operator summary including assumptions, limitations and provenance.

## Interfaces and validation

- `GET /api/sentras/briefs`: latest 20 saved briefs plus defaults.
- `POST /api/sentras/briefs/build`: criteria only; requires existing owner authentication when configured and a matching Origin.
- `acquisition_briefs` schema component version 1: additive table and index.
- Functional tests exercise actual collectors with offline provider transport, cash math, invalid criteria, identity mismatches, quota exhaustion, concurrent requests and restart recovery.
- Desktop/mobile browser tests use the actual HTTP application with an explicitly injected offline provider fixture.

The feature does not create verified buyer demand, contact agents, make offers, create deals or close transactions. Commercial validation still requires a real operator to confirm that the brief meets their criteria and saves useful underwriting time. Operator distribution and paid billing are separate work after this feature is operational.
## Decision guidance

Each card explains whether it needs rent evidence, a positive yield target, a lower price, or representative review. The asking-price ceiling solves `(annual operating income / target yield - repair reserve) / (1 + closing-cost rate)`, caps it at the configured budget, and rounds down to whole dollars. Required price reduction rounds up. It uses income rather than the rounded display yield. Monthly operating income excludes debt and income tax.

Saved briefs receive the same calculations when read; this requires no new provider calls or schema changes. New representative drafts retain the calculation in their immutable snapshot and export. Existing packets retain their original snapshot. Daily automation and default property selections use the decision classification, so a rounded yield or zero target cannot automatically qualify a property.

These calculations apply to the saved assumptions and provider estimates. They are not buyer demand, a market valuation, an offer, or transaction profit.

## Guided workspace

The Sentra landing view explains the buyer → research → representative workflow in plain language. It shows one next step, saved intended parties (explicitly unverified), the displayed brief's review count and freshness, and daily research status. Existing research/settings controls live in a collapsed secondary workspace. The next-step button opens and focuses the relevant control; it never submits a form or triggers a provider request.

Client and representative details can be entered before any brief exists, then explicitly saved using the existing daily automation settings. Editing a form is not a saved profile or verified engagement. Unsaved edits, paused cycles and unavailable settings remain explicit. Scheduled research does not require a daily button press; draft packets still do not send themselves or establish representation. This UI change adds no database tables, schedules, permissions or external actions.

Saved contacts are read from the existing relationship desk. Selecting an investor copies their name/company into intended-client draft details; selecting an agent copies company/name and recorded business email into proposed-representative details. Blocked and paused records are excluded. No contact is automatically selected or promoted to verified buyer demand, buying criteria are unchanged, and nothing is sent or saved until the existing explicit-save action. The contact review link leads to the exact relationship record, where criteria and contact permissions can be reviewed. If contact loading fails, manual entry remains available.
