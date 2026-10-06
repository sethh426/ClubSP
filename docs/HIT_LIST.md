# ClubSP Hit List

The Hit List is the durable vocabulary of reusable trigger terms the user can invoke to tell ChatGPT/ClubSP what operating mode to use.

## Active trigger terms

1. **Sentras** — source-intelligence mode: governed acquisition, monitoring, normalization, provenance, cross-verification, buyer-demand matching, freshness, health, cost, and scalable source coverage.
2. **Meta-Sentras** — source-expansion mode: discover candidate sources, quarantine them, probe metadata/schema, score usefulness and coverage, propose activation, monitor health/schema drift, and re-quarantine broken sources. Discovery never silently activates a source.
3. **Operation Spike** — focused invention mode. One Spike returns exactly 3 high-leverage, non-obvious ideas that could materially improve ClubSP, with implementation path, dependencies, risks/costs, and architectural fit.
4. **Hit List** — show or update this command vocabulary and the current meaning of each trigger term.

## Rules

- Add a term only when the user explicitly names it as a reusable command, operating mode, or trigger concept.
- Preserve the exact user-given name.
- Do not add ordinary feature names automatically.
- Do not invent missing command names.
- A trigger may point to a system, such as Sentras, when invoking that term clearly changes how the assistant should operate.
- Operation Spike ideas do not automatically become Hit List terms unless the user explicitly names/adopts them as reusable triggers.
