# Integration Strategy

## Source classes

The integration layer should support interchangeable providers for:

1. Property, parcel, ownership, tax and public-record data
2. Permits, liens and distress indicators where legally available
3. Geocoding and mapping
4. Market and comparable-property data
5. Communication: SMS, voice and email
6. Consent, DNC and compliance checks
7. Recording/transcription
8. E-signature, title and closing workflows
9. Buyer/investor intelligence
10. Web extraction and research
11. AI inference, embeddings and reranking
12. Workflow, queues, scheduling and observability

## Source registry

Each provider adapter should declare its capabilities and constraints rather
than leaking provider-specific details into business logic.

Suggested provider contract:

- lookup
- search
- batch_lookup
- webhook handling
- rate-limit metadata
- estimated cost
- jurisdiction support
- freshness
- provenance output

## MCP strategy

Prefer a small set of internal, auditable MCP tools that wrap external
providers. This keeps credentials, rate limits, compliance, cost controls and
evidence capture in one place.

External MCP servers can be added when they provide a meaningful capability,
but the system should not depend on an uncontrolled collection of servers.

## Apify strategy

Apify should be treated as an extraction/research execution layer.

Good uses include:

- sites without usable APIs
- repeated structured extraction
- browser-based research where permitted
- monitoring pages for changes
- turning semi-structured web sources into normalized evidence

Direct APIs remain preferable when they provide stable, licensed, structured
data for the required use case.

## Cost control

Every provider call should be measurable:

- provider
- operation
- request count
- estimated/actual cost
- latency
- success/failure
- data freshness
- result quality

This enables routing decisions based on capability, reliability, freshness,
legal constraints and cost.
