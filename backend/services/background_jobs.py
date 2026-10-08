"""Persistent background job queue for slow provider work."""

from __future__ import annotations

from datetime import datetime, timedelta, timezone
from typing import Any

from sqlalchemy import select
from sqlalchemy.orm import joinedload, selectinload

from backend.database.models import Fixture, FixtureEvent
from backend.database.session import get_sessionmaker
from backend.models.schemas import MatchSummary
from backend.repositories import football_data as repo
from backend.services.matches import fixture_to_summary, hydrate_fixture_events, visual_profiles_for_fixtures
from backend.services.sync import ensure_archive_scope_synced
from backend.services.team_honours import ensure_team_honours
from backend.services.team_profile_content import profile_content_for
from backend.services.team_profiles import profile_enrichment
from backend.services.youtube_cache import fetch_and_cache_youtube_comment_count

JOB_ARCHIVE_HYDRATION = "archive_hydration"
JOB_FIXTURE_EVENTS = "fixture_events_hydration"
JOB_YOUTUBE_COMMENT_COUNT = "youtube_comment_count"
JOB_TEAM_HONOURS = "team_honours"
YOUTUBE_FAILED_RETRY_AFTER = timedelta(hours=24)
YOUTUBE_RATE_LIMIT_RETRY_AFTER = timedelta(hours=12)


async def queue_youtube_comment_count_jobs(matches: list[MatchSummary]) -> int:
    """Queue comment-count jobs for unchecked match result cards."""

    provider_ids = provider_fixture_ids_from_matches(matches)
    if not provider_ids:
        return 0

    queued = 0
    sessionmaker = get_sessionmaker()
    async with sessionmaker() as session:
        fixtures = await repo.fixtures_by_provider_fixture_ids(session, provider_ids)
        fixtures_by_provider_id = {fixture.provider_fixture_id: fixture for fixture in fixtures}
        for match in matches:
            fixture = fixtures_by_provider_id.get(safe_int(match.id))
            if fixture is None or not should_queue_youtube_count(fixture):
                continue
            await repo.upsert_youtube_comment_cache(
                session,
                fixture,
                status="pending",
                raw_payload={"source": "background_job_queue", "match_id": match.id},
            )
            await repo.enqueue_background_job(
                session,
                job_type=JOB_YOUTUBE_COMMENT_COUNT,
                job_key=str(fixture.provider_fixture_id),
                payload={"provider_fixture_id": fixture.provider_fixture_id},
                priority=100,
            )
            match.youtube_comment_status = "pending"
            queued += 1
        await session.commit()
    return queued


async def queue_fixture_event_job(provider_fixture_id: int, *, priority: int = 50) -> None:
    """Queue fixture-event hydration for one locally cached fixture."""

    sessionmaker = get_sessionmaker()
    async with sessionmaker() as session:
        fixture = await repo.fixture_by_provider_fixture_id(session, provider_fixture_id)
        if fixture is None:
            return
        if fixture.event_sync_status is not None and fixture.event_sync_status.status == "complete":
            return
        await repo.upsert_fixture_event_sync_status(
            session,
            fixture,
            status="pending",
            raw_payload={"source": "background_job_queue", "provider_fixture_id": provider_fixture_id},
        )
        await repo.enqueue_background_job(
            session,
            job_type=JOB_FIXTURE_EVENTS,
            job_key=str(provider_fixture_id),
            payload={"provider_fixture_id": provider_fixture_id},
            priority=priority,
        )
        await session.commit()


async def queue_archive_hydration_job(
    *,
    scope_type: str,
    provider_team_ids: list[int] | None = None,
    provider_competition_id: int | None = None,
    season_year: int | None = None,
    priority: int = 200,
) -> None:
    """Queue archive hydration using the same scope payload as archive sync."""

    payload = {
        "scope_type": scope_type,
        "provider_team_ids": provider_team_ids or [],
        "provider_competition_id": provider_competition_id,
        "season_year": season_year,
    }
    job_key = repo.archive_sync_scope_key(
        scope_type=scope_type,
        provider_team_ids=provider_team_ids,
        provider_competition_id=provider_competition_id,
        season_year=season_year,
    )
    sessionmaker = get_sessionmaker()
    async with sessionmaker() as session:
        await repo.enqueue_background_job(
            session,
            job_type=JOB_ARCHIVE_HYDRATION,
            job_key=job_key,
            payload=payload,
            priority=priority,
        )
        await session.commit()


async def process_queued_jobs(
    *,
    limit: int = 10,
    job_types: list[str] | None = None,
) -> dict[str, int]:
    """Run queued background jobs once and return processing counts."""

    stats = {"seen": 0, "complete": 0, "failed": 0}
    sessionmaker = get_sessionmaker()
    async with sessionmaker() as session:
        await repo.recover_stale_background_jobs(session, job_types=job_types)
        await session.commit()
        jobs = await repo.queued_background_jobs(session, limit=limit, job_types=job_types)
        stats["seen"] = len(jobs)

    for job in jobs:
        async with sessionmaker() as session:
            current = await session.get(type(job), job.id)
            if current is None or current.status != "queued":
                continue
            await repo.mark_background_job_running(session, current)
            await session.commit()
            try:
                await process_job(current.job_type, current.payload or {})
            except Exception as exc:
                await repo.fail_background_job(session, current, str(exc))
                stats["failed"] += 1
            else:
                await repo.finish_background_job(session, current)
                stats["complete"] += 1
            await session.commit()

    return stats


async def process_job(job_type: str, payload: dict[str, Any]) -> None:
    if job_type == JOB_YOUTUBE_COMMENT_COUNT:
        await process_youtube_comment_count_job(payload)
        return
    if job_type == JOB_FIXTURE_EVENTS:
        await process_fixture_events_job(payload)
        return
    if job_type == JOB_ARCHIVE_HYDRATION:
        await process_archive_hydration_job(payload)
        return
    if job_type == JOB_TEAM_HONOURS:
        await process_team_honours_job(payload)
        return
    raise ValueError(f"Unsupported background job type: {job_type}")


async def process_youtube_comment_count_job(payload: dict[str, Any]) -> None:
    provider_fixture_id = int(payload["provider_fixture_id"])
    match = await match_summary_for_provider_fixture_id(provider_fixture_id)
    if match is None:
        raise ValueError(f"Fixture {provider_fixture_id} is not cached locally.")
    await fetch_and_cache_youtube_comment_count(match)


async def process_fixture_events_job(payload: dict[str, Any]) -> None:
    provider_fixture_id = int(payload["provider_fixture_id"])
    sessionmaker = get_sessionmaker()
    async with sessionmaker() as session:
        fixture = await session.scalar(
            select(Fixture)
            .options(
                joinedload(Fixture.competition),
                joinedload(Fixture.season),
                joinedload(Fixture.home_team),
                joinedload(Fixture.away_team),
                selectinload(Fixture.events).joinedload(FixtureEvent.team),
            )
            .where(
                Fixture.provider == repo.PROVIDER,
                Fixture.provider_fixture_id == provider_fixture_id,
            )
        )
        if fixture is None:
            raise ValueError(f"Fixture {provider_fixture_id} is not cached locally.")
        if not fixture.events:
            await hydrate_fixture_events(session, fixture)
        else:
            await repo.upsert_fixture_event_sync_status(
                session,
                fixture,
                status="complete",
                event_count=len(fixture.events),
                raw_payload={
                    "source": "local_fixture_events",
                    "provider_fixture_id": provider_fixture_id,
                },
            )
        await session.commit()


async def process_archive_hydration_job(payload: dict[str, Any]) -> None:
    await ensure_archive_scope_synced(
        scope_type=payload["scope_type"],
        provider_team_ids=payload.get("provider_team_ids") or [],
        provider_competition_id=payload.get("provider_competition_id"),
        season_year=payload.get("season_year"),
    )


async def process_team_honours_job(payload: dict[str, Any]) -> None:
    """Resolve and cache major honours for one stored club."""

    provider_team_id = int(payload["provider_team_id"])
    sessionmaker = get_sessionmaker()
    async with sessionmaker() as session:
        team = await repo.team_by_provider_id(session, provider_team_id)
        if team is None:
            raise ValueError(f"Team {provider_team_id} is not cached locally.")
        enrichment = await profile_enrichment(session, team)
        honours = await ensure_team_honours(
            team,
            enrichment,
            manual_content=profile_content_for(team.name),
        )
        if honours is None:
            raise ValueError(f"Major honours could not be resolved for team {provider_team_id}.")
        await session.commit()


async def match_summary_for_provider_fixture_id(provider_fixture_id: int) -> MatchSummary | None:
    sessionmaker = get_sessionmaker()
    async with sessionmaker() as session:
        fixture = await session.scalar(
            select(Fixture)
            .options(
                joinedload(Fixture.competition),
                joinedload(Fixture.season),
                joinedload(Fixture.home_team),
                joinedload(Fixture.away_team),
                joinedload(Fixture.youtube_comment_cache),
                joinedload(Fixture.event_sync_status),
            )
            .where(
                Fixture.provider == repo.PROVIDER,
                Fixture.provider_fixture_id == provider_fixture_id,
            )
        )
        if fixture is None:
            return None
        visual_profiles = await visual_profiles_for_fixtures(session, [fixture])
        await session.commit()
        return fixture_to_summary(fixture, visual_profiles=visual_profiles)


def provider_fixture_ids_from_matches(matches: list[MatchSummary]) -> list[int]:
    return [provider_id for provider_id in [safe_int(match.id) for match in matches] if provider_id is not None]


def should_queue_youtube_count(fixture: Fixture) -> bool:
    cache = fixture.youtube_comment_cache
    if cache is None:
        return True
    if cache.status == "unchecked":
        return True
    if cache.status == "pending":
        return True
    if cache.status == "failed":
        return cache_is_retryable(cache.checked_at, YOUTUBE_FAILED_RETRY_AFTER)
    if cache.status == "rate_limited":
        return cache_is_retryable(cache.checked_at, YOUTUBE_RATE_LIMIT_RETRY_AFTER)
    return False


def cache_is_retryable(checked_at: datetime | None, retry_after: timedelta) -> bool:
    if checked_at is None:
        return True
    if checked_at.tzinfo is None:
        checked_at = checked_at.replace(tzinfo=timezone.utc)
    return datetime.now(timezone.utc) - checked_at >= retry_after


def safe_int(value: object) -> int | None:
    try:
        return int(value)
    except (TypeError, ValueError):
        return None
