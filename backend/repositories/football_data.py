"""Repository helpers for synced football data."""

from __future__ import annotations

from datetime import date, datetime, timedelta, timezone
from typing import Any
import re
import unicodedata

from sqlalchemy import and_, delete, or_, func, select, update
from sqlalchemy.dialects.postgresql import insert as pg_insert
from sqlalchemy.ext.asyncio import AsyncSession
from sqlalchemy.orm import aliased, joinedload

from backend.database.models import (
    AnalysisCache,
    ArchiveSyncStatus,
    BackgroundJob,
    Competition,
    CompetitionSeason,
    Fixture,
    FixtureEvent,
    FixtureEventSyncStatus,
    Season,
    SyncRun,
    Team,
    TeamProfileEnrichment,
    TeamVisualProfile,
    YouTubeCommentCache,
)

PROVIDER = "api_football"
FINISHED_STATUS_CODES = {"FT", "AET", "PEN"}
FINISHED_ARCHIVE_SYNC_STATUSES = {"complete", "partial", "failed", "provider_unavailable"}
PROTECTED_VISUAL_STATUSES = ("known", "manual_verified")
PROTECTED_VISUAL_SOURCES = ("manual_registry", "manual_verified")
HANDLED_VISUAL_SOURCES = ("manual_registry", "manual_verified", "logo_extracted", "fallback_unknown")
RETRYABLE_VISUAL_UNKNOWN_REASONS = ("pillow_unavailable", "logo_fetch_failed")


async def upsert_season(session: AsyncSession, year: int, *, is_current: bool = False) -> Season:
    season = await session.scalar(select(Season).where(Season.year == year))
    if season is None:
        season = Season(year=year, label=season_label(year), is_current=is_current)
        session.add(season)
    else:
        season.label = season_label(year)
        season.is_current = is_current
    await session.flush()
    return season


async def upsert_competition(session: AsyncSession, raw: dict[str, Any]) -> Competition:
    league = raw.get("league") or {}
    country = raw.get("country") or {}
    provider_id = int(league["id"])
    competition = await session.scalar(
        select(Competition).where(
            Competition.provider == PROVIDER,
            Competition.provider_competition_id == provider_id,
        )
    )
    values = {
        "provider": PROVIDER,
        "provider_competition_id": provider_id,
        "name": league.get("name") or f"Competition {provider_id}",
        "code": league.get("code"),
        "type": league.get("type"),
        "country_name": country.get("name"),
        "country_code": country.get("code"),
        "logo_url": league.get("logo"),
        "flag_url": country.get("flag"),
        "coverage": raw.get("coverage"),
        "raw_payload": raw,
        "is_active": True,
    }
    if competition is None:
        competition = Competition(**values)
        session.add(competition)
    else:
        _assign(competition, values)
    await session.flush()
    return competition


async def upsert_competition_season(
    session: AsyncSession,
    competition: Competition,
    season: Season,
    raw_season: dict[str, Any] | None,
) -> CompetitionSeason:
    competition_season = await session.scalar(
        select(CompetitionSeason).where(
            CompetitionSeason.competition_id == competition.id,
            CompetitionSeason.season_id == season.id,
        )
    )
    raw = raw_season or {}
    values = {
        "competition_id": competition.id,
        "season_id": season.id,
        "start_date": parse_date(raw.get("start")),
        "end_date": parse_date(raw.get("end")),
        "is_current": bool(raw.get("current")),
        "coverage": raw.get("coverage"),
        "raw_payload": raw or None,
    }
    if competition_season is None:
        competition_season = CompetitionSeason(**values)
        session.add(competition_season)
    else:
        _assign(competition_season, values)
    await session.flush()
    return competition_season


async def upsert_team(session: AsyncSession, raw: dict[str, Any]) -> Team:
    team_data = raw.get("team") or raw
    venue = raw.get("venue") or {}
    provider_id = int(team_data["id"])
    team = await session.scalar(
        select(Team).where(
            Team.provider == PROVIDER,
            Team.provider_team_id == provider_id,
        )
    )
    values = {
        "provider": PROVIDER,
        "provider_team_id": provider_id,
        "name": team_data.get("name") or f"Team {provider_id}",
        "code": team_data.get("code"),
        "country_name": team_data.get("country"),
        "founded": safe_int(team_data.get("founded")),
        "is_national": bool(team_data.get("national")),
        "logo_url": team_data.get("logo"),
        "venue_name": venue.get("name"),
        "venue_city": venue.get("city"),
        "venue_image_url": venue.get("image"),
        "raw_payload": raw,
    }
    if team is None:
        team = Team(**values)
        session.add(team)
        is_new = True
    else:
        _assign(team, values)
        is_new = False
    await session.flush()
    await link_team_visual_profile(session, team)
    if is_new:
        await enqueue_background_job(
            session,
            job_type="team_honours",
            job_key=str(provider_id),
            payload={"provider_team_id": provider_id},
            priority=180,
        )
    return team


async def upsert_teams(session: AsyncSession, rows: list[dict[str, Any]]) -> dict[int, Team]:
    provider_ids = [int((row.get("team") or row)["id"]) for row in rows]
    existing = await teams_by_provider_ids(session, provider_ids)
    teams: dict[int, Team] = {}
    for raw in rows:
        team_data = raw.get("team") or raw
        provider_id = int(team_data["id"])
        values = team_values(raw)
        team = existing.get(provider_id)
        if team is None:
            team = Team(**values)
            session.add(team)
            await session.flush()
            await enqueue_background_job(
                session,
                job_type="team_honours",
                job_key=str(provider_id),
                payload={"provider_team_id": provider_id},
                priority=180,
            )
        else:
            _assign(team, values)
        teams[provider_id] = team
    await session.flush()
    visual_profiles = await team_visual_profiles_by_provider_ids(session, list(teams.keys()))
    for provider_id, team in teams.items():
        visual_profile = visual_profiles.get(provider_id)
        if visual_profile is not None and visual_profile.team_id != team.id:
            visual_profile.team_id = team.id
    await session.flush()
    return teams


async def upsert_fixture(
    session: AsyncSession,
    raw: dict[str, Any],
    competition: Competition,
    season: Season,
    home_team: Team,
    away_team: Team,
) -> Fixture:
    fixture_data = raw.get("fixture") or {}
    provider_id = int(fixture_data["id"])
    status = fixture_data.get("status") or {}
    venue = fixture_data.get("venue") or {}
    goals = raw.get("goals") or {}
    score = raw.get("score") or {}
    halftime = score.get("halftime") or {}
    fulltime = score.get("fulltime") or {}
    extratime = score.get("extratime") or {}
    penalty = score.get("penalty") or {}
    fixture = await session.scalar(
        select(Fixture).where(
            Fixture.provider == PROVIDER,
            Fixture.provider_fixture_id == provider_id,
        )
    )
    values = {
        "provider": PROVIDER,
        "provider_fixture_id": provider_id,
        "competition_id": competition.id,
        "season_id": season.id,
        "home_team_id": home_team.id,
        "away_team_id": away_team.id,
        "kickoff_at": parse_datetime(fixture_data.get("date")),
        "status_short": status.get("short"),
        "status_long": status.get("long"),
        "elapsed": safe_int(status.get("elapsed")),
        "round_name": (raw.get("league") or {}).get("round"),
        "venue_name": venue.get("name"),
        "venue_city": venue.get("city"),
        "home_goals": safe_int(goals.get("home")),
        "away_goals": safe_int(goals.get("away")),
        "halftime_home_goals": safe_int(halftime.get("home")),
        "halftime_away_goals": safe_int(halftime.get("away")),
        "fulltime_home_goals": safe_int(fulltime.get("home")),
        "fulltime_away_goals": safe_int(fulltime.get("away")),
        "extratime_home_goals": safe_int(extratime.get("home")),
        "extratime_away_goals": safe_int(extratime.get("away")),
        "penalty_home_goals": safe_int(penalty.get("home")),
        "penalty_away_goals": safe_int(penalty.get("away")),
        "raw_payload": raw,
    }
    if fixture is None:
        fixture = Fixture(**values)
        session.add(fixture)
    else:
        _assign(fixture, values)
    await session.flush()
    return fixture


async def upsert_fixtures(
    session: AsyncSession,
    rows: list[dict[str, Any]],
    competition: Competition,
    season: Season,
    teams_by_provider_id: dict[int, Team],
) -> tuple[list[Fixture], int]:
    """Upsert many fixtures with one flush instead of one round-trip per fixture."""

    if not rows:
        return [], 0

    provider_ids = [int((row.get("fixture") or {})["id"]) for row in rows]
    team_provider_ids = sorted({
        int(team["id"])
        for row in rows
        for team in (row.get("teams") or {}).values()
        if team and team.get("id") is not None
    })
    teams_by_provider_id = {
        **await teams_by_provider_ids(session, team_provider_ids),
        **teams_by_provider_id,
    }
    existing_rows = await session.scalars(
        select(Fixture).where(
            Fixture.provider == PROVIDER,
            Fixture.provider_fixture_id.in_(set(provider_ids)),
        )
    )
    existing = {fixture.provider_fixture_id: fixture for fixture in existing_rows}
    fixtures: list[Fixture] = []
    new_teams = 0

    for raw in rows:
        teams = raw.get("teams") or {}
        home_raw = teams.get("home") or {}
        away_raw = teams.get("away") or {}
        home_team = teams_by_provider_id.get(int(home_raw["id"]))
        away_team = teams_by_provider_id.get(int(away_raw["id"]))

        if home_team is None:
            home_team = Team(**team_values({"team": home_raw}))
            session.add(home_team)
            teams_by_provider_id[home_team.provider_team_id] = home_team
            new_teams += 1
        if away_team is None:
            away_team = Team(**team_values({"team": away_raw}))
            session.add(away_team)
            teams_by_provider_id[away_team.provider_team_id] = away_team
            new_teams += 1
        if home_team.id is None or away_team.id is None:
            await session.flush()

        provider_id = int((raw.get("fixture") or {})["id"])
        values = fixture_values(raw, competition, season, home_team, away_team)
        fixture = existing.get(provider_id)
        if fixture is None:
            fixture = Fixture(**values)
            session.add(fixture)
        else:
            _assign(fixture, values)
        fixtures.append(fixture)

    await session.flush()
    return fixtures, new_teams


async def replace_fixture_events(
    session: AsyncSession,
    fixture: Fixture,
    raw_events: list[dict[str, Any]],
) -> int:
    await session.execute(delete(FixtureEvent).where(FixtureEvent.fixture_id == fixture.id))
    for raw in raw_events:
        team_data = raw.get("team") or {}
        player = raw.get("player") or {}
        assist = raw.get("assist") or {}
        time_data = raw.get("time") or {}
        team = await team_by_provider_id(session, safe_int(team_data.get("id")))
        session.add(
            FixtureEvent(
                fixture_id=fixture.id,
                team_id=team.id if team else None,
                provider_player_id=safe_int(player.get("id")),
                player_name=player.get("name"),
                provider_assist_player_id=safe_int(assist.get("id")),
                assist_player_name=assist.get("name"),
                minute=safe_int(time_data.get("elapsed")),
                extra_minute=safe_int(time_data.get("extra")),
                type=raw.get("type") or "Unknown",
                detail=raw.get("detail"),
                comments=raw.get("comments"),
                raw_payload=raw,
            )
        )
    await session.flush()
    return len(raw_events)


async def upsert_fixture_event_sync_status(
    session: AsyncSession,
    fixture: Fixture,
    *,
    status: str,
    event_count: int | None = None,
    checked_at: datetime | None = None,
    error_message: str | None = None,
    raw_payload: dict[str, Any] | None = None,
) -> FixtureEventSyncStatus:
    sync_status = await session.scalar(
        select(FixtureEventSyncStatus).where(FixtureEventSyncStatus.fixture_id == fixture.id)
    )
    if checked_at is None and status in {"complete", "unavailable", "failed"}:
        checked_at = datetime.now(timezone.utc)

    values = {
        "fixture_id": fixture.id,
        "status": status,
        "event_count": event_count,
        "checked_at": checked_at,
        "error_message": error_message,
        "raw_payload": raw_payload,
    }
    if sync_status is None:
        sync_status = FixtureEventSyncStatus(**values)
        session.add(sync_status)
    else:
        _assign(sync_status, values)
    await session.flush()
    return sync_status


async def upsert_youtube_comment_cache(
    session: AsyncSession,
    fixture: Fixture,
    *,
    status: str,
    raw_comment_count: int | None = None,
    analysed_comment_count: int | None = None,
    source_video_count: int | None = None,
    best_video_url: str | None = None,
    best_video_title: str | None = None,
    best_video_channel: str | None = None,
    checked_at: datetime | None = None,
    error_message: str | None = None,
    raw_payload: dict[str, Any] | None = None,
) -> YouTubeCommentCache:
    cache = await session.scalar(
        select(YouTubeCommentCache).where(YouTubeCommentCache.fixture_id == fixture.id)
    )
    if checked_at is None and status in {"complete", "no_comments", "unavailable", "failed", "rate_limited"}:
        checked_at = datetime.now(timezone.utc)

    values = {
        "fixture_id": fixture.id,
        "status": status,
        "raw_comment_count": raw_comment_count,
        "analysed_comment_count": analysed_comment_count,
        "source_video_count": source_video_count,
        "best_video_url": best_video_url,
        "best_video_title": best_video_title,
        "best_video_channel": best_video_channel,
        "checked_at": checked_at,
        "error_message": error_message,
        "raw_payload": raw_payload,
    }
    if cache is None:
        cache = YouTubeCommentCache(**values)
        session.add(cache)
    else:
        _assign(cache, values)
    await session.flush()
    return cache


async def youtube_comment_cache_for_fixture(
    session: AsyncSession,
    fixture: Fixture,
) -> YouTubeCommentCache | None:
    return await session.scalar(
        select(YouTubeCommentCache).where(YouTubeCommentCache.fixture_id == fixture.id)
    )


async def upsert_analysis_cache(
    session: AsyncSession,
    fixture: Fixture,
    *,
    status: str,
    payload: dict[str, Any],
    source_video_count: int | None = None,
    total_comments: int | None = None,
    checked_at: datetime | None = None,
    error_message: str | None = None,
) -> AnalysisCache:
    cache = await session.scalar(
        select(AnalysisCache).where(AnalysisCache.fixture_id == fixture.id)
    )
    if checked_at is None and status in {"complete", "failed"}:
        checked_at = datetime.now(timezone.utc)

    values = {
        "fixture_id": fixture.id,
        "status": status,
        "payload": payload,
        "source_video_count": source_video_count,
        "total_comments": total_comments,
        "checked_at": checked_at,
        "error_message": error_message,
    }
    if cache is None:
        cache = AnalysisCache(**values)
        session.add(cache)
    else:
        _assign(cache, values)
    await session.flush()
    return cache


async def analysis_cache_for_fixture(
    session: AsyncSession,
    fixture: Fixture,
) -> AnalysisCache | None:
    return await session.scalar(
        select(AnalysisCache).where(AnalysisCache.fixture_id == fixture.id)
    )


async def fixture_by_provider_fixture_id(
    session: AsyncSession,
    provider_fixture_id: int,
) -> Fixture | None:
    return await session.scalar(
        select(Fixture)
        .options(
            joinedload(Fixture.youtube_comment_cache),
            joinedload(Fixture.event_sync_status),
            joinedload(Fixture.analysis_cache),
        )
        .where(
            Fixture.provider == PROVIDER,
            Fixture.provider_fixture_id == provider_fixture_id,
        )
    )


async def fixtures_by_provider_fixture_ids(
    session: AsyncSession,
    provider_fixture_ids: list[int],
) -> list[Fixture]:
    if not provider_fixture_ids:
        return []
    rows = await session.scalars(
        select(Fixture)
        .options(
            joinedload(Fixture.competition),
            joinedload(Fixture.season),
            joinedload(Fixture.home_team),
            joinedload(Fixture.away_team),
            joinedload(Fixture.youtube_comment_cache),
            joinedload(Fixture.event_sync_status),
            joinedload(Fixture.analysis_cache),
        )
        .where(
            Fixture.provider == PROVIDER,
            Fixture.provider_fixture_id.in_(set(provider_fixture_ids)),
        )
    )
    return list(rows)


async def teams_represented_in_competitions(
    session: AsyncSession,
    provider_competition_ids: set[int],
) -> list[Team]:
    """Return every distinct club represented by stored fixtures in scope."""

    if not provider_competition_ids:
        return []
    rows = await session.scalars(
        select(Team)
        .join(Fixture, or_(Fixture.home_team_id == Team.id, Fixture.away_team_id == Team.id))
        .join(Competition, Competition.id == Fixture.competition_id)
        .where(
            Competition.provider_competition_id.in_(provider_competition_ids),
            Team.is_national.is_(False),
        )
        .distinct()
        .order_by(Team.name.asc())
    )
    return list(rows)


async def queue_team_honours_jobs(session: AsyncSession, teams: list[Team]) -> int:
    """Queue honours jobs for many clubs in one database operation."""

    if not teams:
        return 0
    now = datetime.now(timezone.utc)
    values = [
        {
            "job_type": "team_honours",
            "job_key": str(team.provider_team_id),
            "status": "queued",
            "priority": 180,
            "attempts": 0,
            "max_attempts": 3,
            "available_at": now,
            "payload": {"provider_team_id": team.provider_team_id},
        }
        for team in teams
    ]
    statement = pg_insert(BackgroundJob).values(values).on_conflict_do_nothing(
        index_elements=[BackgroundJob.job_type, BackgroundJob.job_key],
    )
    await session.execute(statement)
    return len(values)


async def enqueue_background_job(
    session: AsyncSession,
    *,
    job_type: str,
    job_key: str,
    payload: dict[str, Any] | None = None,
    priority: int = 100,
    max_attempts: int = 3,
) -> BackgroundJob:
    job = await session.scalar(
        select(BackgroundJob).where(
            BackgroundJob.job_type == job_type,
            BackgroundJob.job_key == job_key,
        )
    )
    values = {
        "job_type": job_type,
        "job_key": job_key,
        "payload": payload,
        "priority": priority,
        "max_attempts": max_attempts,
    }
    if job is None:
        job = BackgroundJob(status="queued", **values)
        session.add(job)
    elif job.status in {"failed", "complete"}:
        _assign(
            job,
            {
                **values,
                "status": "queued",
                "attempts": 0,
                "started_at": None,
                "finished_at": None,
                "error_message": None,
                "available_at": datetime.now(timezone.utc),
            },
        )
    elif job.status == "running" and is_stale_background_job(job):
        _assign(
            job,
            {
                **values,
                "status": "queued",
                "started_at": None,
                "finished_at": None,
                "error_message": "Recovered stale running job.",
                "available_at": datetime.now(timezone.utc),
            },
        )
    elif job.status == "queued":
        _assign(job, values)
    await session.flush()
    return job


async def recover_stale_background_jobs(
    session: AsyncSession,
    *,
    job_types: list[str] | None = None,
    stale_after_minutes: int = 15,
) -> int:
    cutoff = datetime.now(timezone.utc) - timedelta(minutes=stale_after_minutes)
    query = (
        update(BackgroundJob)
        .where(
            BackgroundJob.status == "running",
            BackgroundJob.started_at.is_not(None),
            BackgroundJob.started_at < cutoff,
        )
        .values(
            status="queued",
            started_at=None,
            finished_at=None,
            error_message="Recovered stale running job.",
            available_at=datetime.now(timezone.utc),
        )
    )
    if job_types:
        query = query.where(BackgroundJob.job_type.in_(set(job_types)))
    result = await session.execute(query)
    await session.flush()
    return int(result.rowcount or 0)


async def queued_background_jobs(
    session: AsyncSession,
    *,
    limit: int = 10,
    job_types: list[str] | None = None,
) -> list[BackgroundJob]:
    query = (
        select(BackgroundJob)
        .where(
            BackgroundJob.status == "queued",
            BackgroundJob.available_at <= datetime.now(timezone.utc),
        )
        .order_by(BackgroundJob.priority.asc(), BackgroundJob.created_at.asc())
        .limit(limit)
    )
    if job_types:
        query = query.where(BackgroundJob.job_type.in_(set(job_types)))
    rows = await session.scalars(query)
    return list(rows)


async def mark_background_job_running(session: AsyncSession, job: BackgroundJob) -> BackgroundJob:
    job.status = "running"
    job.started_at = datetime.now(timezone.utc)
    job.attempts += 1
    job.error_message = None
    await session.flush()
    return job


async def finish_background_job(session: AsyncSession, job: BackgroundJob) -> BackgroundJob:
    job.status = "complete"
    job.finished_at = datetime.now(timezone.utc)
    job.error_message = None
    await session.flush()
    return job


async def fail_background_job(session: AsyncSession, job: BackgroundJob, message: str) -> BackgroundJob:
    job.status = "failed" if job.attempts >= job.max_attempts else "queued"
    job.error_message = message
    job.finished_at = datetime.now(timezone.utc) if job.status == "failed" else None
    job.available_at = datetime.now(timezone.utc)
    await session.flush()
    return job


async def team_by_provider_id(session: AsyncSession, provider_team_id: int | None) -> Team | None:
    if provider_team_id is None:
        return None
    return await session.scalar(
        select(Team).where(
            Team.provider == PROVIDER,
            Team.provider_team_id == provider_team_id,
        )
    )


async def link_team_visual_profile(session: AsyncSession, team: Team) -> None:
    visual_profile = await team_visual_profile_by_provider_id(session, team.provider_team_id)
    if visual_profile is not None and visual_profile.team_id != team.id:
        visual_profile.team_id = team.id
        await session.flush()


async def team_visual_profile_by_provider_id(
    session: AsyncSession,
    provider_team_id: int | None,
) -> TeamVisualProfile | None:
    if provider_team_id is None:
        return None
    return await session.scalar(
        select(TeamVisualProfile).where(
            TeamVisualProfile.provider == PROVIDER,
            TeamVisualProfile.provider_team_id == provider_team_id,
        )
    )


async def team_visual_profiles_by_provider_ids(
    session: AsyncSession,
    provider_team_ids: list[int],
) -> dict[int, TeamVisualProfile]:
    clean_ids = {int(team_id) for team_id in provider_team_ids if team_id is not None}
    if not clean_ids:
        return {}
    rows = await session.scalars(
        select(TeamVisualProfile).where(
            TeamVisualProfile.provider == PROVIDER,
            TeamVisualProfile.provider_team_id.in_(clean_ids),
        )
    )
    return {profile.provider_team_id: profile for profile in rows}


async def upsert_team_visual_profile(
    session: AsyncSession,
    team: Team,
    *,
    primary_colour: str,
    secondary_colour: str | None = None,
    colour_source: str,
    colour_status: str,
    raw_payload: dict[str, Any] | None = None,
) -> TeamVisualProfile:
    visual_profile = await team_visual_profile_by_provider_id(session, team.provider_team_id)
    values = {
        "team_id": team.id,
        "provider": PROVIDER,
        "provider_team_id": team.provider_team_id,
        "primary_colour": primary_colour,
        "secondary_colour": secondary_colour,
        "colour_source": colour_source,
        "colour_status": colour_status,
        "raw_payload": raw_payload,
    }
    if visual_profile is None:
        visual_profile = TeamVisualProfile(**values)
        session.add(visual_profile)
    else:
        _assign(visual_profile, values)
    await session.flush()
    return visual_profile


async def teams_by_provider_ids(session: AsyncSession, provider_team_ids: list[int]) -> dict[int, Team]:
    if not provider_team_ids:
        return {}
    rows = await session.scalars(
        select(Team).where(
            Team.provider == PROVIDER,
            Team.provider_team_id.in_(set(provider_team_ids)),
        )
    )
    return {team.provider_team_id: team for team in rows}


async def teams_for_visual_profile_backfill(session: AsyncSession, limit: int = 100) -> list[Team]:
    rows = await session.scalars(
        select(Team)
        .outerjoin(
            TeamVisualProfile,
            (TeamVisualProfile.provider == Team.provider)
            & (TeamVisualProfile.provider_team_id == Team.provider_team_id),
        )
        .where(
            Team.provider == PROVIDER,
            Team.logo_url.is_not(None),
            or_(
                TeamVisualProfile.id.is_(None),
                and_(
                    TeamVisualProfile.colour_status.not_in(PROTECTED_VISUAL_STATUSES),
                    TeamVisualProfile.colour_source.not_in(HANDLED_VISUAL_SOURCES),
                ),
                and_(
                    TeamVisualProfile.colour_source == "fallback_unknown",
                    TeamVisualProfile.raw_payload["reason"].astext.in_(RETRYABLE_VISUAL_UNKNOWN_REASONS),
                ),
            ),
        )
        .order_by(Team.name.asc())
        .limit(limit)
    )
    return list(rows)


async def team_profile_enrichment_for_team(
    session: AsyncSession,
    team: Team,
) -> TeamProfileEnrichment | None:
    return await session.scalar(
        select(TeamProfileEnrichment).where(TeamProfileEnrichment.team_id == team.id)
    )


async def team_profile_enrichment_by_provider_id(
    session: AsyncSession,
    provider_team_id: int | None,
) -> TeamProfileEnrichment | None:
    if provider_team_id is None:
        return None
    return await session.scalar(
        select(TeamProfileEnrichment).where(
            TeamProfileEnrichment.provider == PROVIDER,
            TeamProfileEnrichment.provider_team_id == provider_team_id,
        )
    )


async def upsert_team_profile_enrichment(
    session: AsyncSession,
    team: Team,
    *,
    wikidata_qid: str | None = None,
    wikipedia_title: str | None = None,
    wikipedia_url: str | None = None,
    summary: str | None = None,
    facts_json: dict[str, Any] | None = None,
    confidence: int | None = None,
    needs_review: bool = False,
    source_updated_at: datetime | None = None,
    attribution_url: str | None = None,
    license_label: str | None = None,
    raw_payload: dict[str, Any] | None = None,
) -> TeamProfileEnrichment:
    enrichment = await team_profile_enrichment_for_team(session, team)
    values = {
        "team_id": team.id,
        "provider": team.provider,
        "provider_team_id": team.provider_team_id,
        "wikidata_qid": wikidata_qid,
        "wikipedia_title": wikipedia_title,
        "wikipedia_url": wikipedia_url,
        "summary": summary,
        "facts_json": facts_json,
        "confidence": confidence,
        "needs_review": needs_review,
        "source_updated_at": source_updated_at,
        "attribution_url": attribution_url,
        "license_label": license_label,
        "raw_payload": raw_payload,
    }
    if enrichment is None:
        enrichment = TeamProfileEnrichment(**values)
        session.add(enrichment)
    else:
        _assign(enrichment, values)
    await session.flush()
    return enrichment


async def search_teams_by_terms(session: AsyncSession, terms: list[str], limit: int = 10) -> list[Team]:
    clean_terms = [term for term in terms if term]
    if not clean_terms:
        return []

    patterns = [f"%{term}%" for term in clean_terms]
    normalised_patterns = [
        f"%{normalise_search_text(term)}%"
        for term in clean_terms
        if normalise_search_text(term)
    ]
    normalised_name = normalised_sql_text(Team.name)
    normalised_code = normalised_sql_text(Team.code)
    clauses = []
    for pattern in patterns:
        clauses.extend([
            Team.name.ilike(pattern),
            Team.code.ilike(pattern),
        ])
    for pattern in normalised_patterns:
        clauses.extend([
            normalised_name.ilike(pattern),
            normalised_code.ilike(pattern),
        ])

    rows = await session.scalars(
        select(Team)
        .where(
            Team.provider == PROVIDER,
            or_(*clauses),
        )
        .order_by(Team.is_national.asc(), Team.name.asc())
        .limit(limit)
    )
    return list(rows)


async def competition_by_provider_id(session: AsyncSession, provider_competition_id: int) -> Competition | None:
    return await session.scalar(
        select(Competition).where(
            Competition.provider == PROVIDER,
            Competition.provider_competition_id == provider_competition_id,
        )
    )


async def recent_fixtures(
    session: AsyncSession,
    *,
    limit: int,
    provider_competition_id: int | None = None,
) -> list[Fixture]:
    query = (
        select(Fixture)
        .options(
            joinedload(Fixture.competition),
            joinedload(Fixture.season),
            joinedload(Fixture.home_team),
            joinedload(Fixture.away_team),
            joinedload(Fixture.youtube_comment_cache),
            joinedload(Fixture.event_sync_status),
            joinedload(Fixture.analysis_cache),
        )
        .join(Competition, Competition.id == Fixture.competition_id)
        .where(
            Fixture.provider == PROVIDER,
            Fixture.status_short.in_(FINISHED_STATUS_CODES),
        )
        .order_by(Fixture.kickoff_at.desc())
        .limit(limit)
    )
    if provider_competition_id is not None:
        query = query.where(Competition.provider_competition_id == provider_competition_id)
    rows = await session.scalars(query)
    return list(rows)


async def recent_finished_fixtures_needing_events(
    session: AsyncSession,
    *,
    limit: int,
) -> list[Fixture]:
    query = (
        select(Fixture)
        .options(
            joinedload(Fixture.competition),
            joinedload(Fixture.season),
            joinedload(Fixture.home_team),
            joinedload(Fixture.away_team),
            joinedload(Fixture.event_sync_status),
            joinedload(Fixture.analysis_cache),
        )
        .outerjoin(FixtureEventSyncStatus, FixtureEventSyncStatus.fixture_id == Fixture.id)
        .where(
            Fixture.provider == PROVIDER,
            Fixture.status_short.in_(FINISHED_STATUS_CODES),
            or_(
                FixtureEventSyncStatus.id.is_(None),
                FixtureEventSyncStatus.status.in_(("pending", "failed", "unknown")),
            ),
        )
        .order_by(Fixture.kickoff_at.desc())
        .limit(limit)
    )
    rows = await session.scalars(query)
    return list(rows)


async def search_fixtures(
    session: AsyncSession,
    *,
    query_text: str = "",
    query_terms: list[str] | None = None,
    provider_team_ids: list[int] | None = None,
    provider_competition_id: int | None = None,
    season_year: int | None = None,
) -> list[Fixture]:
    home_team = aliased(Team)
    away_team = aliased(Team)
    query = (
        select(Fixture)
        .options(
            joinedload(Fixture.competition),
            joinedload(Fixture.season),
            joinedload(Fixture.home_team),
            joinedload(Fixture.away_team),
            joinedload(Fixture.youtube_comment_cache),
            joinedload(Fixture.event_sync_status),
            joinedload(Fixture.analysis_cache),
        )
        .join(Competition, Competition.id == Fixture.competition_id)
        .join(Season, Season.id == Fixture.season_id)
        .join(home_team, home_team.id == Fixture.home_team_id)
        .join(away_team, away_team.id == Fixture.away_team_id)
        .where(
            Fixture.provider == PROVIDER,
            Fixture.status_short.in_(FINISHED_STATUS_CODES),
        )
        .order_by(Fixture.kickoff_at.desc())
    )
    if provider_competition_id is not None:
        query = query.where(Competition.provider_competition_id == provider_competition_id)
    if season_year is not None:
        query = query.where(Season.year == season_year)
    if provider_team_ids:
        query = query.where(
            or_(
                home_team.provider_team_id.in_(set(provider_team_ids)),
                away_team.provider_team_id.in_(set(provider_team_ids)),
            )
        )
    clean_terms = [" ".join(query_text.split())] if query_terms is None else query_terms
    clean_terms = [term for term in clean_terms if term]
    if clean_terms:
        patterns = [f"%{term}%" for term in clean_terms]
        normalised_patterns = [
            f"%{normalise_search_text(term)}%"
            for term in clean_terms
            if normalise_search_text(term)
        ]
        normalised_home_name = normalised_sql_text(home_team.name)
        normalised_away_name = normalised_sql_text(away_team.name)
        normalised_competition_name = normalised_sql_text(Competition.name)
        normalised_round_name = normalised_sql_text(Fixture.round_name)
        clauses = []
        for pattern in patterns:
            clauses.extend([
                home_team.name.ilike(pattern),
                away_team.name.ilike(pattern),
                home_team.code.ilike(pattern),
                away_team.code.ilike(pattern),
                Competition.name.ilike(pattern),
                Fixture.round_name.ilike(pattern),
            ])
        for pattern in normalised_patterns:
            clauses.extend([
                normalised_home_name.ilike(pattern),
                normalised_away_name.ilike(pattern),
                normalised_competition_name.ilike(pattern),
                normalised_round_name.ilike(pattern),
            ])
        query = query.where(or_(*clauses))
    rows = await session.scalars(query)
    return list(rows)


async def synced_competitions(session: AsyncSession) -> list[Competition]:
    rows = await session.scalars(
        select(Competition).where(
            Competition.provider == PROVIDER,
            Competition.is_active.is_(True),
        )
    )
    return list(rows)


async def competition_by_provider_id(
    session: AsyncSession,
    provider_competition_id: int,
) -> Competition | None:
    return await session.scalar(
        select(Competition).where(
            Competition.provider == PROVIDER,
            Competition.provider_competition_id == provider_competition_id,
            Competition.is_active.is_(True),
        )
    )


async def competition_season_by_provider_and_year(
    session: AsyncSession,
    provider_competition_id: int,
    season_year: int,
) -> CompetitionSeason | None:
    return await session.scalar(
        select(CompetitionSeason)
        .join(Competition, Competition.id == CompetitionSeason.competition_id)
        .join(Season, Season.id == CompetitionSeason.season_id)
        .options(joinedload(CompetitionSeason.season))
        .where(
            Competition.provider == PROVIDER,
            Competition.provider_competition_id == provider_competition_id,
            Season.year == season_year,
        )
    )


async def competition_finished_fixtures(
    session: AsyncSession,
    provider_competition_id: int,
    season_year: int,
) -> list[Fixture]:
    rows = await session.scalars(
        select(Fixture)
        .join(Competition, Competition.id == Fixture.competition_id)
        .join(Season, Season.id == Fixture.season_id)
        .options(
            joinedload(Fixture.competition),
            joinedload(Fixture.season),
            joinedload(Fixture.home_team),
            joinedload(Fixture.away_team),
            joinedload(Fixture.youtube_comment_cache),
            joinedload(Fixture.event_sync_status),
            joinedload(Fixture.analysis_cache),
        )
        .where(
            Fixture.provider == PROVIDER,
            Competition.provider_competition_id == provider_competition_id,
            Season.year == season_year,
            Fixture.status_short.in_(FINISHED_STATUS_CODES),
        )
        .order_by(Fixture.kickoff_at.desc())
    )
    return list(rows)


async def latest_team_season_year(
    session: AsyncSession,
    team_id: int,
    *,
    provider_competition_ids: set[int] | None = None,
) -> int | None:
    query = (
        select(func.max(Season.year))
        .join(Fixture, Fixture.season_id == Season.id)
        .join(Competition, Competition.id == Fixture.competition_id)
        .where(
            Fixture.provider == PROVIDER,
            or_(Fixture.home_team_id == team_id, Fixture.away_team_id == team_id),
        )
    )
    if provider_competition_ids:
        query = query.where(Competition.provider_competition_id.in_(provider_competition_ids))
    return await session.scalar(query)


async def team_finished_fixtures(
    session: AsyncSession,
    team_id: int,
    season_year: int,
    *,
    provider_competition_ids: set[int] | None = None,
) -> list[Fixture]:
    query = (
        select(Fixture)
        .join(Competition, Competition.id == Fixture.competition_id)
        .join(Season, Season.id == Fixture.season_id)
        .options(
            joinedload(Fixture.competition),
            joinedload(Fixture.season),
            joinedload(Fixture.home_team),
            joinedload(Fixture.away_team),
            joinedload(Fixture.youtube_comment_cache),
            joinedload(Fixture.event_sync_status),
            joinedload(Fixture.analysis_cache),
        )
        .where(
            Fixture.provider == PROVIDER,
            Season.year == season_year,
            Fixture.status_short.in_(FINISHED_STATUS_CODES),
            or_(Fixture.home_team_id == team_id, Fixture.away_team_id == team_id),
        )
    )
    if provider_competition_ids:
        query = query.where(Competition.provider_competition_id.in_(provider_competition_ids))
    rows = await session.scalars(query.order_by(Fixture.kickoff_at.desc()))
    return list(rows)


async def synced_competition_seasons(session: AsyncSession) -> list[Season]:
    rows = await session.scalars(
        select(Season)
        .join(CompetitionSeason, CompetitionSeason.season_id == Season.id)
        .join(Competition, Competition.id == CompetitionSeason.competition_id)
        .where(
            Competition.provider == PROVIDER,
            Competition.is_active.is_(True),
        )
        .distinct()
        .order_by(Season.year.desc())
    )
    return list(rows)


async def create_sync_run(
    session: AsyncSession,
    sync_type: str,
    *,
    season_year: int | None = None,
    provider_competition_id: int | None = None,
    sync_metadata: dict[str, Any] | None = None,
) -> SyncRun:
    sync_run = SyncRun(
        provider=PROVIDER,
        sync_type=sync_type,
        status="running",
        season_year=season_year,
        provider_competition_id=provider_competition_id,
        sync_metadata=sync_metadata,
    )
    session.add(sync_run)
    await session.flush()
    return sync_run


async def mark_running_syncs_failed(session: AsyncSession, sync_type: str, message: str) -> None:
    await session.execute(
        update(SyncRun)
        .where(SyncRun.provider == PROVIDER, SyncRun.sync_type == sync_type, SyncRun.status == "running")
        .values(status="failed", finished_at=datetime.now(timezone.utc), error_message=message)
    )
    await session.flush()


async def finish_sync_run(
    session: AsyncSession,
    sync_run: SyncRun,
    *,
    status: str,
    records_seen: int = 0,
    records_changed: int = 0,
    error_message: str | None = None,
) -> None:
    sync_run.status = status
    sync_run.finished_at = datetime.now(timezone.utc)
    sync_run.records_seen = records_seen
    sync_run.records_changed = records_changed
    sync_run.error_message = error_message
    await session.flush()


async def latest_sync_run(session: AsyncSession, sync_type: str) -> SyncRun | None:
    return await session.scalar(
        select(SyncRun)
        .where(SyncRun.provider == PROVIDER, SyncRun.sync_type == sync_type)
        .order_by(SyncRun.started_at.desc(), SyncRun.id.desc())
        .limit(1)
    )


async def latest_successful_sync_run(session: AsyncSession, sync_type: str) -> SyncRun | None:
    return await session.scalar(
        select(SyncRun)
        .where(
            SyncRun.provider == PROVIDER,
            SyncRun.sync_type == sync_type,
            SyncRun.status == "succeeded",
        )
        .order_by(SyncRun.finished_at.desc(), SyncRun.started_at.desc(), SyncRun.id.desc())
        .limit(1)
    )


async def archive_sync_status_for(
    session: AsyncSession,
    *,
    scope_type: str,
    provider_team_ids: list[int] | tuple[int, ...] | None = None,
    provider_competition_id: int | None = None,
    season_year: int | None = None,
) -> ArchiveSyncStatus | None:
    return await session.scalar(
        select(ArchiveSyncStatus).where(
            ArchiveSyncStatus.provider == PROVIDER,
            ArchiveSyncStatus.scope_key == archive_sync_scope_key(
                scope_type=scope_type,
                provider_team_ids=provider_team_ids,
                provider_competition_id=provider_competition_id,
                season_year=season_year,
            ),
        )
    )


async def upsert_archive_sync_status(
    session: AsyncSession,
    *,
    scope_type: str,
    provider_team_ids: list[int] | tuple[int, ...] | None = None,
    provider_competition_id: int | None = None,
    season_year: int | None = None,
    status: str,
    records_seen: int = 0,
    records_changed: int = 0,
    error_message: str | None = None,
    sync_metadata: dict[str, Any] | None = None,
    last_synced_at: datetime | None = None,
) -> ArchiveSyncStatus:
    team_ids = normalise_provider_team_ids(provider_team_ids)
    sync_status = await archive_sync_status_for(
        session,
        scope_type=scope_type,
        provider_team_ids=team_ids,
        provider_competition_id=provider_competition_id,
        season_year=season_year,
    )
    if last_synced_at is None and status in FINISHED_ARCHIVE_SYNC_STATUSES:
        last_synced_at = datetime.now(timezone.utc)

    values = {
        "provider": PROVIDER,
        "scope_type": scope_type,
        "scope_key": archive_sync_scope_key(
            scope_type=scope_type,
            provider_team_ids=team_ids,
            provider_competition_id=provider_competition_id,
            season_year=season_year,
        ),
        "provider_team_ids": team_ids or None,
        "provider_competition_id": provider_competition_id,
        "season_year": season_year,
        "status": status,
        "records_seen": records_seen,
        "records_changed": records_changed,
        "error_message": error_message,
        "sync_metadata": sync_metadata,
    }
    if last_synced_at is not None:
        values["last_synced_at"] = last_synced_at

    if sync_status is None:
        sync_status = ArchiveSyncStatus(**values)
        session.add(sync_status)
    else:
        _assign(sync_status, values)
    await session.flush()
    return sync_status


async def mark_archive_sync_pending(
    session: AsyncSession,
    *,
    scope_type: str,
    provider_team_ids: list[int] | tuple[int, ...] | None = None,
    provider_competition_id: int | None = None,
    season_year: int | None = None,
    sync_metadata: dict[str, Any] | None = None,
) -> ArchiveSyncStatus:
    return await upsert_archive_sync_status(
        session,
        scope_type=scope_type,
        provider_team_ids=provider_team_ids,
        provider_competition_id=provider_competition_id,
        season_year=season_year,
        status="pending",
        sync_metadata=sync_metadata,
    )


async def mark_archive_sync_complete(
    session: AsyncSession,
    *,
    scope_type: str,
    provider_team_ids: list[int] | tuple[int, ...] | None = None,
    provider_competition_id: int | None = None,
    season_year: int | None = None,
    records_seen: int = 0,
    records_changed: int = 0,
    sync_metadata: dict[str, Any] | None = None,
) -> ArchiveSyncStatus:
    return await upsert_archive_sync_status(
        session,
        scope_type=scope_type,
        provider_team_ids=provider_team_ids,
        provider_competition_id=provider_competition_id,
        season_year=season_year,
        status="complete",
        records_seen=records_seen,
        records_changed=records_changed,
        sync_metadata=sync_metadata,
    )


async def mark_archive_sync_failed(
    session: AsyncSession,
    *,
    scope_type: str,
    provider_team_ids: list[int] | tuple[int, ...] | None = None,
    provider_competition_id: int | None = None,
    season_year: int | None = None,
    error_message: str,
    records_seen: int = 0,
    records_changed: int = 0,
    sync_metadata: dict[str, Any] | None = None,
) -> ArchiveSyncStatus:
    return await upsert_archive_sync_status(
        session,
        scope_type=scope_type,
        provider_team_ids=provider_team_ids,
        provider_competition_id=provider_competition_id,
        season_year=season_year,
        status="failed",
        records_seen=records_seen,
        records_changed=records_changed,
        error_message=error_message,
        sync_metadata=sync_metadata,
    )


async def table_counts(session: AsyncSession) -> dict[str, int]:
    tables = {
        "competitions": Competition,
        "seasons": Season,
        "competition_seasons": CompetitionSeason,
        "teams": Team,
        "team_visual_profiles": TeamVisualProfile,
        "fixtures": Fixture,
        "fixture_events": FixtureEvent,
        "fixture_event_sync_status": FixtureEventSyncStatus,
        "youtube_comment_cache": YouTubeCommentCache,
        "analysis_cache": AnalysisCache,
        "background_jobs": BackgroundJob,
        "sync_runs": SyncRun,
        "archive_sync_status": ArchiveSyncStatus,
    }
    counts: dict[str, int] = {}
    for name, model in tables.items():
        counts[name] = int(await session.scalar(select(func.count()).select_from(model)) or 0)
    return counts


def season_label(year: int) -> str:
    return f"{year}/{str(year + 1)[-2:]}"


def archive_sync_scope_key(
    *,
    scope_type: str,
    provider_team_ids: list[int] | tuple[int, ...] | None = None,
    provider_competition_id: int | None = None,
    season_year: int | None = None,
) -> str:
    team_ids = normalise_provider_team_ids(provider_team_ids)
    teams_part = ",".join(str(team_id) for team_id in team_ids) if team_ids else "*"
    competition_part = str(provider_competition_id) if provider_competition_id is not None else "*"
    season_part = str(season_year) if season_year is not None else "*"
    return f"{scope_type}|teams={teams_part}|competition={competition_part}|season={season_part}"


def is_stale_background_job(job: BackgroundJob, *, stale_after_minutes: int = 15) -> bool:
    if job.started_at is None:
        return False
    started_at = job.started_at
    if started_at.tzinfo is None:
        started_at = started_at.replace(tzinfo=timezone.utc)
    return started_at < datetime.now(timezone.utc) - timedelta(minutes=stale_after_minutes)


def normalise_provider_team_ids(provider_team_ids: list[int] | tuple[int, ...] | None) -> list[int]:
    if not provider_team_ids:
        return []
    return sorted({int(team_id) for team_id in provider_team_ids if team_id is not None})


def parse_date(value: Any) -> date | None:
    if not value:
        return None
    try:
        return date.fromisoformat(str(value)[:10])
    except ValueError:
        return None


def parse_datetime(value: Any) -> datetime:
    if not value:
        return datetime.now(timezone.utc)
    clean = str(value).replace("Z", "+00:00")
    try:
        parsed = datetime.fromisoformat(clean)
    except ValueError:
        return datetime.now(timezone.utc)
    if parsed.tzinfo is None:
        return parsed.replace(tzinfo=timezone.utc)
    return parsed


def safe_int(value: Any) -> int | None:
    if value is None or value == "":
        return None
    try:
        return int(value)
    except (TypeError, ValueError):
        return None


def normalise_search_text(value: str) -> str:
    normalised = unicodedata.normalize("NFKD", value or "")
    ascii_value = normalised.encode("ascii", "ignore").decode("ascii")
    return " ".join(re.sub(r"[^a-zA-Z0-9]+", " ", ascii_value).lower().split())


def normalised_sql_text(column: Any) -> Any:
    return func.regexp_replace(func.lower(func.coalesce(column, "")), r"[^a-z0-9]+", " ", "g")


def team_values(raw: dict[str, Any]) -> dict[str, Any]:
    team_data = raw.get("team") or raw
    venue = raw.get("venue") or {}
    provider_id = int(team_data["id"])
    return {
        "provider": PROVIDER,
        "provider_team_id": provider_id,
        "name": team_data.get("name") or f"Team {provider_id}",
        "code": team_data.get("code"),
        "country_name": team_data.get("country"),
        "founded": safe_int(team_data.get("founded")),
        "is_national": bool(team_data.get("national")),
        "logo_url": team_data.get("logo"),
        "venue_name": venue.get("name"),
        "venue_city": venue.get("city"),
        "venue_image_url": venue.get("image"),
        "raw_payload": raw,
    }


def fixture_values(
    raw: dict[str, Any],
    competition: Competition,
    season: Season,
    home_team: Team,
    away_team: Team,
) -> dict[str, Any]:
    fixture_data = raw.get("fixture") or {}
    status = fixture_data.get("status") or {}
    venue = fixture_data.get("venue") or {}
    goals = raw.get("goals") or {}
    score = raw.get("score") or {}
    halftime = score.get("halftime") or {}
    fulltime = score.get("fulltime") or {}
    extratime = score.get("extratime") or {}
    penalty = score.get("penalty") or {}
    return {
        "provider": PROVIDER,
        "provider_fixture_id": int(fixture_data["id"]),
        "competition_id": competition.id,
        "season_id": season.id,
        "home_team_id": home_team.id,
        "away_team_id": away_team.id,
        "kickoff_at": parse_datetime(fixture_data.get("date")),
        "status_short": status.get("short"),
        "status_long": status.get("long"),
        "elapsed": safe_int(status.get("elapsed")),
        "round_name": (raw.get("league") or {}).get("round"),
        "venue_name": venue.get("name"),
        "venue_city": venue.get("city"),
        "home_goals": safe_int(goals.get("home")),
        "away_goals": safe_int(goals.get("away")),
        "halftime_home_goals": safe_int(halftime.get("home")),
        "halftime_away_goals": safe_int(halftime.get("away")),
        "fulltime_home_goals": safe_int(fulltime.get("home")),
        "fulltime_away_goals": safe_int(fulltime.get("away")),
        "extratime_home_goals": safe_int(extratime.get("home")),
        "extratime_away_goals": safe_int(extratime.get("away")),
        "penalty_home_goals": safe_int(penalty.get("home")),
        "penalty_away_goals": safe_int(penalty.get("away")),
        "raw_payload": raw,
    }


def _assign(model: Any, values: dict[str, Any]) -> None:
    for key, value in values.items():
        setattr(model, key, value)
