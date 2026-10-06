# Meta-Sentras from day one

Meta-Sentras are part of the core Sentra system, not a late-stage optimization.

## Lifecycle

```text
Discovery catalogs / web intelligence
              |
              v
       meta.discovered
              |
              v
        QUARANTINE
              |
      +-------+--------+
      |       |        |
      v       v        v
 metadata   schema   rights/cost
  probe      probe      review
      \       |        /
       \      |       /
        v     v      v
        usefulness score
              |
      +-------+---------+
      |                 |
      v                 v
   HOLD/REJECT      PROPOSE
                         |
                         v
                 activation review
                         |
                         v
                   Sentra registry
```

## Initial discovery Meta-Sentras

### 1. Apify Store Discovery
Searches the public Apify Store for Actors matching current source gaps. Discovery is unauthenticated; execution is separate and requires approved Actor ID, cost ceiling, schema contract and rights review.

### 2. ArcGIS Hub Discovery
Searches public ArcGIS Hub content for datasets, maps and feature services matching parcel, assessor, sale, permit, zoning, foreclosure and other capability gaps.

### 3. Data.gov Discovery
Searches federal catalog metadata and follows candidate resources. Data.gov metadata identifies datasets and source URLs; the catalog metadata itself is not treated as the underlying property evidence.

### 4. CKAN Discovery
Queries compatible government/open-data catalogs with package_search. Individual CKAN hosts become discovery scopes rather than bespoke code.

### 5. Source-Gap Meta-Sentra
Reads current Sentra coverage and buyer demand and emits targeted discovery intents such as:
- "parcel + assessor + Allen County IN"
- "sheriff sale + Marion County IN"
- "permit + code enforcement + St Joseph County IN"

### 6. Schema-Probe Meta-Sentra
Fetches a small bounded sample from a quarantined candidate and records:
- media type;
- field names;
- inferred entity type;
- row/item count;
- stable identity fields;
- timestamp/freshness fields;
- pagination hints;
- authentication requirement;
- schema fingerprint.

It never imports candidate facts into production.

### 7. Usefulness Meta-Sentra
Scores quarantine candidates using transparent signals:
- relevance to a known capability gap;
- jurisdiction coverage;
- public/official provenance;
- structured machine-readable access;
- freshness/change frequency;
- stable identifiers;
- duplication with existing Sentras;
- expected cost;
- observed reliability;
- schema stability.

The score determines review priority, not truth or automatic activation.

### 8. Health + Drift Meta-Sentra
After activation, continuously compares status, response structure, field fingerprints, latency and freshness. Broken or materially changed sources can be automatically quarantined from further ingestion while preserving prior evidence.

## Activation rule

No Meta-Sentra can self-promote a source directly into production.

Allowed automatic actions:
- discover;
- deduplicate;
- probe bounded metadata/sample;
- infer candidate schema;
- calculate transparent scores;
- detect duplication;
- recommend an executor;
- propose registry configuration;
- re-quarantine a broken active source.

Activation requires an explicit policy/operator approval event. This is the trust boundary that lets discovery be aggressive while production ingestion stays controlled.

## Discovery sources verified for initial implementation

- Apify public Store API: searchable public Actor catalog.
- ArcGIS Hub Search API: programmatic search/filter of public Hub content and resources.
- CKAN package_search: generic searchable open-data catalog API.
- Data.gov catalog search/metadata APIs: government-wide metadata and resource discovery.

Future discovery connectors:
- Socrata catalog/discovery;
- state/county open-data portals;
- agency data.json feeds;
- sitemaps/RSS/Atom feeds;
- search-engine/domain discovery;
- GitHub public-data repositories;
- government procurement and notice catalogs.
