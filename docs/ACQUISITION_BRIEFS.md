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
