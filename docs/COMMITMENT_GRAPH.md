# Commitment Graph and Deal Readiness Engine

ClubSP's differentiator is buyer-first execution:

> Don't find deals. Get deals you can actually close.

The Commitment Graph records *who will buy what*, *which capital is actually
available*, and *what happened after prior matches*. The Deal Readiness Engine
then turns those records into an explainable operational score and next-action
list. It intentionally does **not** present the score as a closing probability,
valuation, lending approval, legal conclusion, or investment recommendation.

## Implemented in this release

The backend now stores:

- buyer mandates: standing buy criteria tied to an existing Buyer record,
  verification evidence and optional expiration;
- capital profiles: partner/lender/self/other availability with market/strategy
  scope, maximum commitment, currently available amount, verification evidence
  and optional expiration;
- commitment outcomes: closed/failed/declined paths tied back to the deal,
  buyer and/or capital profile;
- per-deal readiness: demand commitment (35 points), deal evidence (20), capital
  path (20), transaction progress (15), and network outcome evidence (10);
- reverse opportunity search: reviewed sourcing candidates are ranked against
  current standing mandates before a deal is created;
- audited commitment lifecycle events: mandates/capital can be paused, reactivated,
  marked unverified or expired without erasing the prior state transition;
- freshness fingerprints: stored buyer matches are invalidated when underwriting,
  deal terms, source evidence, strategy, market, or buyer criteria change;
- descriptive reliability intelligence: buyer, mandate, and capital histories count
  recorded closes/failures without presenting them as calibrated probabilities;
- exact outcome context: outcomes can preserve the specific buyer mandate and
  buyer-match snapshot that produced the path;
- provider-neutral sourcing intents generated from active standing buyer demand;
- richer reviewed candidate evidence including optional asking price, beds, baths,
  square footage, and year built.

The complete application state exposes these under `commitment_graph`.
A deal can also be rescored directly with:

`POST /api/deals/{deal_id}/readiness` with an empty JSON object.

New records can be entered through:

- `POST /api/commitments/buyer-mandates`
- `POST /api/commitments/capital`
- `POST /api/commitments/outcomes`

The score is calculated from recorded evidence only. Missing evidence lowers
readiness and appears as a blocker/next action instead of being silently guessed.

## Dependency decision

The core Commitment Graph still adds **zero mandatory runtime dependencies**.
That is deliberate: ClubSP is currently a small SQLite/stdlib application with
strong audit semantics. Buyer/capital relationships are easily represented by
foreign keys and JSON criteria. A graph database would add operating complexity
before the relationship volume or query shape justifies it.

The researched libraries are now staged as optional extras in `pyproject.toml`:

- `pip install .[integrations]` for HTTP/provider validation and fuzzy-review tools;
- `pip install .[scale]` for the later production database/migration/job-queue path.

Nothing in the current local app imports those packages yet, so installing ClubSP
normally keeps the existing zero-dependency runtime.

Add dependencies only at the integration boundary:

| Need | Recommended dependency | Why / when |
| --- | --- | --- |
| External property/provider HTTP calls | `httpx` | Connection pooling plus explicit connect/read/write/pool timeouts. Add when the second live provider is connected. |
| Strict third-party response contracts | `pydantic` | Validate provider payloads before evidence enters domain logic. Add with external provider adapters, not core scoring. |
| Entity/address duplicate review | `rapidfuzz` | Candidate similarity ranking for human-reviewed dedupe. Never silently merge records from a fuzzy score. |
| Schema migrations / PostgreSQL path | `SQLAlchemy` + `Alembic` | Add when ClubSP moves beyond the current single-user SQLite snapshot strategy. |
| Autonomous/background work | `RQ` + Redis/Valkey | Queue ingestion, refreshes, retries and scheduled matching once continuous automation is enabled. |

### Why not Neo4j yet?

"Commitment Graph" describes the product model, not a requirement for a graph
database. The initial high-value queries are bounded: active mandates for an
eligible buyer, capital paths for a deal, and historical outcomes. SQLite now
and PostgreSQL later can answer these efficiently with indexes while preserving
the existing transaction model. Reconsider a graph database only if multi-hop
relationship traversal itself becomes a demonstrated performance/product need.

## External property-data candidates

The provider layer should remain adapter-based and preserve source identity,
retrieval time, evidence hash, licensing constraints and owner review.

Good candidates for the next pilot:

1. **RentCast** — nationwide property records plus sale/property filtering that
   can support property identity and comparable-sale discovery.
2. **ATTOM** — broad property, assessor/recorder, ownership, transaction and
   valuation-related datasets with an established property API.
3. **RealEstateAPI** — property/MLS/ownership/tax/mortgage-oriented APIs and
   advanced search suitable for lead discovery.

Do not let any provider value silently become "truth." Store provider name,
record identifier, retrieval time, raw/normalized evidence and confidence, then
run ClubSP's existing review/invalidation rules.

## Next engineering sequence

1. Add a provider-neutral HTTP adapter that consumes the generated demand-first
   search intents, then connect one property-data source behind explicit credentials.
2. Add provider response contracts and evidence normalization before any external
   field can influence reverse matching or underwriting.
3. Extend standing mandates with optional beds/baths/square-footage/year-built
   filters once provider field normalization is proven.
4. Move repeated provider refreshes and reverse matching into a job queue only
   after the manual/provider pilot is reliable.
5. Calibrate any future probability model only from a sufficiently large labeled
   outcome set; until then, keep readiness deterministic and reliability descriptive.

## Product rule

A high score must always be inspectable. Every point should be traceable to a
recorded buyer commitment, underwriting/plan artifact, capital record, workflow
stage or historical outcome. ClubSP wins by knowing the path to close before
spending time on the opportunity.
