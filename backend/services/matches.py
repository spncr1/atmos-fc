"""App-level match reads backed by the local database."""

from __future__ import annotations

from dataclasses import dataclass
import logging
import re
from typing import Literal

from sqlalchemy import or_, select
from sqlalchemy.ext.asyncio import AsyncSession
from sqlalchemy.exc import SQLAlchemyError
from sqlalchemy.orm import joinedload, selectinload

from backend.database.models import Competition, Fixture, FixtureEvent, FixtureEventSyncStatus, Team, TeamVisualProfile, YouTubeCommentCache
from backend.database.session import get_sessionmaker
from backend.models.schemas import MatchEvent, MatchSummary, SearchNotice
from backend.providers.api_football import ApiFootballClient
from backend.providers.errors import ProviderConfigError, ProviderRequestError, ProviderResponseError
from backend.repositories import football_data as repo
from backend.services.sync import ensure_archive_scope_synced
from backend.services.team_search import (
    ResolvedTeamSearch,
    competition_aliases_for,
    parse_search_intent,
    resolve_team_search,
    unique_ints,
)
from backend.services.team_visuals import ensure_team_visual_profiles, team_visual_response


logger = logging.getLogger(__name__)


class MatchDataError(RuntimeError):
    """Raised when locally stored match data cannot satisfy a request."""


ArchiveScopeType = Literal[
    "team_competition_season",
    "team_competition_all_seasons",
    "team_season",
    "team_all_seasons",
    "competition_season",
    "competition_all_seasons",
    "supported_season",
    "supported_all_seasons",
    "text_filtered",
]


@dataclass(frozen=True)
class ArchiveScope:
    """The archive slice a results-page search needs before DB results are trustworthy."""

    scope_type: ArchiveScopeType
    provider_team_ids: tuple[int, ...] = ()
    provider_competition_id: int | None = None
    season_year: int | None = None
    is_archive_addressable: bool = True


@dataclass(frozen=True)
class MatchSearchResult:
    matches: list[MatchSummary]
    notices: list[SearchNotice]


SUPPORTED_COMPETITIONS = [
    {
        "code": "PL",
        "provider_id": 39,
        "name": "Premier League",
        "short_name": "PL",
        "country_name": "England",
        "country_code": "GB-ENG",
        "group": "Domestic leagues",
        "logo_url": "https://media.api-sports.io/football/leagues/39.png",
    },
    {
        "code": "PD",
        "provider_id": 140,
        "name": "La Liga",
        "short_name": "La Liga",
        "country_name": "Spain",
        "country_code": "ES",
        "group": "Domestic leagues",
        "logo_url": "https://media.api-sports.io/football/leagues/140.png",
    },
    {
        "code": "BL1",
        "provider_id": 78,
        "name": "Bundesliga",
        "short_name": "Bundesliga",
        "country_name": "Germany",
        "country_code": "DE",
        "group": "Domestic leagues",
        "logo_url": "https://media.api-sports.io/football/leagues/78.png",
    },
    {
        "code": "SA",
        "provider_id": 135,
        "name": "Serie A",
        "short_name": "Serie A",
        "country_name": "Italy",
        "country_code": "IT",
        "group": "Domestic leagues",
        "logo_url": "https://media.api-sports.io/football/leagues/135.png",
    },
    {
        "code": "FL1",
        "provider_id": 61,
        "name": "Ligue 1",
        "short_name": "Ligue 1",
        "country_name": "France",
        "country_code": "FR",
        "group": "Domestic leagues",
        "logo_url": "https://media.api-sports.io/football/leagues/61.png",
    },
    {
        "code": "NED1",
        "provider_id": 88,
        "name": "Eredivisie",
        "short_name": "Eredivisie",
        "country_name": "Netherlands",
        "country_code": "NL",
        "group": "Domestic leagues",
        "logo_url": "https://media.api-sports.io/football/leagues/88.png",
    },
    {
        "code": "POR1",
        "provider_id": 94,
        "name": "Liga Portugal",
        "short_name": "Liga Portugal",
        "country_name": "Portugal",
        "country_code": "PT",
        "group": "Domestic leagues",
        "logo_url": "https://media.api-sports.io/football/leagues/94.png",
    },
    {
        "code": "BEL1",
        "provider_id": 144,
        "name": "Belgian Pro League",
        "short_name": "Belgian Pro League",
        "country_name": "Belgium",
        "country_code": "BE",
        "group": "Domestic leagues",
        "logo_url": "https://media.api-sports.io/football/leagues/144.png",
    },
    {
        "code": "TUR1",
        "provider_id": 203,
        "name": "Turkish Super Lig",
        "short_name": "Super Lig",
        "country_name": "Turkey",
        "country_code": "TR",
        "group": "Domestic leagues",
        "logo_url": "https://media.api-sports.io/football/leagues/203.png",
    },
    {
        "code": "CL",
        "provider_id": 2,
        "name": "UEFA Champions League",
        "short_name": "UCL",
        "country_name": "World",
        "country_code": None,
        "group": "European competitions",
        "logo_url": "https://media.api-sports.io/football/leagues/2.png",
    },
    {
        "code": "EL",
        "provider_id": 3,
        "name": "UEFA Europa League",
        "short_name": "UEL",
        "country_name": "World",
        "country_code": None,
        "group": "European competitions",
        "logo_url": "https://media.api-sports.io/football/leagues/3.png",
    },
    {
        "code": "UECL",
        "provider_id": 848,
        "name": "UEFA Conference League",
        "short_name": "UECL",
        "country_name": "World",
        "country_code": None,
        "group": "European competitions",
        "logo_url": "https://media.api-sports.io/football/leagues/848.png",
    },
]


COMPETITION_CODE_TO_PROVIDER_ID = {
    competition["code"]: competition["provider_id"]
    for competition in SUPPORTED_COMPETITIONS
}
COMPETITION_CODE_TO_PROVIDER_ID.update({
    "UCL": 2,
})

PROVIDER_ID_TO_COMPETITION_CODE = {
    competition["provider_id"]: competition["code"]
    for competition in SUPPORTED_COMPETITIONS
}
APP_COMPETITION_NAMES = {
    competition["provider_id"]: competition["name"]
    for competition in SUPPORTED_COMPETITIONS
}
COMPETITION_SEARCH_ALIASES = competition_aliases_for(SUPPORTED_COMPETITIONS)


async def recent_matches(limit: int = 18, competition: str | None = None) -> list[MatchSummary]:
    """Return recent finished matches from locally synced API-Football data."""

    provider_competition_id = provider_competition_id_for_code(competition)
    try:
        sessionmaker = get_sessionmaker()
        async with sessionmaker() as session:
            fixtures = await repo.recent_fixtures(
                session,
                limit=limit,
                provider_competition_id=provider_competition_id,
            )
            visual_profiles = await visual_profiles_for_fixtures(session, fixtures)
            await session.commit()
    except SQLAlchemyError as exc:
        raise MatchDataError("Recent matches could not be loaded from the local database.") from exc

    return fixtures_to_summaries(fixtures, visual_profiles=visual_profiles)


async def search_matches(
    query: str = "",
    competition: str | None = None,
    season: int | None = None,
) -> MatchSearchResult:
    """Search locally synced fixtures by team, competition, round, and season."""

    intent = parse_search_intent(query, competition_aliases=COMPETITION_SEARCH_ALIASES)
    effective_competition = competition or intent.competition_code
    provider_competition_id = provider_competition_id_for_code(effective_competition)
    provider_notices: list[SearchNotice] = []
    try:
        sessionmaker = get_sessionmaker()
        async with sessionmaker() as session:
            resolved_team = await resolve_team_search(intent, session, None)
            archive_scope = archive_scope_for(
                intent_kind=intent.kind,
                provider_team_ids=resolved_team.provider_team_ids,
                provider_competition_id=provider_competition_id,
                season_year=season,
            )
            sync_result = {}
            query_terms = query_terms_for_scope(intent.kind, resolved_team.query_terms, archive_scope)
            fixtures = await repo.search_fixtures(
                session,
                query_text=query,
                query_terms=query_terms,
                provider_team_ids=resolved_team.provider_team_ids,
                provider_competition_id=provider_competition_id,
                season_year=season,
            )

            provider_available = True
            should_try_archive_refresh = (
                not fixtures
                and archive_scope.is_archive_addressable
                and archive_scope.scope_type != "supported_all_seasons"
            )
            if should_try_archive_refresh and intent.should_resolve_team and not resolved_team.provider_team_ids:
                try:
                    resolved_team = await resolve_team_search(intent, session, ApiFootballClient())
                    archive_scope = archive_scope_for(
                        intent_kind=intent.kind,
                        provider_team_ids=resolved_team.provider_team_ids,
                        provider_competition_id=provider_competition_id,
                        season_year=season,
                    )
                except (ProviderConfigError, ProviderRequestError, ProviderResponseError) as exc:
                    logger.warning("API-Football team resolution failed; continuing with local search: %s", exc)
                    resolved_team = ResolvedTeamSearch(provider_team_ids=[], query_terms=intent.query_terms)
                    provider_notices.append(provider_refresh_unavailable_notice())
                    provider_available = False

            if should_try_archive_refresh and provider_available:
                try:
                    sync_result = await ensure_archive_scope_synced(
                        scope_type=archive_scope.scope_type,
                        provider_team_ids=archive_scope.provider_team_ids,
                        provider_competition_id=archive_scope.provider_competition_id,
                        season_year=archive_scope.season_year,
                        is_archive_addressable=archive_scope.is_archive_addressable,
                    )
                except (ProviderConfigError, ProviderRequestError, ProviderResponseError) as exc:
                    logger.warning("API-Football archive refresh failed; continuing with local search: %s", exc)
                    provider_notices.append(provider_refresh_unavailable_notice())

            if sync_result:
                query_terms = query_terms_for_scope(intent.kind, resolved_team.query_terms, archive_scope)
                fixtures = await repo.search_fixtures(
                    session,
                    query_text=query,
                    query_terms=query_terms,
                    provider_team_ids=resolved_team.provider_team_ids,
                    provider_competition_id=provider_competition_id,
                    season_year=season,
                )
            visual_profiles = await visual_profiles_for_fixtures(session, fixtures)
            await session.commit()
    except SQLAlchemyError as exc:
        raise MatchDataError("Search results could not be loaded from the local database.") from exc

    return MatchSearchResult(
        matches=fixtures_to_summaries(fixtures, visual_profiles=visual_profiles),
        notices=[*provider_notices, *search_notices_from_sync_result(sync_result)],
    )


def archive_scope_for(
    *,
    intent_kind: str,
    provider_team_ids: list[int],
    provider_competition_id: int | None,
    season_year: int | None,
) -> ArchiveScope:
    """Return the archive slice that must be complete for a results search."""

    team_ids = tuple(unique_ints(provider_team_ids))
    if team_ids:
        if provider_competition_id is not None and season_year is not None:
            return ArchiveScope(
                scope_type="team_competition_season",
                provider_team_ids=team_ids,
                provider_competition_id=provider_competition_id,
                season_year=season_year,
            )
        if provider_competition_id is not None:
            return ArchiveScope(
                scope_type="team_competition_all_seasons",
                provider_team_ids=team_ids,
                provider_competition_id=provider_competition_id,
            )
        if season_year is not None:
            return ArchiveScope(
                scope_type="team_season",
                provider_team_ids=team_ids,
                season_year=season_year,
            )
        return ArchiveScope(scope_type="team_all_seasons", provider_team_ids=team_ids)

    if provider_competition_id is not None:
        if season_year is not None:
            return ArchiveScope(
                scope_type="competition_season",
                provider_competition_id=provider_competition_id,
                season_year=season_year,
            )
        return ArchiveScope(
            scope_type="competition_all_seasons",
            provider_competition_id=provider_competition_id,
        )

    if intent_kind == "text":
        return ArchiveScope(
            scope_type="text_filtered",
            season_year=season_year,
            is_archive_addressable=False,
        )

    if season_year is not None:
        return ArchiveScope(scope_type="supported_season", season_year=season_year)

    return ArchiveScope(scope_type="supported_all_seasons")


def query_terms_for_scope(
    intent_kind: str,
    query_terms: list[str],
    archive_scope: ArchiveScope,
) -> list[str]:
    if intent_kind == "competition" and archive_scope.scope_type in {"competition_season", "competition_all_seasons"}:
        return []
    return query_terms


def search_notices_from_sync_result(sync_result: dict) -> list[SearchNotice]:
    synced = sync_result.get("synced") or {}
    coverage_gaps = synced.get("coverage_gaps") or []
    if not coverage_gaps:
        return []

    first_gap = coverage_gaps[0]
    competition_name = APP_COMPETITION_NAMES.get(
        first_gap.get("provider_competition_id"),
        "that competition",
    )
    season = season_label(first_gap["season_year"]) if first_gap.get("season_year") is not None else "that season"
    if sync_result.get("status") == "provider_unavailable":
        return [
            SearchNotice(
                type="provider_coverage_gap",
                title="Provider coverage unavailable",
                message=(
                    f"This application does not provide {competition_name} data for {season}. "
                ),
            )
        ]

    return [
        SearchNotice(
            type="partial_provider_coverage",
            title="Some historical coverage is unavailable",
            message=(
                f"API-Football is missing at least one requested {competition_name} season. "
                "Available matches are still shown below."
            ),
        )
    ]


def provider_refresh_unavailable_notice() -> SearchNotice:
    return SearchNotice(
        type="provider_refresh_unavailable",
        title="Live fixture refresh unavailable",
        message="Showing matches already stored by Atmos FC. Newly completed fixtures may take longer to appear.",
    )


async def analysis_match(match_id: str) -> tuple[MatchSummary, list[MatchEvent]] | None:
    """Return a locally synced fixture and events for analysis, if present."""

    try:
        provider_fixture_id = int(match_id)
    except (TypeError, ValueError):
        return None

    try:
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
                    selectinload(Fixture.events).joinedload(FixtureEvent.team),
                )
                .where(
                    Fixture.provider == repo.PROVIDER,
                    Fixture.provider_fixture_id == provider_fixture_id,
                )
            )
            if fixture is None:
                return None

            aggregate_candidates = list(await session.scalars(
                select(Fixture)
                .options(
                    joinedload(Fixture.competition),
                    joinedload(Fixture.season),
                    joinedload(Fixture.home_team),
                    joinedload(Fixture.away_team),
                )
                .where(
                    Fixture.provider == repo.PROVIDER,
                    Fixture.competition_id == fixture.competition_id,
                    Fixture.season_id == fixture.season_id,
                    Fixture.round_name == fixture.round_name,
                    or_(
                        Fixture.home_team_id.in_([fixture.home_team_id, fixture.away_team_id]),
                        Fixture.away_team_id.in_([fixture.home_team_id, fixture.away_team_id]),
                    ),
                )
            ))
            events = await ensure_fixture_events_for_analysis(session, fixture, list(fixture.events))
            await session.commit()

            visual_profiles = await visual_profiles_for_fixtures(session, [fixture])
            await session.commit()
            return (
                fixture_to_summary(
                    fixture,
                    aggregate_score=aggregate_score_from_fixture(fixture, aggregate_candidates),
                    visual_profiles=visual_profiles,
                ),
                fixture_events_to_summary(events),
            )
    except SQLAlchemyError as exc:
        raise MatchDataError("Match analysis data could not be loaded from the local database.") from exc
    except (ProviderConfigError, ProviderRequestError, ProviderResponseError) as exc:
        raise MatchDataError("Match event data could not be loaded from API-Football.") from exc


async def hydrate_fixture_events(session: AsyncSession, fixture: Fixture) -> list[FixtureEvent]:
    """Fetch and store event timeline data for one locally cached fixture."""

    client = ApiFootballClient()
    raw_events = await client.fixture_events(fixture.provider_fixture_id)
    event_count = await repo.replace_fixture_events(session, fixture, raw_events)
    sync_status = await repo.upsert_fixture_event_sync_status(
        session,
        fixture,
        status="complete" if event_count else "unavailable",
        event_count=event_count,
        raw_payload={
            "source": "api_football",
            "provider_fixture_id": fixture.provider_fixture_id,
        },
    )
    fixture.event_sync_status = sync_status
    events = await session.scalars(
        select(FixtureEvent)
        .options(joinedload(FixtureEvent.team))
        .where(FixtureEvent.fixture_id == fixture.id)
    )
    return list(events)


async def ensure_fixture_events_for_analysis(
    session: AsyncSession,
    fixture: Fixture,
    events: list[FixtureEvent],
) -> list[FixtureEvent]:
    """Return stored events, fetching them on demand when analysis needs them."""

    if events:
        if event_feed_status(fixture.event_sync_status) != "complete":
            sync_status = await repo.upsert_fixture_event_sync_status(
                session,
                fixture,
                status="complete",
                event_count=len(events),
                raw_payload={
                    "source": "local_fixture_events",
                    "provider_fixture_id": fixture.provider_fixture_id,
                },
            )
            fixture.event_sync_status = sync_status
        return events

    if event_feed_status(fixture.event_sync_status) == "unavailable":
        return []

    try:
        return await hydrate_fixture_events(session, fixture)
    except (ProviderConfigError, ProviderRequestError, ProviderResponseError) as exc:
        sync_status = await repo.upsert_fixture_event_sync_status(
            session,
            fixture,
            status="failed",
            event_count=0,
            error_message=str(exc),
            raw_payload={
                "source": "analysis_on_demand",
                "provider_fixture_id": fixture.provider_fixture_id,
            },
        )
        fixture.event_sync_status = sync_status
        logger.warning(
            "On-demand event hydration failed for fixture %s: %s",
            fixture.provider_fixture_id,
            exc,
        )
        return []


def provider_competition_id_for_code(competition: str | None) -> int | None:
    if not competition:
        return None
    code = competition.strip().upper()
    provider_id = COMPETITION_CODE_TO_PROVIDER_ID.get(code)
    if provider_id is None:
        raise MatchDataError("Unsupported competition.")
    return provider_id


async def visual_profiles_for_fixtures(
    session: AsyncSession,
    fixtures: list[Fixture],
) -> dict[int, TeamVisualProfile]:
    teams = [
        team
        for fixture in fixtures
        for team in [
            fixture.home_team,
            fixture.away_team,
        ]
        if team is not None and team.provider_team_id is not None
    ]
    unique_teams = {team.provider_team_id: team for team in teams}
    return await ensure_team_visual_profiles(session, list(unique_teams.values()))


def fixtures_to_summaries(
    fixtures: list[Fixture],
    *,
    visual_profiles: dict[int, TeamVisualProfile] | None = None,
) -> list[MatchSummary]:
    aggregate_scores = aggregate_scores_for_fixtures(fixtures)
    return [
        fixture_to_summary(
            fixture,
            aggregate_score=aggregate_scores.get(fixture.provider_fixture_id),
            visual_profiles=visual_profiles,
        )
        for fixture in fixtures
    ]


def fixture_to_summary(
    fixture: Fixture,
    aggregate_score: str | None = None,
    *,
    visual_profiles: dict[int, TeamVisualProfile] | None = None,
) -> MatchSummary:
    home_team = fixture.home_team
    away_team = fixture.away_team
    competition = fixture.competition
    competition_code = app_competition_code(competition)
    youtube_cache = fixture.youtube_comment_cache
    event_sync_status = fixture.event_sync_status
    return MatchSummary(
        id=str(fixture.provider_fixture_id),
        home=home_team.name,
        away=away_team.name,
        home_team_id=home_team.provider_team_id,
        away_team_id=away_team.provider_team_id,
        home_team_key=str(home_team.provider_team_id),
        away_team_key=str(away_team.provider_team_id),
        home_short_name=home_team.name,
        away_short_name=away_team.name,
        home_tla=team_code(home_team),
        away_tla=team_code(away_team),
        home_crest=home_team.logo_url,
        away_crest=away_team.logo_url,
        home_visual=team_visual_response((visual_profiles or {}).get(home_team.provider_team_id)),
        away_visual=team_visual_response((visual_profiles or {}).get(away_team.provider_team_id)),
        score=score_label(fixture.home_goals, fixture.away_goals),
        score_note=score_note(fixture),
        penalty_score=penalty_score_label(fixture),
        aggregate_score=aggregate_score,
        half_time_score=score_label(fixture.halftime_home_goals, fixture.halftime_away_goals),
        competition=APP_COMPETITION_NAMES.get(competition.provider_competition_id, competition.name),
        competition_code=competition_code,
        competition_id=competition.provider_competition_id,
        date=fixture.kickoff_at.isoformat(),
        venue=venue_label(fixture),
        round=display_round_name(fixture.round_name),
        season=fixture.season.label,
        season_year=fixture.season.year,
        status=app_status(fixture.status_short),
        youtube_comment_count=youtube_raw_comment_count(youtube_cache),
        youtube_analysed_comment_count=youtube_analysed_comment_count(youtube_cache),
        youtube_comment_status=youtube_comment_status(youtube_cache),
        youtube_checked_at=youtube_checked_at(youtube_cache),
        event_feed_status=event_feed_status(event_sync_status),
        event_feed_checked_at=event_feed_checked_at(event_sync_status),
        analysis_available=analysis_is_available(fixture),
    )


def analysis_is_available(fixture: Fixture) -> bool:
    cache = fixture.__dict__.get("analysis_cache")
    return bool(
        cache
        and cache.status == "complete"
        and isinstance(cache.payload, dict)
        and isinstance(cache.payload.get("meta"), dict)
        and cache.payload["meta"].get("analysis_version") == "match_context_v2"
    )

def app_competition_code(competition: Competition) -> str:
    return PROVIDER_ID_TO_COMPETITION_CODE.get(
        competition.provider_competition_id,
        competition.code or str(competition.provider_competition_id),
    )


def team_code(team: Team) -> str | None:
    value = team.code
    return value.strip().upper() if isinstance(value, str) and value.strip() else None


EVENT_TEAM_SHORT_NAMES = {
    "Arsenal": "Arsenal",
    "Atalanta": "Atalanta",
    "Bayern München": "Bayern",
    "Bayern Munich": "Bayern",
    "FC Bayern München": "Bayern",
    "FC Internazionale Milano": "INT",
    "Inter": "INT",
    "Internazionale": "INT",
    "Liverpool": "Liverpool",
    "Manchester City": "Man City",
    "Manchester United": "Man United",
    "Olympique de Marseille": "Marseille",
    "Paris Saint Germain": "PSG",
    "Paris Saint-Germain": "PSG",
    "Paris Saint-Germain FC": "PSG",
    "Real Madrid": "Real Madrid",
}


def event_team_label(team: Team | None) -> str | None:
    if team is None:
        return None
    return EVENT_TEAM_SHORT_NAMES.get(team.name) or team_code(team) or short_team_label(team.name)


def event_team_suffix(team: Team | None) -> str:
    label = event_team_label(team)
    return f" ({label})" if label else ""


def short_team_label(name: str | None) -> str | None:
    clean = " ".join(str(name or "").split())
    if not clean:
        return None
    return (
        clean.replace("Paris Saint-Germain", "PSG")
        .replace("Paris Saint Germain", "PSG")
        .replace("FC Bayern München", "Bayern")
        .replace("Bayern München", "Bayern")
        .replace("FC Internazionale Milano", "Inter")
        .replace("Internazionale", "Inter")
        .removeprefix("FC ")
        .removesuffix(" FC")
        .removesuffix(" AFC")
        .removesuffix(" CF")
    )


def score_label(home: int | None, away: int | None) -> str | None:
    if home is None or away is None:
        return None
    return f"{home}-{away}"


def score_note(fixture: Fixture) -> str | None:
    if fixture.status_short == "AET":
        return "AET"
    if fixture.status_short == "PEN":
        return "AET"
    return None


def penalty_score_label(fixture: Fixture) -> str | None:
    if fixture.penalty_home_goals is None or fixture.penalty_away_goals is None:
        return None
    home = fixture.home_team
    away = fixture.away_team
    winner = home if fixture.penalty_home_goals > fixture.penalty_away_goals else away
    return f"{team_code(winner) or winner.name} won {fixture.penalty_home_goals}-{fixture.penalty_away_goals} on penalties"


def venue_label(fixture: Fixture) -> str | None:
    stadium = clean_stadium_name(fixture.venue_name)
    if fixture.venue_name and fixture.venue_city:
        return f"{stadium}, {fixture.venue_city}"
    return stadium or fixture.venue_city


def clean_stadium_name(name: str | None) -> str | None:
    clean = " ".join(str(name or "").split())
    if not clean:
        return None
    replacements = {
        "The American Express Community Stadium": "Amex Stadium",
        "Stadio Giuseppe Meazza": "San Siro",
    }
    return replacements.get(clean, clean)


def app_status(status_short: str | None) -> str | None:
    if status_short in {"FT", "AET", "PEN"}:
        return "FINISHED"
    return status_short


def youtube_raw_comment_count(cache: YouTubeCommentCache | None) -> int | None:
    if cache is None:
        return None
    return cache.raw_comment_count


def youtube_analysed_comment_count(cache: YouTubeCommentCache | None) -> int | None:
    if cache is None:
        return None
    return cache.analysed_comment_count


def youtube_comment_status(cache: YouTubeCommentCache | None) -> str:
    return cache.status if cache is not None else "unchecked"


def youtube_checked_at(cache: YouTubeCommentCache | None) -> str | None:
    if cache is None or cache.checked_at is None:
        return None
    return cache.checked_at.isoformat()


def event_feed_status(status: FixtureEventSyncStatus | None) -> str:
    return status.status if status is not None else "unchecked"


def event_feed_checked_at(status: FixtureEventSyncStatus | None) -> str | None:
    if status is None or status.checked_at is None:
        return None
    return status.checked_at.isoformat()


def display_round_name(round_name: str | None) -> str | None:
    clean = " ".join(str(round_name or "").split())
    if not clean:
        return None

    lower = clean.lower()
    if lower.startswith("regular season -"):
        matchday = clean.split("-", maxsplit=1)[1].strip()
        return f"Matchday {matchday}" if matchday else "Regular Season"
    replacements = {
        "16th finals": "Round of 32",
        "8th finals": "Round of 16",
        "quarter-finals": "Quarter-finals",
        "semi-finals": "Semi-finals",
    }
    if lower in replacements:
        return replacements[lower]
    group_match = re.match(r"^group\s+([a-z])\s+-\s+(\d+)$", clean, flags=re.IGNORECASE)
    if group_match:
        group, matchday = group_match.groups()
        return f"Group {group.upper()} - MD{matchday}"
    group_stage_match = re.match(r"^group\s+stage\s+-\s+(\d+)$", clean, flags=re.IGNORECASE)
    if group_stage_match:
        return f"Group Stage - MD{group_stage_match.group(1)}"
    if lower.startswith("group stage"):
        return clean.replace("Group stage", "Group Stage").replace("group stage", "Group Stage")
    if lower.startswith("league stage -"):
        matchday = clean.split("-", maxsplit=1)[1].strip()
        return f"League Phase - MD{matchday}" if matchday else "League Phase"
    return clean


def season_label(year: int) -> str:
    return f"{year}/{str(year + 1)[-2:]}"


def aggregate_scores_for_fixtures(fixtures: list[Fixture]) -> dict[int, str]:
    fixtures_by_id = {fixture.provider_fixture_id: fixture for fixture in fixtures}
    scores: dict[int, str] = {}
    for fixture in fixtures:
        aggregate = aggregate_score_from_fixture(fixture, fixtures)
        if aggregate:
            scores[fixture.provider_fixture_id] = aggregate
    return {
        fixture_id: aggregate
        for fixture_id, aggregate in scores.items()
        if fixture_id in fixtures_by_id
    }


def aggregate_score_from_fixture(fixture: Fixture, candidates: list[Fixture] | None = None) -> str | None:
    other = matching_two_leg_fixture(fixture, candidates or sibling_fixtures(fixture))
    if other is None or not is_second_leg(fixture, other):
        return None

    if same_home_team(fixture, other):
        home_total = safe_goal(fixture.home_goals) + safe_goal(other.home_goals)
        away_total = safe_goal(fixture.away_goals) + safe_goal(other.away_goals)
    else:
        home_total = safe_goal(fixture.home_goals) + safe_goal(other.away_goals)
        away_total = safe_goal(fixture.away_goals) + safe_goal(other.home_goals)

    if home_total > away_total:
        winner = fixture.home_team
        winner_total = home_total
        loser_total = away_total
    else:
        winner = fixture.away_team
        winner_total = away_total
        loser_total = home_total
    return f"{team_code(winner) or winner.name} won {winner_total}-{loser_total} on aggregate"


def sibling_fixtures(fixture: Fixture) -> list[Fixture]:
    return list(getattr(fixture.season, "fixtures", []) or [])


def matching_two_leg_fixture(fixture: Fixture, candidates: list[Fixture]) -> Fixture | None:
    for candidate in candidates:
        if candidate.id == fixture.id:
            continue
        if candidate.competition_id != fixture.competition_id or candidate.season_id != fixture.season_id:
            continue
        if candidate.round_name != fixture.round_name:
            continue
        reversed_order = candidate.home_team_id == fixture.away_team_id and candidate.away_team_id == fixture.home_team_id
        if reversed_order:
            return candidate
    return None


def is_second_leg(fixture: Fixture, other: Fixture) -> bool:
    return fixture.kickoff_at > other.kickoff_at


def same_home_team(fixture: Fixture, other: Fixture) -> bool:
    return fixture.home_team_id == other.home_team_id


def safe_goal(value: int | None) -> int:
    return value or 0


def fixture_events_to_summary(events: list[FixtureEvent]) -> list[MatchEvent]:
    summaries: list[MatchEvent] = []
    sorted_events = sorted(events, key=lambda item: ((item.minute or 0), (item.extra_minute or 0), item.id))
    for event in sorted_events:
        if not is_key_event(event) or is_duplicate_second_yellow_event(event, sorted_events):
            continue
        summaries.append(
            MatchEvent(
                minute=event.minute or 0,
                display_minute=event_minute_label(event),
                type=event_type(event, sorted_events),
                description=event_description(event, sorted_events),
            )
        )
    return summaries


def event_minute_label(event: FixtureEvent) -> str:
    minute = event.minute or 0
    extra = event.extra_minute or 0
    if extra > 0:
        return f"{minute}+{extra}'"
    return f"{minute}'"


def is_key_event(event: FixtureEvent) -> bool:
    event_type_clean = (event.type or "").strip().lower()
    detail = (event.detail or "").strip().lower()
    if event_type_clean in {"goal", "card", "subst", "var"}:
        return True
    if "penalty" in detail or "own goal" in detail:
        return True
    return False


def event_type(event: FixtureEvent, fixture_events: list[FixtureEvent] | None = None) -> str:
    event_type_clean = (event.type or "").strip().lower()
    detail = (event.detail or "").strip().lower()
    comments = (event.comments or "").strip().lower()
    if event_type_clean == "goal":
        if "own goal" in detail:
            return "own-goal"
        if "missed penalty" in detail or "penalty missed" in detail:
            return "missed-penalty"
        if "penalty" in detail:
            return "penalty-goal"
        return "goal"
    if event_type_clean == "subst":
        return "substitution"
    if event_type_clean == "var":
        return "var"
    if is_second_yellow_card_event(event, fixture_events or []):
        return "second-yellow-card"
    if event_type_clean == "card" and "red" in detail:
        return "red-card"
    if "penalty" in detail or "penalty" in comments:
        return "penalty"
    return "yellow-card"


def event_description(event: FixtureEvent, fixture_events: list[FixtureEvent] | None = None) -> str:
    player = event.player_name or "Unknown player"
    assist = event.assist_player_name
    event_kind = event_type(event, fixture_events)
    team_suffix = event_team_suffix(event.team)

    if event_kind == "substitution":
        if assist:
            return f"{player} OUT - {assist} IN{team_suffix}"
        return f"{player} OUT{team_suffix}"

    if event_kind in {"goal", "penalty-goal", "own-goal"}:
        assist_text = f" - Assist: {assist}" if assist else ""
        return f"{player}{assist_text}{team_suffix}"

    if event_kind == "missed-penalty":
        return f"{player}{team_suffix}"

    if event_kind == "var":
        detail = clean_event_detail(event.detail or "VAR")
        return f"{detail} - {player}{team_suffix}"

    if event.comments:
        return f"{player} - {event.comments}{team_suffix}"

    return f"{player}{team_suffix}"


def event_detail_label(event: FixtureEvent) -> str:
    event_kind = event_type(event)
    labels = {
        "goal": "Goal",
        "penalty-goal": "Penalty goal",
        "missed-penalty": "Missed penalty",
        "own-goal": "Own goal",
        "yellow-card": "Yellow card",
        "second-yellow-card": "Second yellow",
        "red-card": "Red card",
        "substitution": "Substitution",
        "var": normalise_var_detail(event.detail),
        "penalty": "Penalty",
    }
    return labels.get(event_kind, clean_event_detail(event.detail or event.type))


def is_second_yellow_card_event(event: FixtureEvent, fixture_events: list[FixtureEvent]) -> bool:
    detail = (event.detail or "").strip().lower()
    comments = (event.comments or "").strip().lower()
    if "second yellow" in detail or "second yellow" in comments:
        return True
    if "red" not in detail:
        return False
    return any(is_matching_same_minute_yellow(event, other) for other in fixture_events)


def is_duplicate_second_yellow_event(event: FixtureEvent, fixture_events: list[FixtureEvent]) -> bool:
    if event_type(event, []) != "yellow-card":
        return False
    return any(is_matching_same_minute_yellow(other, event) for other in fixture_events if other is not event)


def is_matching_same_minute_yellow(red_event: FixtureEvent, yellow_event: FixtureEvent) -> bool:
    red_detail = (red_event.detail or "").strip().lower()
    yellow_detail = (yellow_event.detail or "").strip().lower()
    if "red" not in red_detail or "yellow" not in yellow_detail:
        return False
    return (
        red_event.player_name
        and yellow_event.player_name
        and red_event.player_name == yellow_event.player_name
        and red_event.team_id == yellow_event.team_id
        and (red_event.minute or 0) == (yellow_event.minute or 0)
        and (red_event.extra_minute or 0) == (yellow_event.extra_minute or 0)
    )


def normalise_var_detail(detail: str | None) -> str:
    clean = clean_event_detail(detail or "VAR")
    return f"VAR - {clean}" if clean.upper() != "VAR" else "VAR"


def clean_event_detail(detail: str | None) -> str:
    clean = " ".join(str(detail or "").split())
    if not clean:
        return "Event"
    if clean.lower() == "normal goal":
        return "Goal"
    return clean[:1].upper() + clean[1:]
