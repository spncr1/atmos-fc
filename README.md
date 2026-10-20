# Atmos FC

Atmos FC analyses fan sentiment and online reaction around football matches by collecting YouTube comments from match highlight videos and turning them into a readable post-match snapshot. 

Users can search for finished fixtures, filter by competition and season, pick a match, and see how the mood shifted in the 24 hours after full time through a reaction intensity chart, top comments, match events, overall vibe labels, and crowd energy indicators. 

The aim is to turn thousands of scattered reactions into one centralised view of what a match felt like after the final whistle.

*Live version: https://atmosfc-v1.vercel.app/*

**Current version:** 1.3.0

**Lifecycle stage:** MVP

The root [`VERSION`](VERSION) file is the authoritative product-version source. See [`CHANGELOG.md`](CHANGELOG.md) for the evidence-backed milestone history and [`PRODUCT_RULES.md`](PRODUCT_RULES.md) for Atmos FC-specific content rules.

## The problem

Following football in the modern era can feel more emotionally taxing than ever, especially when more and more high-profile matches are creating multitudes of highlight reels, reaction clips, posts, replies, and comment sections across the many social media platforms people have access to. 

If a fan cannot watch the match live, whether because it is on late at night, they missed the broadcast, or they could not get to the game, it can be hard to get a proper feel for what actually happened without scrolling through thousands of comments online, all saying different things. 

Atmos FC is built to make that post-match catch-up easier for the average fan by bringing that reaction into one place, starting with YouTube comments, so users can quickly understand whether the game was exciting, disappointing, controversial, quiet, or genuinely memorable. By using VADER sentiment analysis and time-based comment bucketing in Python, the app focuses on the first 24 hours after full time, when the raw fan reaction is typically strongest.

## Features

- **Dashboard home page**
    - Search for finished football matches by team or fixture name
    - Browse recent matches from supported competitions
    - Filter by competition and season before choosing a match to analyse
- **Match search and filtering**
    - Search across the Premier League, La Liga, Bundesliga, Serie A, Ligue 1, and the UEFA Champions League
    - Filter results by competition and season from 2015/16 through to 2025/26
    - View match cards with team names, crests, scores, dates, rounds, and competition details
- **Match analysis page**
    - View a selected match with score, half-time score, competition, round, season, venue, and team crests where available
    - See key match events, including goals and cards, alongside the sentiment output
    - Review top liked YouTube comments with source links back to the original videos
- **Sentiment analysis**
    - Pull relevant YouTube highlight videos for a selected match and aggregate top-level comments across available sources
    - Analyse comments with VADER to calculate post-match sentiment and reaction intensity
    - Focus on the first 24 hours after full time to capture the strongest period of online fan reaction
- **Interactive visualisations**
    - Display a Chart.js reaction intensity graph for the 24 hours after full time
    - Show high and low reaction zones, peak reaction window, comment volume, and sentiment score
    - Surface summary labels such as overall vibe and crowd energy based on sentiment, volume, score margin, and late reaction shifts
- **Fallback handling**
    - Try multiple matching YouTube videos when comments are disabled or unavailable
    - Return clear empty and error states when match data, video comments, or analysis results cannot be loaded

## Tech Stack

- Backend: Python, FastAPI, API-Football, Neon Postgres, Football-Data.org, YouTube Data API, VADER
- Frontend: vanilla HTML, CSS, JavaScript, Chart.js
- Backend deployment: Railway
- Frontend deployment: Vercel

## Running the Project locally

Clone the repository:

```bash
git clone https://github.com/spncr1/atmosfc-v1.git
cd atmosfc-v1
```

Install the frontend tooling:

```bash
npm install
```

Create a backend virtual environment and install the Python dependencies:

```bash
python3 -m venv venv
source venv/bin/activate
pip install -r backend/requirements.txt
```

Create `backend/.env` with the API keys required by the FastAPI service:

```bash
FOOTBALL_DATA_API_KEY=your_football_data_api_key_goes_here
API_FOOTBALL_API_KEY=your_api_football_api_key_goes_here
DATABASE_URL=your_neon_pooled_connection_string
DIRECT_DATABASE_URL=your_neon_direct_connection_string
YOUTUBE_API_KEY=your_youtube_api_key_goes_here
CORS_ORIGINS=http://localhost:3000,http://127.0.0.1:3000
```

Run the development server:

```bash
npm run dev
```

Then open the local frontend URL shown in the terminal.

## Showcase and live data modes

The public site is a hybrid portfolio deployment. Its default `showcase` mode reads versioned JSON from `frontend/data/` and does not contact Railway, Neon, YouTube, API-Football, or any other runtime backend. Search, team pages, competition pages, and the exported completed match analyses therefore continue to work if every external service is unavailable.

`frontend/js/config.js` controls the mode:

```js
window.ATMOS_CONFIG = {
  DATA_MODE: "showcase"
};
```

Local development can explicitly use the FastAPI system by setting `window.ATMOS_DATA_MODE = "live"` before `api.js` loads, or by storing `ATMOS_DATA_MODE=live` in browser local storage. Live mode retains the existing backend routes and provider pipeline.

### Public coverage

Current finished results are refreshed for eight competitions:

- Premier League (`PL`)
- La Liga (`PD`)
- Bundesliga (`BL1`)
- Serie A (`SA`)
- Ligue 1 (`FL1`)
- Eredivisie (`NED1`)
- Liga Portugal (`POR1`)
- UEFA Champions League (`CL`)

Four competitions retain clearly dated archive coverage and are not described as current:

- Belgian Pro League (`BEL1`)
- Turkish Super Lig (`TUR1`)
- UEFA Europa League (`EL`)
- UEFA Conference League (`UECL`)

The committed archive contains all reviewed finished fixtures available at export time. Only completed `match_context_v2` analyses are public. Matches without an exported analysis still appear in results and profiles, but do not display an Analyse control and cannot start a YouTube or backend request.

### Generate and validate data

Export the reviewed database archive. This is a read-only database operation and publishes only after every document validates:

```bash
npm run showcase:export
```

Refresh the cumulative current-season pack. The command updates corrections, adds newly finished fixtures, retains prior data after a partial or empty response, and publishes successful competitions independently:

```bash
FOOTBALL_DATA_API_KEY=your_key npm run showcase:refresh
```

Validate schemas, counts, integrity checksums, analysis versions, and the generated-file secret scan without network access:

```bash
npm run showcase:validate
```

The archive manifest is `frontend/data/showcase/manifest.json`. The current pack has a separate checksum in `frontend/data/current/current-season.json`; together they identify the exact archive and current-result pack included in a deployment.

### Scheduled production refresh

The production refresh runs every six hours using `.github/workflows/refresh-showcase.yml` and Vercel CLI. It requires these repository secrets:

```text
FOOTBALL_DATA_API_KEY
VERCEL_TOKEN
VERCEL_ORG_ID
VERCEL_PROJECT_ID
```

The machine-managed `showcase-data` branch stores the last successful generated current pack and is never merged into `main`. A refresh must validate before updating that branch or deploying. Vercel Git deployments must remain disabled so the workflow is the only production deployment authority; otherwise a normal Git deployment could replace a newer pack with the older committed fallback.

### Re-enable the full live system

Keep the existing Railway, Neon, API-Football, YouTube, and FastAPI configuration. To use it locally or in a dedicated live deployment:

1. Supply the server-side variables listed below.
2. Start the backend with `npm run backend`.
3. Set the frontend data mode to `live` and point `ATMOS_CONFIG.API_BASE` at that backend.
4. Re-enable the required Railway workers only after confirming provider subscriptions and spending limits.

The showcase export never contains credentials, raw provider payloads, failed jobs, review states, or matching diagnostics.

## Deployment

Production defaults to the static showcase data described above. The FastAPI backend URL remains configured for explicitly selected live mode.

The Railway backend must have these environment variables:

```bash
API_FOOTBALL_API_KEY=your_api_football_api_key_goes_here
DATABASE_URL=your_neon_pooled_connection_string
DIRECT_DATABASE_URL=your_neon_direct_connection_string
FOOTBALL_DATA_API_KEY=your_football_data_api_key_goes_here
YOUTUBE_API_KEY=your_youtube_api_key_goes_here
CORS_ORIGINS=https://atmosfc-v1.vercel.app,http://localhost:3000,http://127.0.0.1:3000
```

Railway runs the database migrations on startup before launching FastAPI.
