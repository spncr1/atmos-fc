"""Team profile reads backed by API-Football facts and sourced enrichment."""

from __future__ import annotations

from datetime import datetime, timedelta, timezone

from sqlalchemy.exc import SQLAlchemyError

from backend.database.models import Team, TeamProfileEnrichment, TeamVisualProfile
from backend.database.session import get_sessionmaker
from backend.models.schemas import (
    TeamKitSlot,
    TeamKits,
    TeamMatchAnalysisSummary,
    TeamPlayer,
    TeamProfileResponse,
    TeamSeasonSnapshot,
)
from backend.providers.api_football import ApiFootballClient
from backend.providers.errors import ProviderConfigError, ProviderError, ProviderRequestError, ProviderResponseError
from backend.repositories import football_data as repo
from backend.services.matches import SUPPORTED_COMPETITIONS, clean_stadium_name, fixture_to_summary
from backend.services.team_profile_content import profile_content_for
from backend.services.team_honours import TeamHonours, ensure_team_honours, manual_honours
from backend.services.team_profile_resolver import resolve_team_profile_enrichment
from backend.services.team_visuals import ensure_team_visual_profiles, team_visual_response


class TeamProfileError(RuntimeError):
    """Raised when a team profile cannot be loaded."""


class TeamProfileNotFoundError(TeamProfileError):
    """Raised when a requested team does not exist locally or in API-Football."""


class TeamProfileUnavailableError(TeamProfileError):
    """Raised when the team profile source cannot be reached."""


SUPPORTED_COMPETITION_IDS = {
    int(definition["provider_id"])
    for definition in SUPPORTED_COMPETITIONS
}
PLAYER_CACHE_TTL = timedelta(days=7)


async def team_profile(provider_team_id: int, season_year: int | None = None) -> TeamProfileResponse:
    """Return team profile facts, using sourced enrichment when available."""

    if provider_team_id <= 0:
        raise TeamProfileNotFoundError("Team ID must be a positive API-Football team ID.")

    try:
        sessionmaker = get_sessionmaker()
        async with sessionmaker() as session:
            team = await repo.team_by_provider_id(session, provider_team_id)
            data_source = "local"
            try:
                rows = await ApiFootballClient().teams(team_id=provider_team_id)
            except (ProviderConfigError, ProviderRequestError, ProviderResponseError) as exc:
                if team is None:
                    raise TeamProfileUnavailableError("Team profile could not be loaded from API-Football.") from exc
                rows = []
            if rows:
                team = await repo.upsert_team(session, rows[0])
                await session.commit()
                data_source = "api_football"
            if team is None:
                raise TeamProfileNotFoundError("Team profile was not found.")
            enrichment = await profile_enrichment(session, team)
            honours = await ensure_team_honours(
                team,
                enrichment,
                manual_content=profile_content_for(team.name),
            )
            players, players_updated_at = await current_players(session, team, enrichment)
            selected_season_year = season_year or await repo.latest_team_season_year(
                session,
                team.id,
                provider_competition_ids=SUPPORTED_COMPETITION_IDS,
            ) or datetime.now(timezone.utc).year
            fixtures = await repo.team_finished_fixtures(
                session,
                team.id,
                selected_season_year,
                provider_competition_ids=SUPPORTED_COMPETITION_IDS,
            )
            visual_profiles = await ensure_team_visual_profiles(session, [team])
            visual_profile = visual_profiles.get(team.provider_team_id)
            await session.commit()
            return profile_response(
                team,
                enrichment=enrichment,
                data_source=data_source,
                visual_profile=visual_profile,
                selected_season_year=selected_season_year,
                fixtures=fixtures,
                current_players=players,
                current_players_updated_at=players_updated_at,
                honours=honours,
            )
    except SQLAlchemyError as exc:
        raise TeamProfileUnavailableError("Team profile could not be loaded from the local database.") from exc


async def profile_enrichment(session, team: Team) -> TeamProfileEnrichment | None:
    """Return cached/resolved profile enrichment without blocking basic team facts."""

    try:
        return await resolve_team_profile_enrichment(session, team)
    except (ProviderError, SQLAlchemyError):
        return await repo.team_profile_enrichment_for_team(session, team)


def profile_response(
    team: Team,
    *,
    enrichment: TeamProfileEnrichment | None = None,
    data_source: str,
    visual_profile: TeamVisualProfile | None = None,
    selected_season_year: int,
    fixtures: list | None = None,
    current_players: list[TeamPlayer] | None = None,
    current_players_updated_at: datetime | None = None,
    honours: TeamHonours | None = None,
) -> TeamProfileResponse:
    stadium = clean_stadium_name(team.venue_name)
    facts = enrichment.facts_json if enrichment and isinstance(enrichment.facts_json, dict) else {}
    venue_label = None
    if stadium and team.venue_city:
        venue_label = f"{stadium}, {team.venue_city}"
    elif stadium or team.venue_city:
        venue_label = stadium or team.venue_city
    manual_content = profile_content_for(team.name)
    resolved_honours = honours or manual_honours(manual_content)
    official_websites = facts.get("official_websites") if isinstance(facts.get("official_websites"), list) else []
    official_website = (manual_content.official_website if manual_content else None) or (
        official_websites[0] if official_websites else None
    )
    wikidata_description = facts.get("wikidata_description") if isinstance(facts.get("wikidata_description"), str) else None
    profile_source = data_source
    if enrichment and enrichment.wikidata_qid and not enrichment.needs_review:
        profile_source = f"{data_source}+wikidata"
    season_label = repo.season_label(selected_season_year)
    fixture_rows = fixtures or []
    snapshot = build_season_snapshot(team, fixture_rows, selected_season_year)

    return TeamProfileResponse(
        provider_team_id=team.provider_team_id,
        name=team.name,
        code=team.code,
        country_name=team.country_name,
        founded=team.founded,
        is_national=team.is_national,
        logo_url=team.logo_url,
        venue_name=stadium,
        venue_city=team.venue_city,
        venue_label=venue_label,
        visual=team_visual_response(visual_profile),
        summary=(manual_content.summary if manual_content else None)
        or (enrichment.summary if enrichment else None)
        or wikidata_description,
        profile_sections=[],
        has_manual_profile=manual_content is not None,
        wikidata_qid=enrichment.wikidata_qid if enrichment else None,
        wikipedia_title=enrichment.wikipedia_title if enrichment else None,
        wikipedia_url=enrichment.wikipedia_url if enrichment else None,
        official_website=official_website,
        profile_confidence=enrichment.confidence if enrichment else None,
        profile_needs_review=bool(enrichment.needs_review) if enrichment else False,
        source_attribution_url=enrichment.attribution_url if enrichment else None,
        license_label=enrichment.license_label if enrichment else None,
        facts=facts,
        data_source=profile_source,
        selected_season_year=selected_season_year,
        selected_season_label=season_label,
        season_snapshot=snapshot,
        latest_match_analysis=latest_match_analysis(fixture_rows),
        current_players=current_players or [],
        current_players_updated_at=current_players_updated_at,
        major_trophy_count=resolved_honours.total if resolved_honours else None,
        major_achievements=[f"{honour.count} {honour.title}" for honour in resolved_honours.honours] if resolved_honours else [],
        major_honours_status=resolved_honours.status if resolved_honours else "unavailable",
        achievements_source_url=resolved_honours.source_url if resolved_honours else None,
        kits=TeamKits(
            season_label=season_label,
            home=TeamKitSlot(label="Home kit"),
            away=TeamKitSlot(label="Away kit"),
        ),
    )


async def current_players(
    session,
    team: Team,
    enrichment: TeamProfileEnrichment | None,
) -> tuple[list[TeamPlayer], datetime | None]:
    """Return a fresh cached squad, falling back cleanly when the provider fails."""

    facts = dict(enrichment.facts_json or {}) if enrichment and isinstance(enrichment.facts_json, dict) else {}
    cached_players = normalise_cached_players(facts.get("current_players"))
    cached_at = parse_timestamp(facts.get("current_players_updated_at"))
    if cached_at and datetime.now(timezone.utc) - cached_at <= PLAYER_CACHE_TTL:
        return cached_players, cached_at

    try:
        rows = await ApiFootballClient().squad(team.provider_team_id)
        players = normalise_squad_rows(rows)
    except (ProviderConfigError, ProviderRequestError, ProviderResponseError):
        return cached_players, cached_at
    if not players and cached_players:
        return cached_players, cached_at

    refreshed_at = datetime.now(timezone.utc)
    if enrichment is not None:
        facts["current_players"] = [player.model_dump(mode="json") for player in players]
        facts["current_players_updated_at"] = refreshed_at.isoformat()
        enrichment.facts_json = facts
    return players, refreshed_at


def normalise_squad_rows(rows: list[dict]) -> list[TeamPlayer]:
    """Convert API-Football's squad response into stable frontend records."""

    raw_players = []
    for row in rows:
        if isinstance(row, dict) and isinstance(row.get("players"), list):
            raw_players.extend(row["players"])
    players: list[TeamPlayer] = []
    seen_ids: set[int] = set()
    for raw in raw_players:
        if not isinstance(raw, dict):
            continue
        try:
            player_id = int(raw.get("id"))
        except (TypeError, ValueError):
            continue
        name = " ".join(str(raw.get("name") or "").split())
        if not name or player_id in seen_ids:
            continue
        seen_ids.add(player_id)
        players.append(
            TeamPlayer(
                provider_player_id=player_id,
                name=name,
                age=safe_int(raw.get("age")),
                number=safe_int(raw.get("number")),
                position=" ".join(str(raw.get("position") or "").split()) or None,
                photo_url=raw.get("photo") if isinstance(raw.get("photo"), str) else None,
            )
        )
    position_order = {"Goalkeeper": 0, "Defender": 1, "Midfielder": 2, "Attacker": 3, "Forward": 3}
    return sorted(players, key=lambda player: (position_order.get(player.position or "", 4), player.name.casefold()))


def normalise_cached_players(value) -> list[TeamPlayer]:
    if not isinstance(value, list):
        return []
    players: list[TeamPlayer] = []
    for row in value:
        if not isinstance(row, dict):
            continue
        try:
            players.append(TeamPlayer.model_validate(row))
        except (TypeError, ValueError):
            continue
    return players


def build_season_snapshot(team: Team, fixtures: list, season_year: int) -> TeamSeasonSnapshot:
    season_label = repo.season_label(season_year)
    wins = draws = losses = goals_for = goals_against = 0
    form: list[str] = []
    for fixture in fixtures:
        home = fixture.home_team_id == team.id
        scored = fixture.home_goals if home else fixture.away_goals
        conceded = fixture.away_goals if home else fixture.home_goals
        scored = scored if isinstance(scored, int) else 0
        conceded = conceded if isinstance(conceded, int) else 0
        goals_for += scored
        goals_against += conceded
        if scored > conceded:
            wins += 1
            result = "W"
        elif scored < conceded:
            losses += 1
            result = "L"
        else:
            draws += 1
            result = "D"
        if len(form) < 5:
            form.append(result)

    return TeamSeasonSnapshot(
        season_year=season_year,
        season_label=season_label,
        overview=season_overview(
            team,
            fixtures,
            season_label,
            wins=wins,
            draws=draws,
            losses=losses,
            goals_for=goals_for,
            goals_against=goals_against,
        ),
        matches_played=len(fixtures),
        wins=wins,
        draws=draws,
        losses=losses,
        goals_for=goals_for,
        goals_against=goals_against,
        form=form,
        latest_matches=[fixture_to_summary(fixture) for fixture in fixtures[:5]],
    )


def season_overview(
    team: Team,
    fixtures: list,
    season_label: str,
    *,
    wins: int,
    draws: int,
    losses: int,
    goals_for: int,
    goals_against: int,
) -> str:
    if not fixtures:
        return f"Atmos FC has no finished {team.name} matches stored for the {season_label} season yet."

    recent = fixtures[:5]
    recent_wins = sum(1 for fixture in recent if result_for_team(team, fixture) == "W")
    latest = recent[0]
    opponent = latest.away_team.name if latest.home_team_id == team.id else latest.home_team.name
    score = f"{latest.home_goals}-{latest.away_goals}"
    result_word = {"W": "win", "D": "draw", "L": "defeat"}[result_for_team(team, latest)]
    record = f"{wins} {word(wins, 'win')}, {draws} {word(draws, 'draw')} and {losses} {word(losses, 'loss')}"
    return (
        f"Across matches stored by Atmos FC for {season_label}, {team.name} have recorded {record}, "
        f"scoring {goals_for} and conceding {goals_against}. They have won {recent_wins} of their last "
        f"{len(recent)}, with their latest result a {score} {result_word} against {opponent}."
    )


def result_for_team(team: Team, fixture) -> str:
    home = fixture.home_team_id == team.id
    scored = fixture.home_goals if home else fixture.away_goals
    conceded = fixture.away_goals if home else fixture.home_goals
    if scored is not None and conceded is not None and scored > conceded:
        return "W"
    if scored is not None and conceded is not None and scored < conceded:
        return "L"
    return "D"


def latest_match_analysis(fixtures: list) -> TeamMatchAnalysisSummary | None:
    if not fixtures:
        return None
    fixture = fixtures[0]
    cache = getattr(fixture, "analysis_cache", None)
    if cache is None or cache.status != "complete" or not isinstance(cache.payload, dict):
        return None
    meta = cache.payload.get("meta")
    if not isinstance(meta, dict):
        return None
    return TeamMatchAnalysisSummary(
        match=fixture_to_summary(fixture),
        overall_vibe=string_dict(meta.get("overall_vibe")),
        crowd_energy=string_dict(meta.get("crowd_energy")),
        peak_window=integer_dict(meta.get("peak_window")),
        total_comments=safe_int(cache.total_comments) or safe_int(meta.get("total_comments")) or 0,
    )


def major_achievements(content) -> list[str]:
    if content is None:
        return []
    return list(content.major_achievements)


def string_dict(value) -> dict[str, str]:
    if not isinstance(value, dict):
        return {}
    return {str(key): str(item) for key, item in value.items() if item is not None}


def integer_dict(value) -> dict[str, int] | None:
    if not isinstance(value, dict):
        return None
    result = {str(key): parsed for key, item in value.items() if (parsed := safe_int(item)) is not None}
    return result or None


def parse_timestamp(value) -> datetime | None:
    if not isinstance(value, str) or not value.strip():
        return None
    try:
        parsed = datetime.fromisoformat(value.replace("Z", "+00:00"))
    except ValueError:
        return None
    return parsed if parsed.tzinfo else parsed.replace(tzinfo=timezone.utc)


def safe_int(value) -> int | None:
    try:
        return int(value) if value is not None else None
    except (TypeError, ValueError):
        return None


def word(count: int, singular: str) -> str:
    return singular if count == 1 else f"{singular}s"
