# Memory Core

ClubSP separates **operational state** from **memory and learning**.

## Core loop

**Evidence → Fact → Prediction → Observation → Error → Learning**

### SourceRecord
Records where evidence came from, when it was retrieved, and how reliable it is.

### Fact
A normalized claim about a subject, linked to its source and confidence.

### Prediction
A forecast made by a model or rule. Predictions are retained so they can later be measured.

### Observation
What actually happened, with optional source provenance.

### LearningRecord
A durable record of a learned pattern tied back to the evidence that produced it.

### WorkflowEvent
An auditable state transition or action taken by the system.

The current implementation is intentionally small and dependency-free. Persistence,
provider adapters, vector retrieval, model evaluation, and MCP tools should be
layered on top rather than embedded into these domain primitives.
