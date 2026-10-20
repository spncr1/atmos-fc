"""Export a validated, read-only Atmos FC showcase bundle from the local database."""

from __future__ import annotations

import argparse
import asyncio
import hashlib
import json
import os
import re
import shutil
import tempfile
from collections import defaultdict
from datetime import datetime, timezone
from pathlib import Path
from typing import Any

from sqlalchemy import select
from sqlalchemy.orm import joinedload, selectinload

from backend.database.models import Competition, CompetitionSeason, Fixture, Season, Team
from backend.database.session import get_sessionmaker
from backend.models.schemas import (
    AnalysisResponse,
    CompetitionClub,
    CompetitionOption,
    CompetitionProfileResponse,
    MatchSummary,
    MetadataResponse,
    SeasonOption,
    TeamProfileResponse,
)
from backend.repositories.football_data import season_label
from backend.services.competition_profiles import (
    COMPETITION_EDITORIAL_CONTENT,
    OFFICIAL_COMPETITION_WEBSITES,
    safe_official_url,
)
from backend.services.matches import SUPPORTED_COMPETITIONS, fixture_to_summary
from backend.services.team_honours import cached_honours, manual_honours
from backend.services.team_profile_content import profile_content_for
from backend.services.team_profiles import normalise_cached_players, parse_timestamp, profile_response
from backend.services.team_visuals import team_visual_response

ANALYSIS_VERSION = "match_context_v2"
REFRESHED_CODES = ("PL", "PD", "BL1", "SA", "FL1", "NED1", "POR1", "CL")
ARCHIVE_ONLY_CODES = ("BEL1", "TUR1", "EL", "UECL")
FORBIDDEN_KEYS = {
    "raw_payload", "error_message", "needs_review", "profile_needs_review",
    "wikidata_qid", "provider", "internal_id", "matching_diagnostics",
}
SECRET_PATTERN = re.compile(r"(?:api[_-]?key|authorization|bearer\s+[a-z0-9._-]{12,}|database_url)", re.I)


def json_bytes(value: Any) -> bytes:
    return json.dumps(value, ensure_ascii=False, separators=(",", ":"), sort_keys=True).encode("utf-8")


def write_json(path: Path, value: Any) -> None:
    path.write_bytes(json_bytes(value) + b"\n")


def current_analysis(fixture: Fixture) -> bool:
    cache = fixture.analysis_cache
    return bool(
        cache
        and cache.status == "complete"
        and isinstance(cache.payload, dict)
        and isinstance(cache.payload.get("meta"), dict)
        and cache.payload["meta"].get("analysis_version") == ANALYSIS_VERSION
    )


def public_match(fixture: Fixture) -> dict[str, Any]:
    summary = fixture_to_summary(fixture).model_dump(mode="json")
    summary["analysis_available"] = current_analysis(fixture)
    summary["home_team_key"] = str(summary["home_team_id"])
    summary["away_team_key"] = str(summary["away_team_id"])
    return MatchSummary.model_validate(summary).model_dump(mode="json")


def public_facts(team: Team) -> dict[str, Any]:
    enrichment = team.profile_enrichment
    facts = enrichment.facts_json if enrichment and isinstance(enrichment.facts_json, dict) else {}
    allowed = {}
    for key in ("current_players", "current_players_updated_at", "major_honours"):
        if key in facts:
            allowed[key] = facts[key]
    return allowed


def validate_public_document(value: Any, path: str = "$") -> None:
    if isinstance(value, dict):
        for key, child in value.items():
            if str(key).lower() in FORBIDDEN_KEYS:
                raise ValueError(f"Prohibited field {path}.{key}")
            validate_public_document(child, f"{path}.{key}")
        return
    if isinstance(value, list):
        for index, child in enumerate(value):
            validate_public_document(child, f"{path}[{index}]")
        return
    if isinstance(value, str) and SECRET_PATTERN.search(value):
        raise ValueError(f"Possible credential or internal configuration at {path}")


async def build_bundle() -> dict[str, Any]:
    definitions = {int(row["provider_id"]): row for row in SUPPORTED_COMPETITIONS}
    sessionmaker = get_sessionmaker()
    async with sessionmaker() as session:
        fixture_result = await session.execute(
            select(Fixture)
            .options(
                joinedload(Fixture.competition),
                joinedload(Fixture.season),
                joinedload(Fixture.home_team).joinedload(Team.visual_profile),
                joinedload(Fixture.away_team).joinedload(Team.visual_profile),
                joinedload(Fixture.youtube_comment_cache),
                joinedload(Fixture.event_sync_status),
                joinedload(Fixture.analysis_cache),
            )
            .order_by(Fixture.kickoff_at.desc(), Fixture.provider_fixture_id.desc())
        )
        fixtures = list(fixture_result.scalars().unique())
        team_result = await session.execute(
            select(Team)
            .options(joinedload(Team.visual_profile), joinedload(Team.profile_enrichment))
            .order_by(Team.name)
        )
        teams = list(team_result.scalars().unique())
        competition_result = await session.execute(select(Competition).order_by(Competition.provider_competition_id))
        competitions = list(competition_result.scalars())
        season_result = await session.execute(select(Season).order_by(Season.year.desc()))
        seasons = list(season_result.scalars())
        competition_season_result = await session.execute(
            select(CompetitionSeason).options(joinedload(CompetitionSeason.competition), joinedload(CompetitionSeason.season))
        )
        competition_seasons = list(competition_season_result.scalars().unique())

    summaries_by_fixture = {fixture.id: public_match(fixture) for fixture in fixtures}
    public_matches = [summaries_by_fixture[fixture.id] for fixture in fixtures]
    fixtures_by_team_season: dict[tuple[int, int], list[Fixture]] = defaultdict(list)
    fixtures_by_competition_season: dict[tuple[int, int], list[Fixture]] = defaultdict(list)
    for fixture in fixtures:
        fixtures_by_team_season[(fixture.home_team_id, fixture.season.year)].append(fixture)
        fixtures_by_team_season[(fixture.away_team_id, fixture.season.year)].append(fixture)
        fixtures_by_competition_season[(fixture.competition.provider_competition_id, fixture.season.year)].append(fixture)

    team_profiles: dict[str, dict[str, Any]] = {}
    for team in teams:
        seasons_for_team = sorted(
            (year for team_id, year in fixtures_by_team_season if team_id == team.id), reverse=True
        )
        if not seasons_for_team:
            continue
        enrichment = team.profile_enrichment
        facts = public_facts(team)
        players = normalise_cached_players(facts.get("current_players"))
        players_updated_at = parse_timestamp(facts.get("current_players_updated_at"))
        honours = manual_honours(profile_content_for(team.name)) or cached_honours(enrichment)
        profiles: dict[str, Any] = {}
        for year in seasons_for_team:
            rows = fixtures_by_team_season[(team.id, year)]
            response = profile_response(
                team,
                enrichment=enrichment,
                data_source="showcase",
                visual_profile=team.visual_profile,
                selected_season_year=year,
                fixtures=rows,
                current_players=players,
                current_players_updated_at=players_updated_at,
                honours=honours,
            ).model_dump(mode="json")
            response["facts"] = {}
            response["wikidata_qid"] = None
            response["profile_needs_review"] = False
            response["data_source"] = "showcase"
            if response.get("latest_match_analysis"):
                latest_id = str(response["latest_match_analysis"]["match"]["id"])
                if not any(item["id"] == latest_id and item["analysis_available"] for item in public_matches):
                    response["latest_match_analysis"] = None
            validated = TeamProfileResponse.model_validate(response).model_dump(mode="json")
            validated.pop("wikidata_qid", None)
            validated.pop("profile_needs_review", None)
            profiles[str(year)] = validated
        team_profiles[str(team.provider_team_id)] = {
            "team_key": str(team.provider_team_id),
            "aliases": [str(team.provider_team_id)],
            "name_key": normalise_name(team.name),
            "profiles": profiles,
        }

    cs_by_key = {
        (row.competition.provider_competition_id, row.season.year): row
        for row in competition_seasons
    }
    competition_profiles: dict[str, dict[str, Any]] = {}
    for provider_id, definition in definitions.items():
        years = sorted(
            {year for competition_id, year in fixtures_by_competition_season if competition_id == provider_id}
            | {year for competition_id, year in cs_by_key if competition_id == provider_id},
            reverse=True,
        )
        stored = next((row for row in competitions if row.provider_competition_id == provider_id), None)
        per_season: dict[str, Any] = {}
        for year in years:
            rows = fixtures_by_competition_season.get((provider_id, year), [])
            clubs_by_id = {team.provider_team_id: team for fixture in rows for team in (fixture.home_team, fixture.away_team)}
            cs = cs_by_key.get((provider_id, year))
            editorial = COMPETITION_EDITORIAL_CONTENT[provider_id]
            response = CompetitionProfileResponse(
                provider_competition_id=provider_id,
                code=str(definition["code"]),
                name=stored.name if stored else str(definition["name"]),
                short_name=str(definition["short_name"]),
                competition_type=(stored.type if stored else None) or ("Cup" if definition["group"] == "European competitions" else "League"),
                country_name="Europe" if definition["group"] == "European competitions" else ((stored.country_name if stored else None) or definition.get("country_name")),
                country_code=(stored.country_code if stored else None) or definition.get("country_code"),
                logo_url=(stored.logo_url if stored else None) or definition.get("logo_url"),
                flag_url=stored.flag_url if stored else None,
                official_website=safe_official_url(OFFICIAL_COMPETITION_WEBSITES.get(provider_id)),
                wikipedia_url=safe_official_url(editorial["wikipedia_url"]),
                summary=editorial["summary"],
                season_year=year,
                season_label=cs.season.label if cs else season_label(year),
                season_start_date=cs.start_date if cs else None,
                season_end_date=cs.end_date if cs else None,
                season_is_current=bool(cs.is_current) if cs else False,
                finished_match_count=len(rows),
                goals_scored=sum((row.home_goals or 0) + (row.away_goals or 0) for row in rows),
                club_count=len(clubs_by_id),
                latest_result_at=rows[0].kickoff_at if rows else None,
                latest_matches=[MatchSummary.model_validate(summaries_by_fixture[row.id]) for row in rows[:6]],
                clubs=[
                    CompetitionClub(provider_team_id=club.provider_team_id, team_key=str(club.provider_team_id), name=club.name, logo_url=club.logo_url, country_name=club.country_name)
                    for club in sorted(clubs_by_id.values(), key=lambda item: item.name.casefold())
                ],
                data_source="showcase",
            )
            per_season[str(year)] = response.model_dump(mode="json")
        competition_profiles[str(provider_id)] = per_season

    analyses: dict[str, Any] = {}
    for fixture in fixtures:
        if not current_analysis(fixture):
            continue
        response = AnalysisResponse.model_validate(fixture.analysis_cache.payload)
        response.match = MatchSummary.model_validate(summaries_by_fixture[fixture.id])
        analyses[str(fixture.provider_fixture_id)] = response.model_dump(mode="json")

    metadata = MetadataResponse(
        competitions=[
            CompetitionOption(
                code=str(row["code"]), name=str(row["name"]), short_name=str(row["short_name"]),
                provider_id=int(row["provider_id"]), logo_url=row.get("logo_url"), country_name=row.get("country_name"),
                country_code=row.get("country_code"), group=str(row["group"]),
            ) for row in SUPPORTED_COMPETITIONS
        ],
        seasons=[SeasonOption(year=row.year, label=row.label, is_current=row.is_current) for row in seasons],
    ).model_dump(mode="json")

    results_through = max((row["date"] for row in public_matches), default=None)
    return {
        "metadata.json": metadata,
        "matches.json": {"matches": public_matches},
        "teams.json": {"teams": team_profiles},
        "competitions.json": {"competitions": competition_profiles},
        "analyses.json": {"analyses": analyses},
        "_manifest_fields": {
            "schema_version": 1,
            "generated_at": datetime.now(timezone.utc).isoformat(),
            "results_through": results_through,
            "fixture_count": len(public_matches),
            "analysis_count": len(analyses),
            "team_count": len(team_profiles),
            "refreshed_competitions": list(REFRESHED_CODES),
            "archive_only_competitions": list(ARCHIVE_ONLY_CODES),
        },
    }


def normalise_name(value: str) -> str:
    return re.sub(r"[^a-z0-9]+", "", value.casefold().replace("ß", "ss"))


def publish(bundle: dict[str, Any], destination: Path) -> dict[str, Any]:
    manifest_fields = bundle.pop("_manifest_fields")
    for filename, document in bundle.items():
        validate_public_document(document, filename)
    integrity = hashlib.sha256(b"".join(json_bytes(bundle[name]) for name in sorted(bundle))).hexdigest()
    manifest = {**manifest_fields, "integrity": {"algorithm": "sha256", "bundle_checksum": integrity}}
    validate_public_document(manifest, "manifest.json")

    destination.parent.mkdir(parents=True, exist_ok=True)
    temporary = Path(tempfile.mkdtemp(prefix=".showcase-", dir=destination.parent))
    backup = destination.with_name(f".{destination.name}-previous")
    try:
        for filename, document in bundle.items():
            write_json(temporary / filename, document)
        write_json(temporary / "manifest.json", manifest)
        if backup.exists():
            shutil.rmtree(backup)
        if destination.exists():
            os.replace(destination, backup)
        os.replace(temporary, destination)
        if backup.exists():
            shutil.rmtree(backup)
    except Exception:
        if destination.exists() is False and backup.exists():
            os.replace(backup, destination)
        shutil.rmtree(temporary, ignore_errors=True)
        raise
    return manifest


async def run(destination: Path) -> None:
    bundle = await build_bundle()
    manifest = publish(bundle, destination)
    print(json.dumps(manifest, indent=2))


def main() -> None:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--output", type=Path, default=Path("frontend/data/showcase"))
    args = parser.parse_args()
    asyncio.run(run(args.output.resolve()))


if __name__ == "__main__":
    main()
