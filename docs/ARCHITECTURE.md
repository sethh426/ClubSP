# ClubSP Architecture

ClubSP is designed as a provider-agnostic Wholesale Autopilot.

## 1. Operational world model

Core operational entities:

- Property
- Owner
- Lead
- Underwriting
- Communication
- Conversation
- Offer
- Contract
- TitleCase
- Buyer
- BuyerMatch
- Exception
- WorkflowEvent

## 2. Memory and learning

Memory is separate from operational state:

- SourceRecord
- Fact
- Observation
- Prediction
- LearningRecord
- ModelVersion

The LLM or other model is a reasoning component, not the database of record.

## 3. Capability/control plane

MCP should act as a bounded capability layer between reasoning and external systems.

Representative internal tools:

- source.search
- source.fetch
- property.lookup
- owner.lookup
- market.lookup
- memory.retrieve
- memory.record_fact
- memory.record_prediction
- memory.resolve_outcome
- underwriting.run
- compliance.check
- communication.prepare
- communication.send
- buyer.match
- contract.prepare
- title.check
- workflow.advance
- exception.open
- exception.resolve

Actions should carry authorization, idempotency, compliance decisions,
evidence references, tool/model versions, and audit events.

## 4. Provider abstraction

External providers should sit behind adapters.

A provider registry should track:

- capabilities
- jurisdiction coverage
- freshness
- reliability
- rate limits
- cost
- licensing/terms constraints
- provenance requirements
- fallback providers

The router can then choose an appropriate provider for each task instead of
hard-coding a vendor into the domain model.

## 5. Automation

Long-running workflows should be event-driven and resumable.

Every external action should support:

- idempotency
- retries with bounded policies
- timeout handling
- webhook reconciliation
- exception escalation
- dry-run/simulation mode
- auditability

Apify belongs behind the same provider boundary as direct APIs. Use an official
API when it is the stable licensed source; use web extraction where permitted
when an API does not provide the needed information.

## 6. Safety boundary

Autonomy is not the same as unrestricted access.

High-impact actions such as outbound communications, offers, contracts,
payments, or other legally consequential actions should pass explicit policy
and compliance gates before execution.
