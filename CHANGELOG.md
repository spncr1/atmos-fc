# Changelog

This changelog follows Semantic Versioning. The root `VERSION` file is the authoritative source for the current product version.

## Current baseline

### 1.2.0 - Established 2026-10-15

Atmos FC is a deployed football match-search and fan-reaction analysis MVP with competition and club profile experiences, persistent football data, fixture and event syncing, and provider-outage handling.

This is the first version recorded under the repository's complete project-level versioning workflow. The repository has no earlier version tags or releases, so the history below records evidence-backed development milestones rather than claiming formal historical releases.

## Historical milestones (not formal releases)

### October 2026 - Competition and expanded club profiles

- Added competition profile pages with season summaries, participating clubs, and recent results.
- Expanded club profiles with honours, squad information, season snapshots, and reviewed editorial content.
- Added supporting backend services, schemas, background work, and focused tests.

### August-September 2026 - Persistent football data and resilient syncing

- Adopted API-Football with Neon Postgres-backed persistence while retaining legacy provider fallback behaviour.
- Added team profiles, fixture and event syncing, background workers, stale-data handling, and provider-outage fallback.
- Expanded match-analysis context and responsive presentation.

### May-June 2026 - First coherent MVP and deployment

- Completed match search, results, YouTube reaction collection, sentiment analysis, and post-match visualisation workflows.
- Added responsive layouts and cup-tie handling.
- Deployed the frontend through Vercel and the backend through Railway.
