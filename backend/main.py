# FastAPI entry point

from __future__ import annotations

from datetime import datetime, timezone
from math import ceil

from fastapi import FastAPI, Header, HTTPException, Query
from fastapi.middleware.cors import CORSMiddleware

from backend.config import get_settings
from backend.project_version import PROJECT_VERSION
from backend.database.session import get_sessionmaker
from backend.models.schemas import (
    AnalyseRequest,
    AnalysisResponse,
    AnalyseMeta,
    MatchSearchResponse,
    MetadataResponse,
    CompetitionProfileResponse,
    TeamProfileResponse,
)
from backend.repositories import football_data as repo
from backend.services import football_api
from backend.services.football_api import FootballDataError
from backend.services.metadata import MetadataError, frontend_metadata
from backend.services.competition_profiles import (
    CompetitionProfileNotFoundError,
    CompetitionProfileUnavailableError,
    competition_profile,
)
from backend.services.team_profiles import TeamProfileNotFoundError, TeamProfileUnavailableError, team_profile
from backend.services.matches import (
    MatchDataError,
    analysis_match,
    recent_matches,
    search_matches,
)
from backend.services.sync import fixture_sync_status, sync_core_football_data
from backend.services.youtube import YouTubeError, fetch_match_comments
from backend.services.youtube_cache import cache_youtube_comment_batch, cache_youtube_comment_error
from backend.services.sentiment import analyse_comments, energy_label

ANALYSIS_ALGORITHM_VERSION = "match_context_v2"

app = FastAPI(title="Atmos API", version=PROJECT_VERSION)
settings = get_settings()

app.add_middleware(
    CORSMiddleware,
    allow_origins=settings.allowed_origins,
    allow_credentials=True,
    allow_methods=["*"],
    allow_headers=["*"],
)


@app.get("/health")
async def health() -> dict[str, str]:
    # Return a simple service health response.

    return {"status": "ok"}


@app.get("/debug/config")
async def debug_config() -> dict[str, list[str]]:
    # Return non-secret runtime config useful for deployment checks.

    return {"allowed_origins": settings.allowed_origins}


@app.get("/sync/fixture-status")
async def get_fixture_sync_status():
    # Return whether the API-Football fixture sync is keeping match data fresh.

    return await fixture_sync_status()


@app.post("/sync/fixture-refresh")
async def refresh_fixture_sync(
    recent_limit: int = Query(default=30, ge=1, le=50),
    include_events: bool = False,
    sync_admin_token: str | None = Header(default=None, alias="X-Sync-Admin-Token"),
):
    # Manually refresh recent API-Football fixtures when the scheduled worker falls behind.

    require_sync_admin_token(sync_admin_token)
    try:
        result = await sync_core_football_data(recent_limit=recent_limit, include_events=include_events)
        sync = await fixture_sync_status()
        return {"result": result, "sync": sync}
    except FootballDataError as exc:
        raise HTTPException(status_code=502, detail=str(exc)) from exc


@app.get("/metadata", response_model=MetadataResponse)
async def get_metadata() -> MetadataResponse:
    # Return synced frontend filter options.

    try:
        return await frontend_metadata()
    except MetadataError as exc:
        raise HTTPException(status_code=502, detail=str(exc)) from exc


@app.get("/teams/{team_id}", response_model=TeamProfileResponse)
async def get_team_profile(
    team_id: int,
    season: int | None = Query(default=None, ge=1900, le=2100),
) -> TeamProfileResponse:
    # Return an Atmos FC team profile for one selected season.

    try:
        return await team_profile(team_id, season)
    except TeamProfileNotFoundError as exc:
        raise HTTPException(status_code=404, detail=str(exc)) from exc
    except TeamProfileUnavailableError as exc:
        raise HTTPException(status_code=502, detail=str(exc)) from exc


@app.get("/competitions/{competition_id}", response_model=CompetitionProfileResponse)
async def get_competition_profile(
    competition_id: int,
    season: int = Query(ge=1900, le=2100),
) -> CompetitionProfileResponse:
    # Return a season-specific competition database snapshot from stored data.

    try:
        return await competition_profile(competition_id, season)
    except CompetitionProfileNotFoundError as exc:
        raise HTTPException(status_code=404, detail=str(exc)) from exc
    except CompetitionProfileUnavailableError as exc:
        raise HTTPException(status_code=502, detail=str(exc)) from exc


@app.get("/matches/recent")
async def get_recent_matches(
    competition: str | None = None,
    limit: int = Query(default=18, ge=1, le=30),
):
    # Return recent finished matches for the landing page.

    try:
        matches = await recent_matches(limit=limit, competition=competition)
        sync = await fixture_sync_status()
        return {"matches": matches, "sync": sync}
    except MatchDataError as exc:
        raise HTTPException(status_code=502, detail=str(exc)) from exc


@app.get("/matches/search", response_model=MatchSearchResponse)
async def get_search_matches(
    q: str = "",
    competition: str | None = None,
    season: str | None = None,
    analysis_available: bool = False,
    page: int = Query(default=1, ge=1),
    page_size: int = Query(default=15, ge=1, le=30),
):
    # Search matches by team, competition, and season.

    try:
        season_year = int(season) if season else None
        result = await search_matches(query=q, competition=competition, season=season_year)
        matches = (
            [match for match in result.matches if match.analysis_available]
            if analysis_available
            else result.matches
        )
        total = len(matches)
        total_pages = ceil(total / page_size) if total else 0
        current_page = min(page, total_pages) if total_pages else 1
        start = (current_page - 1) * page_size
        end = start + page_size
        page_matches = matches[start:end]
        return {
            "matches": page_matches,
            "pagination": {
                "page": current_page,
                "page_size": page_size,
                "total": total,
                "total_pages": total_pages,
                "has_previous": current_page > 1,
                "has_next": total_pages > 0 and current_page < total_pages,
            },
            "notices": result.notices,
        }
    except ValueError as exc:
        raise HTTPException(status_code=422, detail="Season must be a year such as 2025.") from exc
    except MatchDataError as exc:
        raise HTTPException(status_code=502, detail=str(exc)) from exc


@app.post("/analyse", response_model=AnalysisResponse)
async def analyse_match(payload: AnalyseRequest) -> AnalysisResponse:
    # Analyse one finished match, using cached analysis before new YouTube calls.

    match = None
    try:
        local_analysis = await analysis_match(payload.match_id)
        if local_analysis is None:
            raw_match = await football_api.get_match(payload.match_id)
            home_team_detail, away_team_detail = await football_api.get_match_team_details(raw_match)
            match = football_api.parse_match(raw_match, home_team_detail, away_team_detail)
            match = await football_api.enrich_match_context(match, raw_match)
            events = football_api.parse_events(raw_match)
            score_margin = _score_margin(raw_match)
        else:
            match, events = local_analysis
            score_margin = _summary_score_margin(match.score)
        if match.status != "FINISHED":
            raise HTTPException(status_code=400, detail="Only finished matches can be analysed.")
        cached_response = await cached_analysis_response(match, events)
        if cached_response is not None:
            return cached_response
        try:
            video_comments = fetch_match_comments(match)
        except YouTubeError as exc:
            cache_result = await cache_youtube_comment_error(match, exc)
            match.youtube_comment_status = cache_result.status
            match.youtube_comment_count = cache_result.raw_comment_count
            match.youtube_analysed_comment_count = cache_result.analysed_comment_count
            cached_response = await cached_analysis_response(match, events)
            if cached_response is not None:
                return cached_response
            return unavailable_youtube_analysis_response(match, events)
        kickoff = datetime.fromisoformat(match.date.replace("Z", "+00:00")).astimezone(timezone.utc)
        source_video_count = len({comment.permalink for comment in video_comments.comments}) or 1
        buckets, reaction_intensity, half_split, top_comments, peak_minute, peak_window, _youtube_vibe, _youtube_energy = analyse_comments(
            video_comments.comments,
            kickoff,
            score_margin,
        )
        total_comments = sum(bucket.comment_count for bucket in reaction_intensity)
        cache_result = await cache_youtube_comment_batch(match, video_comments)
        match.youtube_comment_status = cache_result.status
        match.youtube_comment_count = cache_result.raw_comment_count
        match.youtube_analysed_comment_count = cache_result.analysed_comment_count
        response = AnalysisResponse(
            match=match,
            events=events,
            sentiment_buckets=buckets,
            reaction_intensity=reaction_intensity,
            top_comments=top_comments,
            half_split=half_split,
            meta=AnalyseMeta(
                total_comments=total_comments,
                peak_minute=peak_minute,
                peak_window=peak_window,
                source_video_count=source_video_count,
                overall_vibe=match_context_vibe(match, events),
                crowd_energy=reaction_energy_label(total_comments),
                youtube_video_url=video_comments.url,
                analysis_mode="youtube_sentiment",
                analysis_version=ANALYSIS_ALGORITHM_VERSION,
            ),
        )
        await cache_analysis_response(match, response)
        return response
    except MatchDataError as exc:
        raise HTTPException(status_code=502, detail=str(exc)) from exc
    except FootballDataError as exc:
        raise HTTPException(status_code=502, detail=str(exc)) from exc


def require_sync_admin_token(sync_admin_token: str | None) -> None:
    if not settings.sync_admin_token:
        raise HTTPException(status_code=503, detail="Manual fixture sync is not configured.")
    if sync_admin_token != settings.sync_admin_token:
        raise HTTPException(status_code=401, detail="Invalid sync admin token.")


def _score_margin(raw_match: dict) -> int:
    return football_api.score_margin(raw_match)


def _summary_score_margin(score: str | None) -> int:
    if not score or "-" not in score:
        return 0
    home, away = score.split("-", maxsplit=1)
    try:
        return abs(int(home.strip()) - int(away.strip()))
    except ValueError:
        return 0


async def cache_analysis_response(match, response: AnalysisResponse) -> None:
    provider_fixture_id = provider_fixture_id_from_match(match)
    if provider_fixture_id is None:
        return

    sessionmaker = get_sessionmaker()
    async with sessionmaker() as session:
        fixture = await repo.fixture_by_provider_fixture_id(session, provider_fixture_id)
        if fixture is None:
            return
        await repo.upsert_analysis_cache(
            session,
            fixture,
            status="complete",
            payload=response.model_dump(mode="json"),
            source_video_count=response.meta.source_video_count,
            total_comments=response.meta.total_comments,
        )
        await session.commit()


async def cached_analysis_response(match, events) -> AnalysisResponse | None:
    provider_fixture_id = provider_fixture_id_from_match(match)
    if provider_fixture_id is None:
        return None

    sessionmaker = get_sessionmaker()
    async with sessionmaker() as session:
        fixture = await repo.fixture_by_provider_fixture_id(session, provider_fixture_id)
        if fixture is None:
            return None
        cache = await repo.analysis_cache_for_fixture(session, fixture)
        if cache is None or cache.status != "complete":
            return None
    payload = cache.payload
    meta = payload.get("meta") if isinstance(payload, dict) else None
    if not isinstance(meta, dict) or meta.get("analysis_version") != ANALYSIS_ALGORITHM_VERSION:
        return None

    response = AnalysisResponse.model_validate(payload)
    response.match = match
    response.events = events
    response.meta.analysis_mode = "cached_youtube_sentiment"
    return response


def provider_fixture_id_from_match(match) -> int | None:
    try:
        return int(match.id)
    except (TypeError, ValueError):
        return None


def unavailable_youtube_analysis_response(match, events) -> AnalysisResponse:
    return AnalysisResponse(
        match=match,
        events=events,
        sentiment_buckets=[],
        reaction_intensity=[],
        top_comments=[],
        half_split={
            "first": {"pos": 0, "neg": 0, "neu": 0},
            "second": {"pos": 0, "neg": 0, "neu": 0},
        },
        meta=AnalyseMeta(
            total_comments=0,
            peak_minute=0,
            peak_window={"hour_start": 0, "hour_end": 0},
            source_video_count=0,
            overall_vibe=match_context_vibe(match, events),
            crowd_energy={
                "label": "Unavailable",
                "subtext": "24-hour reaction data unavailable for this match",
            },
            youtube_video_url=None,
            analysis_mode="youtube_unavailable",
            analysis_version=ANALYSIS_ALGORITHM_VERSION,
        ),
    )


def match_context_vibe(match, events) -> dict[str, str]:
    total_goals = total_score_goals(match)
    event_count = len(events)
    margin = _summary_score_margin(match.score)
    has_red_card = any(getattr(event, "type", "") == "red-card" for event in events)
    has_penalties = bool(getattr(match, "penalty_score", None))
    went_to_extra_time = getattr(match, "score_note", None) == "AET"

    if not match.score or "-" not in match.score:
        return {
            "label": "Unavailable",
            "subtext": "Match score data unavailable",
        }

    if has_penalties or (went_to_extra_time and margin <= 1):
        label = "Dramatic"
    elif total_goals >= 6 and margin <= 1:
        label = "Thriller"
    elif total_goals >= 5 and margin <= 2:
        label = "Thriller"
    elif total_goals >= 5 and margin >= 4:
        label = "Dominant"
    elif total_goals >= 5:
        label = "Chaotic"
    elif total_goals >= 3 or event_count >= 12:
        label = "Lively"
    elif total_goals == 0:
        label = "Cagey"
    elif has_red_card and margin <= 1:
        label = "Lively"
    else:
        label = "Competitive"
    return {
        "label": label,
        "subtext": "Estimated from goals and key match events",
    }


def reaction_energy_label(total_comments: int) -> dict[str, str]:
    if total_comments <= 0:
        return {
            "label": "Unavailable",
            "subtext": "No YouTube comment data available",
        }
    return energy_label(total_comments)


def total_score_goals(match) -> int:
    if not match.score or "-" not in match.score:
        return 0
    home, away = match.score.split("-", maxsplit=1)
    try:
        return int(home.strip()) + int(away.strip())
    except ValueError:
        return 0
