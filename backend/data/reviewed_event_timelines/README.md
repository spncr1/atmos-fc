# Reviewed event timelines

This directory contains optional, human-reviewed timeline overrides for the
static showcase. It is not read during a page request.

Import a reviewed JSON document with:

```bash
npm run showcase:events:import -- path/to/timeline.json
```

Each document must identify the fixture (`fixture_id`, `home`, `away`, `date`
and `score`), include a safe source URL and review timestamp, and provide key
events using the public `MatchEvent` shape. The importer rejects a timeline
unless its scoring events reconcile with the final score. The showcase export
then checks the identity fields against the stored fixture before using it.
