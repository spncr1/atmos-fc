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

COMPETITION_EDITORIAL_CONTENT = {
    39: {
        "summary": (
            "England's top flight has operated as the Premier League since 1992, when its founding clubs "
            "broke away from the Football League. Twenty clubs play a home-and-away season, with promotion "
            "and relegation connecting the competition to the EFL Championship."
        ),
        "wikipedia_url": "https://en.wikipedia.org/wiki/Premier_League",
    },
    140: {
        "summary": (
            "La Liga is the highest level of Spanish football and has crowned the country's league champion "
            "since 1929. Its twenty clubs contest a 38-matchday season, with promotion and relegation linking "
            "the division to the Segunda Division."
        ),
        "wikipedia_url": "https://en.wikipedia.org/wiki/La_Liga",
    },
    78: {
        "summary": (
            "The Bundesliga is Germany's nationwide top division, launched for the 1963/64 season after the "
            "country moved away from regional elite leagues. Eighteen clubs compete from August to May, with "
            "promotion and relegation connecting the league to the 2. Bundesliga."
        ),
        "wikipedia_url": "https://en.wikipedia.org/wiki/Bundesliga",
    },
    135: {
        "summary": (
            "Serie A is Italy's highest division, taking on its national round-robin format in 1929/30 while "
            "building on an Italian championship first contested in 1898. Twenty clubs compete for the title, "
            "whose winners wear the scudetto emblem during the following season."
        ),
        "wikipedia_url": "https://en.wikipedia.org/wiki/Serie_A",
    },
    61: {
        "summary": (
            "Ligue 1 is France's professional top division. It began in 1932 as National, spent most of its "
            "history under the Division 1 name, and became Ligue 1 in 2002. Eighteen clubs now compete with "
            "promotion and relegation to and from Ligue 2."
        ),
        "wikipedia_url": "https://en.wikipedia.org/wiki/Ligue_1",
    },
    88: {
        "summary": (
            "The Eredivisie is the highest level of Dutch football. It was established in 1956 when the KNVB "
            "replaced the regional structure with a nationwide league in which the country's leading clubs "
            "could compete directly for the title."
        ),
        "wikipedia_url": "https://en.wikipedia.org/wiki/Eredivisie",
    },
    94: {
        "summary": (
            "Liga Portugal is the top tier of Portuguese football, tracing its national league competition to "
            "1934. The division adopted the Primeira Liga name after Portugal's professional league body took "
            "control of the country's two nationwide professional tiers in 1999."
        ),
        "wikipedia_url": "https://en.wikipedia.org/wiki/Primeira_Liga",
    },
    144: {
        "summary": (
            "The Belgian Pro League is Belgium's highest football division and traces its history to the "
            "country's first round-robin championship in 1895/96. Its structure has evolved repeatedly across "
            "more than a century of national competition, including changes to league and postseason formats."
        ),
        "wikipedia_url": "https://en.wikipedia.org/wiki/Belgian_Pro_League",
    },
    203: {
        "summary": (
            "The Super Lig is Turkey's highest football division. The modern nationwide league began as the "
            "Milli Lig in 1959, replacing an era built around major regional competitions and the short-lived "
            "Federation Cup used to determine Turkey's entrant in the European Cup."
        ),
        "wikipedia_url": "https://en.wikipedia.org/wiki/S%C3%BCper_Lig",
    },
    2: {
        "summary": (
            "The UEFA Champions League is Europe's premier annual club competition. Founded in 1955 as the "
            "European Champion Clubs' Cup and rebranded in 1992, it brings together leading clubs from UEFA's "
            "national associations through qualifying, a league phase and knockout rounds."
        ),
        "wikipedia_url": "https://en.wikipedia.org/wiki/UEFA_Champions_League",
    },
    3: {
        "summary": (
            "The UEFA Europa League is the second tier of European club competition. Introduced in 1971 as the "
            "UEFA Cup and renamed in 2009, it now progresses from qualifying into a league phase and knockouts, "
            "with the winner earning a place in the following Champions League."
        ),
        "wikipedia_url": "https://en.wikipedia.org/wiki/UEFA_Europa_League",
    },
    848: {
        "summary": (
            "The UEFA Conference League is Europe's third-tier club competition, launched in 2021 to give more "
            "clubs and national associations a route into continental football. Originally called the Europa "
            "Conference League, it adopted its shorter name and a 36-team league phase in 2024/25."
        ),
        "wikipedia_url": "https://en.wikipedia.org/wiki/UEFA_Conference_League",
    },
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
    editorial = COMPETITION_EDITORIAL_CONTENT[provider_competition_id]

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
        wikipedia_url=safe_official_url(editorial["wikipedia_url"]),
        summary=editorial["summary"],
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


def safe_official_url(value: str | None) -> str | None:
    if not value:
        return None
    parsed = urlparse(value)
    if parsed.scheme not in {"http", "https"} or not parsed.netloc:
        return None
    return value
