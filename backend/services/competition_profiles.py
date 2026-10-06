"""Season-specific competition database reads backed by stored Atmos FC data."""

from __future__ import annotations

from urllib.parse import urlparse

from sqlalchemy.exc import SQLAlchemyError

from backend.database.session import get_sessionmaker
from backend.models.schemas import CompetitionClub, CompetitionProfileResponse
from backend.repositories import football_data as repo
from backend.services.matches import SUPPORTED_COMPETITIONS, fixture_to_summary


class CompetitionProfileNotFoundError(RuntimeError):
    """Raised when a competition is outside Atmos FC's supported set."""


class CompetitionProfileUnavailableError(RuntimeError):
    """Raised when stored competition data cannot be read."""


OFFICIAL_COMPETITION_WEBSITES = {
    39: "https://www.premierleague.com/",
    140: "https://www.laliga.com/",
    78: "https://www.bundesliga.com/",
    135: "https://www.legaseriea.it/",
    61: "https://ligue1.com/",
    88: "https://eredivisie.nl/",
    94: "https://www.ligaportugal.pt/",
    144: "https://www.proleague.be/",
    203: "https://www.tff.org/default.aspx?pageID=80",
    2: "https://www.uefa.com/uefachampionsleague/",
    3: "https://www.uefa.com/uefaeuropaleague/",
    848: "https://www.uefa.com/uefaconferenceleague/",
}

SUPPORTED_BY_PROVIDER_ID = {
    int(definition["provider_id"]): definition
    for definition in SUPPORTED_COMPETITIONS
}


async def competition_profile(
    provider_competition_id: int,
    season_year: int,
) -> CompetitionProfileResponse:
    """Return one supported competition's stored snapshot for a season."""

    definition = SUPPORTED_BY_PROVIDER_ID.get(provider_competition_id)
    if definition is None:
        raise CompetitionProfileNotFoundError("Competition is not supported by Atmos FC.")

    try:
        sessionmaker = get_sessionmaker()
        async with sessionmaker() as session:
            competition = await repo.competition_by_provider_id(session, provider_competition_id)
            competition_season = await repo.competition_season_by_provider_and_year(
                session,
                provider_competition_id,
                season_year,
            )
            fixtures = await repo.competition_finished_fixtures(
                session,
                provider_competition_id,
                season_year,
            )
    except SQLAlchemyError as exc:
        raise CompetitionProfileUnavailableError(
            "Competition database could not be loaded from the local database."
        ) from exc

    clubs_by_id = {}
    for fixture in fixtures:
        clubs_by_id[fixture.home_team.provider_team_id] = fixture.home_team
        clubs_by_id[fixture.away_team.provider_team_id] = fixture.away_team
    clubs = [
        CompetitionClub(
            provider_team_id=team.provider_team_id,
            name=team.name,
            logo_url=team.logo_url,
            country_name=team.country_name,
        )
        for team in sorted(clubs_by_id.values(), key=lambda item: item.name.casefold())
    ]

    season = competition_season.season if competition_season else None
    name = competition.name if competition else str(definition["name"])
    group = str(definition["group"])
    competition_type = (competition.type if competition else None) or (
        "Cup" if group == "European competitions" else "League"
    )
    country_name = (competition.country_name if competition else None) or definition.get("country_name")
    if group == "European competitions" and country_name == "World":
        country_name = "Europe"
    season_label = season.label if season else repo.season_label(season_year)

    return CompetitionProfileResponse(
        provider_competition_id=provider_competition_id,
        code=str(definition["code"]),
        name=name,
        short_name=str(definition["short_name"]),
        competition_type=competition_type,
        country_name=country_name,
        country_code=(competition.country_code if competition else None) or definition.get("country_code"),
        logo_url=(competition.logo_url if competition else None) or definition.get("logo_url"),
        flag_url=competition.flag_url if competition else None,
        official_website=safe_official_url(OFFICIAL_COMPETITION_WEBSITES.get(provider_competition_id)),
        summary=competition_summary(name, competition_type, country_name, season_label, group),
        season_year=season_year,
        season_label=season_label,
        season_start_date=competition_season.start_date if competition_season else None,
        season_end_date=competition_season.end_date if competition_season else None,
        season_is_current=bool(competition_season.is_current) if competition_season else False,
        finished_match_count=len(fixtures),
        goals_scored=sum(
            (fixture.home_goals or 0) + (fixture.away_goals or 0)
            for fixture in fixtures
        ),
        club_count=len(clubs),
        latest_result_at=fixtures[0].kickoff_at if fixtures else None,
        latest_matches=[fixture_to_summary(fixture) for fixture in fixtures[:6]],
        clubs=clubs,
        data_source="local" if competition else "supported_definition",
    )


def competition_summary(
    name: str,
    competition_type: str,
    country_name: str | None,
    season_label: str,
    group: str,
) -> str:
    if group == "European competitions":
        identity = "a European club competition"
    elif competition_type.casefold() == "league" and country_name:
        identity = f"a domestic league in {country_name}"
    elif country_name:
        identity = f"a {competition_type.lower()} competition in {country_name}"
    else:
        identity = f"a {competition_type.lower()} competition"
    return (
        f"{name} is {identity}. Atmos FC records finished matches and post-match fan reaction "
        f"for the {season_label} season."
    )


def safe_official_url(value: str | None) -> str | None:
    if not value:
        return None
    parsed = urlparse(value)
    if parsed.scheme not in {"http", "https"} or not parsed.netloc:
        return None
    return value
