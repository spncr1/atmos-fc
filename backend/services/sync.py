"""Sync API-Football data into the local database."""

from __future__ import annotations

from collections.abc import AsyncIterator
from contextlib import asynccontextmanager
from dataclasses import dataclass
from datetime import date, datetime, timedelta, timezone
from typing import Any

from sqlalchemy import text
from sqlalchemy.ext.asyncio import AsyncSession

from backend.database.session import get_async_engine, get_sessionmaker
from backend.database.models import Competition, Fixture, Season, Team
from backend.providers.api_football import ApiFootballClient
from backend.providers.errors import ProviderRequestError, ProviderResponseError
from backend.repositories import football_data as repo


@dataclass(frozen=True)
class CompetitionTarget:
    provider_id: int
    label: str


CORE_COMPETITIONS = [
    CompetitionTarget(39, "Premier League"),
    CompetitionTarget(140, "La Liga"),
    CompetitionTarget(78, "Bundesliga"),
    CompetitionTarget(135, "Serie A"),
    CompetitionTarget(61, "Ligue 1"),
    CompetitionTarget(88, "Eredivisie"),
    CompetitionTarget(94, "Liga Portugal"),
    CompetitionTarget(144, "Belgian Pro League"),
    CompetitionTarget(203, "Turkish Super Lig"),
    CompetitionTarget(2, "UEFA Champions League"),
    CompetitionTarget(3, "UEFA Europa League"),
    CompetitionTarget(848, "UEFA Conference League"),
]
CORE_COMPETITION_IDS = {target.provider_id for target in CORE_COMPETITIONS}
ARCHIVE_START_SEASON = 2010
API_FOOTBALL_CORE_SYNC_TYPE = "api_football_core"
API_FOOTBALL_EVENTS_SYNC_TYPE = "api_football_events"
API_FOOTBALL_CORE_LOCK_ID = 2026081401
API_FOOTBALL_EVENTS_LOCK_ID = 2026082801
FIXTURE_SYNC_STALE_AFTER = timedelta(hours=2)


@asynccontextmanager
async def api_football_core_sync_lock() -> AsyncIterator[bool]:
    engine = get_async_engine()
    async with engine.connect() as connection:
        acquired = bool(
            await connection.scalar(
                text("select pg_try_advisory_lock(:lock_id)"),
                {"lock_id": API_FOOTBALL_CORE_LOCK_ID},
            )
        )
        if not acquired:
            yield False
            return

        try:
            yield True
        finally:
            await connection.execute(
                text("select pg_advisory_unlock(:lock_id)"),
                {"lock_id": API_FOOTBALL_CORE_LOCK_ID},
            )


@asynccontextmanager
async def api_football_events_sync_lock() -> AsyncIterator[bool]:
    engine = get_async_engine()
    async with engine.connect() as connection:
        acquired = bool(
            await connection.scalar(
                text("select pg_try_advisory_lock(:lock_id)"),
                {"lock_id": API_FOOTBALL_EVENTS_LOCK_ID},
            )
        )
        if not acquired:
            yield False
            return

        try:
            yield True
        finally:
            await connection.execute(
                text("select pg_advisory_unlock(:lock_id)"),
                {"lock_id": API_FOOTBALL_EVENTS_LOCK_ID},
            )


async def sync_core_football_data(
    *,
    season_years: list[int] | None = None,
    recent_limit: int | None = 3,
    include_events: bool = True,
    competition_ids: list[int] | None = None,
) -> dict[str, Any]:
    """Sync the first production MVP slice from API-Football into Postgres."""

    async with api_football_core_sync_lock() as lock_acquired:
        if not lock_acquired:
            return {
                "status": "skipped",
                "skip_reason": "api_football_core sync already running.",
                "records_seen": 0,
                "records_changed": 0,
                "synced": {},
                "sync_type": API_FOOTBALL_CORE_SYNC_TYPE,
            }

        client = ApiFootballClient(min_interval_seconds=7.0)
        sessionmaker = get_sessionmaker()
        async with sessionmaker() as session:
            await repo.mark_running_syncs_failed(
                session,
                API_FOOTBALL_CORE_SYNC_TYPE,
                "Superseded by a new sync run.",
            )
            sync_run = await repo.create_sync_run(
                session,
                API_FOOTBALL_CORE_SYNC_TYPE,
                sync_metadata={
                    "requested_seasons": season_years,
                    "recent_limit": recent_limit,
                    "include_events": include_events,
                    "competition_ids": competition_ids,
                },
            )
            await session.commit()
            try:
                result = await _sync_core(session, client, season_years, recent_limit, include_events, competition_ids)
            except Exception as exc:
                await session.rollback()
                await repo.finish_sync_run(session, sync_run, status="failed", error_message=str(exc))
                await session.commit()
                raise

            await repo.finish_sync_run(
                session,
                sync_run,
                status="succeeded",
                records_seen=result["records_seen"],
                records_changed=result["records_changed"],
            )
            result["table_counts"] = await repo.table_counts(session)
            await session.commit()
            return result


async def sync_recent_fixture_events(
    *,
    limit: int = 50,
) -> dict[str, Any]:
    """Hydrate event timelines for recent finished API-Football fixtures."""

    clean_limit = max(1, limit)
    async with api_football_events_sync_lock() as lock_acquired:
        if not lock_acquired:
            return {
                "status": "skipped",
                "skip_reason": "api_football_events sync already running.",
                "records_seen": 0,
                "records_changed": 0,
                "synced": {},
                "sync_type": API_FOOTBALL_EVENTS_SYNC_TYPE,
            }

        sessionmaker = get_sessionmaker()
        async with sessionmaker() as session:
            await repo.mark_running_syncs_failed(
                session,
                API_FOOTBALL_EVENTS_SYNC_TYPE,
                "Superseded by a new event sync run.",
            )
            sync_run = await repo.create_sync_run(
                session,
                API_FOOTBALL_EVENTS_SYNC_TYPE,
                sync_metadata={"limit": clean_limit},
            )
            await session.commit()
            try:
                client = ApiFootballClient(min_interval_seconds=7.0)
                await client.status()
                result = await _sync_recent_fixture_events(session, client, clean_limit)
            except Exception as exc:
                await session.rollback()
                await repo.finish_sync_run(session, sync_run, status="failed", error_message=str(exc))
                await session.commit()
                raise

            await repo.finish_sync_run(
                session,
                sync_run,
                status="succeeded",
                records_seen=result["records_seen"],
                records_changed=result["records_changed"],
            )
            await session.commit()
            result["status"] = "succeeded"
            result["sync_type"] = API_FOOTBALL_EVENTS_SYNC_TYPE
            return result


async def _sync_recent_fixture_events(
    session: AsyncSession,
    client: ApiFootballClient,
    limit: int,
) -> dict[str, Any]:
    fixtures = await repo.recent_finished_fixtures_needing_events(session, limit=limit)
    records_seen = len(fixtures)
    records_changed = 0
    synced = {
        "fixtures_checked": len(fixtures),
        "event_requests": 0,
        "fixture_events": 0,
        "complete": 0,
        "unavailable": 0,
        "failed": 0,
    }

    for fixture in fixtures:
        try:
            raw_events = await client.fixture_events(fixture.provider_fixture_id)
        except (ProviderRequestError, ProviderResponseError) as exc:
            await repo.upsert_fixture_event_sync_status(
                session,
                fixture,
                status="failed",
                error_message=str(exc),
                raw_payload={
                    "source": "api_football_events_sync",
                    "provider_fixture_id": fixture.provider_fixture_id,
                },
            )
            synced["failed"] += 1
            records_changed += 1
            continue

        synced["event_requests"] += 1
        records_seen += len(raw_events)
        event_count = await repo.replace_fixture_events(session, fixture, raw_events)
        await repo.upsert_fixture_event_sync_status(
            session,
            fixture,
            status="complete" if event_count else "unavailable",
            event_count=event_count,
            raw_payload={
                "source": "api_football_events_sync",
                "provider_fixture_id": fixture.provider_fixture_id,
            },
        )
        synced["fixture_events"] += event_count
        synced["complete" if event_count else "unavailable"] += 1
        records_changed += event_count + 1

    return {
        "records_seen": records_seen,
        "records_changed": records_changed,
        "synced": synced,
    }


async def fixture_sync_status() -> dict[str, Any]:
    """Return whether the API-Football fixture sync is keeping the DB fresh."""

    sessionmaker = get_sessionmaker()
    now = datetime.now(timezone.utc)
    async with sessionmaker() as session:
        latest_run = await repo.latest_sync_run(session, API_FOOTBALL_CORE_SYNC_TYPE)
        latest_success = await repo.latest_successful_sync_run(session, API_FOOTBALL_CORE_SYNC_TYPE)
        latest_payload = sync_run_payload(latest_run)
        latest_success_payload = sync_run_payload(latest_success)
        last_success_finished_at = aware_utc(latest_success.finished_at) if latest_success else None

    seconds_since_success = None
    is_fresh = False
    if last_success_finished_at is not None:
        seconds_since_success = max(0, int((now - last_success_finished_at).total_seconds()))
        is_fresh = seconds_since_success <= int(FIXTURE_SYNC_STALE_AFTER.total_seconds())

    status = "missing"
    message = "No API-Football fixture sync has run yet."
    if latest_run and latest_run.status == "running":
        status = "running"
        message = "API-Football fixture sync is currently running."
    elif latest_success and is_fresh:
        status = "fresh"
        message = "API-Football fixture data was synced recently."
    elif latest_success:
        status = "stale"
        message = "API-Football fixture data is stale; the fixture sync worker should run."
    elif latest_run and latest_run.status == "failed":
        status = "failed"
        message = "No successful API-Football fixture sync has completed."

    return {
        "sync_type": API_FOOTBALL_CORE_SYNC_TYPE,
        "status": status,
        "is_fresh": is_fresh,
        "checked_at": now.isoformat(),
        "stale_after_seconds": int(FIXTURE_SYNC_STALE_AFTER.total_seconds()),
        "seconds_since_success": seconds_since_success,
        "last_success_finished_at": (
            last_success_finished_at.isoformat() if last_success_finished_at is not None else None
        ),
        "latest_run": latest_payload,
        "latest_successful_run": latest_success_payload,
        "message": message,
    }


def sync_run_payload(sync_run: Any | None) -> dict[str, Any] | None:
    if sync_run is None:
        return None
    return {
        "id": sync_run.id,
        "provider": sync_run.provider,
        "sync_type": sync_run.sync_type,
        "status": sync_run.status,
        "started_at": datetime_payload(sync_run.started_at),
        "finished_at": datetime_payload(sync_run.finished_at),
        "records_seen": sync_run.records_seen,
        "records_changed": sync_run.records_changed,
        "error_message": sync_run.error_message,
        "metadata": sync_run.sync_metadata,
    }


def datetime_payload(value: datetime | None) -> str | None:
    clean = aware_utc(value)
    return clean.isoformat() if clean is not None else None


def aware_utc(value: datetime | None) -> datetime | None:
    if value is None:
        return None
    if value.tzinfo is None:
        return value.replace(tzinfo=timezone.utc)
    return value.astimezone(timezone.utc)


async def _sync_core(
    session: AsyncSession,
    client: ApiFootballClient,
    season_years: list[int] | None,
    recent_limit: int | None,
    include_events: bool,
    competition_ids: list[int] | None,
) -> dict[str, Any]:
    api_seasons = await client.seasons()
    selected_seasons = select_sync_seasons(api_seasons, season_years)
    records_seen = len(api_seasons)
    records_changed = 0
    synced = {
        "api_seasons": len(api_seasons),
        "selected_seasons": selected_seasons,
        "competitions": 0,
        "competition_seasons": 0,
        "teams": 0,
        "fixtures": 0,
        "fixture_events": 0,
        "event_requests": 0,
    }

    current_year = current_european_season_year()
    for season_year in api_seasons:
        await repo.upsert_season(session, season_year, is_current=season_year == current_year)
        records_changed += 1

    targets = [
        target for target in CORE_COMPETITIONS
        if not competition_ids or target.provider_id in set(competition_ids)
    ]

    for target in targets:
        for season_year in selected_seasons:
            league_rows = await client.leagues(league_id=target.provider_id, season=season_year)
            records_seen += len(league_rows)
            if not league_rows:
                continue

            competition = await repo.upsert_competition(session, league_rows[0])
            season = await repo.upsert_season(session, season_year, is_current=season_year == current_year)
            synced["competitions"] += 1
            records_changed += 2

            raw_season = league_season_payload(league_rows[0], season_year)
            await repo.upsert_competition_season(session, competition, season, raw_season)
            synced["competition_seasons"] += 1
            records_changed += 1

            team_rows = await client.teams(league_id=target.provider_id, season=season_year)
            records_seen += len(team_rows)
            teams_by_provider_id = await repo.upsert_teams(session, team_rows)
            records_changed += len(team_rows)
            synced["teams"] += len(team_rows)

            fixture_args = {
                "league_id": target.provider_id,
                "season": season_year,
                "status": "FT-AET-PEN",
            }
            if recent_limit is not None:
                fixture_args["last"] = max(1, recent_limit)
            fixtures = await client.fixtures(**fixture_args)
            records_seen += len(fixtures)
            if include_events:
                for raw_fixture in fixtures:
                    fixture, new_teams = await sync_fixture(session, raw_fixture, competition, season, teams_by_provider_id)
                    synced["fixtures"] += 1
                    synced["teams"] += new_teams
                    records_changed += 1 + new_teams

                    events = await client.fixture_events(fixture.provider_fixture_id)
                    synced["event_requests"] += 1
                    synced["fixture_events"] += await repo.replace_fixture_events(session, fixture, events)
                    records_seen += len(events)
                    records_changed += len(events)
            else:
                _, new_teams = await repo.upsert_fixtures(session, fixtures, competition, season, teams_by_provider_id)
                synced["fixtures"] += len(fixtures)
                synced["teams"] += new_teams
                records_changed += len(fixtures) + new_teams

    return {
        "records_seen": records_seen,
        "records_changed": records_changed,
        "synced": synced,
    }


async def ensure_archive_scope_synced(
    *,
    scope_type: str,
    provider_team_ids: list[int] | tuple[int, ...] | None = None,
    provider_competition_id: int | None = None,
    season_year: int | None = None,
    is_archive_addressable: bool = True,
    start_year: int = ARCHIVE_START_SEASON,
) -> dict[str, Any]:
    """Ensure the exact archive slice behind a results-page search is cached."""

    if not is_archive_addressable:
        return empty_hydration_result("Archive scope is not addressable.")
    if scope_type == "supported_all_seasons":
        return empty_hydration_result("All competitions across all seasons is too broad to hydrate automatically.")

    team_ids = repo.normalise_provider_team_ids(provider_team_ids)
    sessionmaker = get_sessionmaker()
    async with sessionmaker() as session:
        existing = await repo.archive_sync_status_for(
            session,
            scope_type=scope_type,
            provider_team_ids=team_ids,
            provider_competition_id=provider_competition_id,
            season_year=season_year,
        )
        if existing is not None and existing.status in {"complete", "provider_unavailable"} and not should_recheck_archive_status(
            existing.status,
            existing.records_changed,
            existing.sync_metadata,
            provider_competition_id,
            season_year,
        ):
            return {
                "records_seen": existing.records_seen,
                "records_changed": existing.records_changed,
                "synced": existing.sync_metadata or {},
                "status": existing.status,
                "skipped": "Archive scope already checked.",
            }

        await repo.mark_archive_sync_pending(
            session,
            scope_type=scope_type,
            provider_team_ids=team_ids,
            provider_competition_id=provider_competition_id,
            season_year=season_year,
            sync_metadata={"previous_status": existing.status if existing else None},
        )
        await session.commit()

        try:
            client = ApiFootballClient()
            result = await _hydrate_archive_scope(
                session,
                client,
                scope_type=scope_type,
                provider_team_ids=team_ids,
                provider_competition_id=provider_competition_id,
                season_year=season_year,
                start_year=start_year,
            )
        except Exception as exc:
            await session.rollback()
            await repo.mark_archive_sync_failed(
                session,
                scope_type=scope_type,
                provider_team_ids=team_ids,
                provider_competition_id=provider_competition_id,
                season_year=season_year,
                error_message=str(exc),
            )
            await session.commit()
            raise

        status = archive_sync_status_from_result(result)
        await repo.upsert_archive_sync_status(
            session,
            scope_type=scope_type,
            provider_team_ids=team_ids,
            provider_competition_id=provider_competition_id,
            season_year=season_year,
            status=status,
            records_seen=result["records_seen"],
            records_changed=result["records_changed"],
            sync_metadata=result["synced"],
        )
        await session.commit()
        result["status"] = status
        return result


async def _hydrate_archive_scope(
    session: AsyncSession,
    client: ApiFootballClient,
    *,
    scope_type: str,
    provider_team_ids: list[int],
    provider_competition_id: int | None,
    season_year: int | None,
    start_year: int,
) -> dict[str, Any]:
    api_seasons = await client.seasons()
    selected_seasons = selected_archive_seasons(api_seasons, season_year, start_year)
    records_seen = len(api_seasons)
    records_changed = 0
    synced = {
        "scope_type": scope_type,
        "selected_seasons": selected_seasons,
        "competitions": 0,
        "competition_seasons": 0,
        "fixtures": 0,
        "teams": 0,
    }

    if not selected_seasons:
        return {
            "records_seen": records_seen,
            "records_changed": records_changed,
            "synced": synced,
        }

    if provider_team_ids:
        result = await hydrate_team_scope(
            session,
            client,
            provider_team_ids=provider_team_ids,
            season_years=selected_seasons,
            provider_competition_id=provider_competition_id,
        )
    elif provider_competition_id is not None:
        result = await hydrate_competition_scope(
            session,
            client,
            season_years=selected_seasons,
            provider_competition_id=provider_competition_id,
        )
    elif scope_type == "supported_season":
        result = await hydrate_supported_competitions_scope(
            session,
            client,
            season_years=selected_seasons,
        )
    else:
        result = empty_hydration_result("Archive scope has no supported hydration strategy.")

    result["records_seen"] += records_seen
    result["synced"] = {
        **synced,
        **result["synced"],
    }
    return result


async def hydrate_team_scope(
    session: AsyncSession,
    client: ApiFootballClient,
    *,
    provider_team_ids: list[int],
    season_years: list[int],
    provider_competition_id: int | None,
) -> dict[str, Any]:
    records_seen = 0
    records_changed = 0
    synced = empty_synced_payload(season_years)

    for season_year in season_years:
        for team_id in provider_team_ids:
            if provider_competition_id is not None:
                league_rows = await client.leagues(league_id=provider_competition_id, season=season_year)
                records_seen += len(league_rows)
                if not league_rows:
                    synced["coverage_gaps"].append(coverage_gap(provider_competition_id, season_year))
                    continue
            fixture_args = {
                "team_id": team_id,
                "season": season_year,
                "status": "FT-AET-PEN",
            }
            if provider_competition_id is not None:
                fixture_args["league_id"] = provider_competition_id
            fixtures = await client.fixtures(**fixture_args)
            records_seen += len(fixtures)
            grouped = group_supported_fixtures(fixtures, provider_competition_id)
            result = await sync_grouped_fixtures(session, client, grouped, season_year)
            records_seen += result["records_seen"]
            records_changed += result["records_changed"]
            merge_synced_counts(synced, result["synced"])

    return {
        "records_seen": records_seen,
        "records_changed": records_changed,
        "synced": synced,
    }


async def hydrate_competition_scope(
    session: AsyncSession,
    client: ApiFootballClient,
    *,
    season_years: list[int],
    provider_competition_id: int,
) -> dict[str, Any]:
    records_seen = 0
    records_changed = 0
    synced = empty_synced_payload(season_years)

    for season_year in season_years:
        league_rows = await client.leagues(league_id=provider_competition_id, season=season_year)
        records_seen += len(league_rows)
        if not league_rows:
            synced["coverage_gaps"].append(coverage_gap(provider_competition_id, season_year))
            continue
        fixtures = await client.fixtures(
            league_id=provider_competition_id,
            season=season_year,
            status="FT-AET-PEN",
        )
        records_seen += len(fixtures)
        result = await sync_grouped_fixtures(session, client, {provider_competition_id: fixtures}, season_year)
        records_seen += result["records_seen"]
        records_changed += result["records_changed"]
        merge_synced_counts(synced, result["synced"])

    return {
        "records_seen": records_seen,
        "records_changed": records_changed,
        "synced": synced,
    }


async def hydrate_supported_competitions_scope(
    session: AsyncSession,
    client: ApiFootballClient,
    *,
    season_years: list[int],
) -> dict[str, Any]:
    records_seen = 0
    records_changed = 0
    synced = empty_synced_payload(season_years)

    for season_year in season_years:
        for competition_id in CORE_COMPETITION_IDS:
            league_rows = await client.leagues(league_id=competition_id, season=season_year)
            records_seen += len(league_rows)
            if not league_rows:
                synced["coverage_gaps"].append(coverage_gap(competition_id, season_year))
                continue
            fixtures = await client.fixtures(
                league_id=competition_id,
                season=season_year,
                status="FT-AET-PEN",
            )
            records_seen += len(fixtures)
            result = await sync_grouped_fixtures(session, client, {competition_id: fixtures}, season_year)
            records_seen += result["records_seen"]
            records_changed += result["records_changed"]
            merge_synced_counts(synced, result["synced"])

    return {
        "records_seen": records_seen,
        "records_changed": records_changed,
        "synced": synced,
    }


async def sync_grouped_fixtures(
    session: AsyncSession,
    client: ApiFootballClient,
    grouped: dict[int, list[dict[str, Any]]],
    season_year: int,
) -> dict[str, Any]:
    records_seen = 0
    records_changed = 0
    synced = empty_synced_payload([season_year])
    current_year = current_european_season_year()

    for league_id, rows in grouped.items():
        league_rows = await client.leagues(league_id=league_id, season=season_year)
        records_seen += len(league_rows)
        if not league_rows:
            continue

        competition = await repo.upsert_competition(session, league_rows[0])
        season = await repo.upsert_season(session, season_year, is_current=season_year == current_year)
        raw_season = league_season_payload(league_rows[0], season_year)
        await repo.upsert_competition_season(session, competition, season, raw_season)

        _, new_teams = await repo.upsert_fixtures(session, rows, competition, season, {})
        synced["competitions"] += 1
        synced["competition_seasons"] += 1
        synced["fixtures"] += len(rows)
        synced["teams"] += new_teams
        records_changed += len(rows) + new_teams + 3

    return {
        "records_seen": records_seen,
        "records_changed": records_changed,
        "synced": synced,
    }


async def sync_fixture(
    session: AsyncSession,
    raw_fixture: dict[str, Any],
    competition: Competition,
    season: Season,
    teams_by_provider_id: dict[int, Team],
) -> tuple[Fixture, int]:
    teams = raw_fixture.get("teams") or {}
    home_raw = teams.get("home") or {}
    away_raw = teams.get("away") or {}
    new_teams = 0

    home_team = teams_by_provider_id.get(int(home_raw["id"]))
    if home_team is None:
        home_team = await repo.upsert_team(session, {"team": home_raw})
        teams_by_provider_id[home_team.provider_team_id] = home_team
        new_teams += 1

    away_team = teams_by_provider_id.get(int(away_raw["id"]))
    if away_team is None:
        away_team = await repo.upsert_team(session, {"team": away_raw})
        teams_by_provider_id[away_team.provider_team_id] = away_team
        new_teams += 1

    fixture = await repo.upsert_fixture(session, raw_fixture, competition, season, home_team, away_team)
    return fixture, new_teams


def select_sync_seasons(api_seasons: list[int], requested: list[int] | None) -> list[int]:
    available = sorted(set(api_seasons))
    if not available:
        return []
    if requested:
        return [season for season in requested if season in available]

    current = current_european_season_year()
    selected = [season for season in (current, current - 1) if season in available]
    return selected or [available[-1]]


def historical_seasons(api_seasons: list[int], start_year: int, end_year: int | None = None) -> list[int]:
    """Return available seasons in an inclusive historical range."""

    upper = current_european_season_year() if end_year is None else end_year
    return [season for season in sorted(set(api_seasons)) if start_year <= season <= upper]


def selected_archive_seasons(api_seasons: list[int], season_year: int | None, start_year: int) -> list[int]:
    available = set(api_seasons)
    if season_year is not None:
        return [season_year] if season_year in available else []
    return historical_seasons(api_seasons, start_year)


def archive_seasons(start_year: int = ARCHIVE_START_SEASON, end_year: int | None = None) -> list[int]:
    upper = current_european_season_year() if end_year is None else end_year
    return list(range(upper, start_year - 1, -1))


def current_european_season_year(today: date | None = None) -> int:
    value = today or date.today()
    return value.year if value.month >= 7 else value.year - 1


def league_season_payload(raw_league: dict[str, Any], season_year: int) -> dict[str, Any] | None:
    for item in raw_league.get("seasons", []) or []:
        if item.get("year") == season_year:
            return item
    return None


def group_supported_fixtures(
    fixtures: list[dict[str, Any]],
    provider_competition_id: int | None,
) -> dict[int, list[dict[str, Any]]]:
    grouped: dict[int, list[dict[str, Any]]] = {}
    allowed_ids = {provider_competition_id} if provider_competition_id is not None else CORE_COMPETITION_IDS
    for raw in fixtures:
        league = raw.get("league") or {}
        league_id = league.get("id")
        if league_id not in allowed_ids:
            continue
        grouped.setdefault(int(league_id), []).append(raw)
    return grouped


def empty_hydration_result(reason: str) -> dict[str, Any]:
    return {
        "records_seen": 0,
        "records_changed": 0,
        "synced": {
            "selected_seasons": [],
            "competitions": 0,
            "competition_seasons": 0,
            "fixtures": 0,
            "teams": 0,
            "coverage_gaps": [],
        },
        "skipped": reason,
    }


def empty_synced_payload(season_years: list[int]) -> dict[str, Any]:
    return {
        "selected_seasons": season_years,
        "competitions": 0,
        "competition_seasons": 0,
        "fixtures": 0,
        "teams": 0,
        "coverage_gaps": [],
    }


def merge_synced_counts(target: dict[str, Any], source: dict[str, Any]) -> None:
    for key in ("competitions", "competition_seasons", "fixtures", "teams"):
        target[key] = int(target.get(key, 0)) + int(source.get(key, 0))
    target.setdefault("coverage_gaps", []).extend(source.get("coverage_gaps") or [])


def coverage_gap(provider_competition_id: int, season_year: int) -> dict[str, int]:
    return {
        "provider_competition_id": provider_competition_id,
        "season_year": season_year,
    }


def archive_sync_status_from_result(result: dict[str, Any]) -> str:
    synced = result.get("synced") or {}
    selected_seasons = synced.get("selected_seasons") or []
    coverage_gaps = synced.get("coverage_gaps") or []
    fixture_count = int(synced.get("fixtures") or 0)
    if selected_seasons and coverage_gaps and len(coverage_gaps) >= len(selected_seasons) and fixture_count == 0:
        return "provider_unavailable"
    return "complete"


def should_recheck_archive_status(
    status: str,
    records_changed: int,
    sync_metadata: dict[str, Any] | None,
    provider_competition_id: int | None,
    season_year: int | None,
) -> bool:
    if status != "complete" or provider_competition_id is None or season_year is None:
        return False
    metadata = sync_metadata or {}
    return records_changed == 0 and not metadata.get("coverage_gaps")
