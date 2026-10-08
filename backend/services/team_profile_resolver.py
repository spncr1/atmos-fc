"""Resolve API-Football teams to sourced Wikidata profile enrichment."""

from __future__ import annotations

from dataclasses import dataclass
from datetime import datetime, timezone
from typing import Any
import re
import unicodedata

from sqlalchemy.ext.asyncio import AsyncSession

from backend.database.models import Team, TeamProfileEnrichment
from backend.providers.wikidata import (
    WikidataClient,
    WikidataEntity,
    WikidataSearchResult,
    claim_entity_ids,
    claim_strings,
    claim_time_years,
    english_wikipedia_title,
    english_wikipedia_url,
)
from backend.providers.errors import ProviderError
from backend.providers.wikipedia import WikipediaClient
from backend.repositories import football_data as repo


MIN_CONFIDENT_SCORE = 70
MAX_SEARCH_NAMES = 4
MAX_ENTITIES_TO_SCORE = 10

FOOTBALL_CLUB_QIDS = {
    "Q476028",  # association football club
    "Q847017",  # sports club
}
NATIONAL_TEAM_QIDS = {
    "Q6979593",  # national association football team
}


@dataclass(frozen=True)
class TeamProfileMatch:
    """One scored Wikidata candidate for a team."""

    score: int
    needs_review: bool
    reasons: tuple[str, ...]
    search_result: WikidataSearchResult
    entity: WikidataEntity


async def resolve_team_profile_enrichment(
    session: AsyncSession,
    team: Team,
    *,
    client: WikidataClient | None = None,
    wikipedia_client: WikipediaClient | None = None,
    force: bool = False,
) -> TeamProfileEnrichment:
    """Resolve and cache the best Wikidata profile match for a team."""

    cached = await repo.team_profile_enrichment_for_team(session, team)
    if cached and cached.wikidata_qid and not cached.needs_review and not force:
        if cached.summary or not cached.wikipedia_title:
            return cached
        return await enrich_wikipedia_summary(cached, wikipedia_client or WikipediaClient())

    wikidata = client or WikidataClient()
    matches = await candidate_matches(wikidata, team)
    best = matches[0] if matches else None
    if best is None:
        return await repo.upsert_team_profile_enrichment(
            session,
            team,
            confidence=0,
            needs_review=True,
            source_updated_at=datetime.now(timezone.utc),
            raw_payload={"status": "no_wikidata_candidates", "searched": team_name_candidates(team)},
        )

    is_confident = best.score >= MIN_CONFIDENT_SCORE
    enrichment = await repo.upsert_team_profile_enrichment(
        session,
        team,
        wikidata_qid=best.entity.entity_id if is_confident else None,
        wikipedia_title=english_wikipedia_title(best.entity) if is_confident else None,
        wikipedia_url=english_wikipedia_url(best.entity) if is_confident else None,
        facts_json=entity_facts(best.entity) if is_confident else None,
        confidence=best.score,
        needs_review=not is_confident,
        source_updated_at=datetime.now(timezone.utc),
        attribution_url=f"https://www.wikidata.org/wiki/{best.entity.entity_id}" if is_confident else None,
        license_label="CC0",
        raw_payload={
            "status": "matched" if is_confident else "needs_review",
            "searched": team_name_candidates(team),
            "best_candidate": match_payload(best),
            "candidates": [match_payload(match) for match in matches[:5]],
        },
    )
    if is_confident and enrichment.wikipedia_title:
        return await enrich_wikipedia_summary(enrichment, wikipedia_client or WikipediaClient())
    return enrichment


async def enrich_wikipedia_summary(
    enrichment: TeamProfileEnrichment,
    client: WikipediaClient,
) -> TeamProfileEnrichment:
    """Cache a readable Wikipedia introduction without blocking Wikidata facts."""

    try:
        payload = await client.page_summary(enrichment.wikipedia_title or "")
    except ProviderError:
        return enrichment

    extract = payload.get("extract")
    summary = concise_wikipedia_summary(extract if isinstance(extract, str) else None)
    if not summary:
        return enrichment

    enrichment.summary = summary
    enrichment.source_updated_at = datetime.now(timezone.utc)
    enrichment.license_label = "Wikipedia text: CC BY-SA 4.0; Wikidata: CC0"
    raw_payload = dict(enrichment.raw_payload or {})
    raw_payload["wikipedia_summary"] = {
        "title": enrichment.wikipedia_title,
        "pageid": payload.get("pageid"),
        "timestamp": payload.get("timestamp"),
    }
    enrichment.raw_payload = raw_payload
    return enrichment


def concise_wikipedia_summary(value: str | None, *, max_characters: int = 560) -> str | None:
    """Keep a short readable introduction rather than an entire article extract."""

    clean = " ".join(str(value or "").split())
    if not clean:
        return None
    sentences = re.split(r"(?<=[.!?])\s+", clean)
    selected: list[str] = []
    for sentence in sentences:
        candidate = " ".join([*selected, sentence]).strip()
        if selected and len(candidate) > max_characters:
            break
        selected.append(sentence)
        if len(selected) >= 3:
            break
    summary = " ".join(selected).strip()
    if len(summary) <= max_characters:
        return summary
    clipped = summary[: max_characters + 1].rsplit(" ", 1)[0].rstrip(" ,;:")
    return f"{clipped}…"


async def candidate_matches(client: WikidataClient, team: Team) -> list[TeamProfileMatch]:
    """Return Wikidata candidates ordered by match confidence."""

    search_results: list[WikidataSearchResult] = []
    seen_ids: set[str] = set()
    for name in team_name_candidates(team)[:MAX_SEARCH_NAMES]:
        for result in await client.search_entities(name, limit=8):
            if result.entity_id in seen_ids:
                continue
            search_results.append(result)
            seen_ids.add(result.entity_id)
            if len(search_results) >= MAX_ENTITIES_TO_SCORE:
                break
        if len(search_results) >= MAX_ENTITIES_TO_SCORE:
            break

    matches: list[TeamProfileMatch] = []
    for result in search_results:
        entity = await client.entity(result.entity_id)
        matches.append(score_match(team, result, entity))

    return sorted(matches, key=lambda match: match.score, reverse=True)


def score_match(team: Team, search_result: WikidataSearchResult, entity: WikidataEntity) -> TeamProfileMatch:
    """Score how likely a Wikidata entity is to describe the API-Football team."""

    score = 0
    reasons: list[str] = []
    team_names = {normalise(name) for name in team_name_candidates(team)}
    entity_names = {
        normalise(value)
        for value in (
            entity.label,
            search_result.label,
            *entity.aliases,
            *search_result.aliases,
        )
        if value
    }
    entity_text = normalise(
        " ".join(
            value
            for value in (
                entity.label,
                entity.description,
                search_result.label,
                search_result.description,
                english_wikipedia_title(entity),
            )
            if value
        )
    )

    if team_names & entity_names:
        score += 45
        reasons.append("name_exact")
    elif any(name and (name in entity_text or any(entity_name in name for entity_name in entity_names)) for name in team_names):
        score += 25
        reasons.append("name_partial")

    if team.country_name and normalise(team.country_name) in entity_text:
        score += 10
        reasons.append("country_text")

    if team.founded and team.founded in founding_years(entity):
        score += 15
        reasons.append("founded_year")

    instance_ids = set(claim_entity_ids(entity, "P31"))
    if team.is_national:
        if instance_ids & NATIONAL_TEAM_QIDS or "national" in entity_text:
            score += 20
            reasons.append("national_team_type")
    elif instance_ids & FOOTBALL_CLUB_QIDS or "football club" in entity_text or "association football club" in entity_text:
        score += 15
        reasons.append("club_type")

    if english_wikipedia_title(entity):
        score += 10
        reasons.append("english_wikipedia")

    if team.code and normalise(team.code) in entity_names:
        score += 5
        reasons.append("team_code")

    score = min(score, 100)
    return TeamProfileMatch(
        score=score,
        needs_review=score < MIN_CONFIDENT_SCORE,
        reasons=tuple(reasons),
        search_result=search_result,
        entity=entity,
    )


def team_name_candidates(team: Team) -> list[str]:
    """Return provider-backed team names worth searching in Wikidata."""

    raw_team = (team.raw_payload or {}).get("team") if isinstance(team.raw_payload, dict) else None
    candidates = [
        team.name,
        raw_team.get("name") if isinstance(raw_team, dict) else None,
        raw_team.get("code") if isinstance(raw_team, dict) else None,
        team.code,
        strip_fc_suffix(team.name),
    ]
    return unique_clean_values(candidates)


def entity_facts(entity: WikidataEntity) -> dict[str, Any]:
    """Extract factual profile fields that are safe to cache from Wikidata."""

    return {
        "wikidata_label": entity.label,
        "wikidata_description": entity.description,
        "aliases": list(entity.aliases),
        "instance_of": claim_entity_ids(entity, "P31"),
        "country": claim_entity_ids(entity, "P17"),
        "home_venue": claim_entity_ids(entity, "P115"),
        "inception_years": claim_time_years(entity, "P571"),
        "official_websites": claim_strings(entity, "P856"),
    }


def match_payload(match: TeamProfileMatch) -> dict[str, Any]:
    return {
        "score": match.score,
        "needs_review": match.needs_review,
        "reasons": list(match.reasons),
        "entity_id": match.entity.entity_id,
        "label": match.entity.label,
        "description": match.entity.description,
        "wikipedia_title": english_wikipedia_title(match.entity),
    }


def founding_years(entity: WikidataEntity) -> set[int]:
    return set(claim_time_years(entity, "P571"))


def strip_fc_suffix(value: str | None) -> str | None:
    if not value:
        return None
    return re.sub(r"\b(f\.?c\.?|football club|cf|afc|sc)\b", "", value, flags=re.IGNORECASE).strip(" -")


def unique_clean_values(values: list[str | None]) -> list[str]:
    seen: set[str] = set()
    clean_values: list[str] = []
    for value in values:
        clean = " ".join(str(value or "").split())
        key = normalise(clean)
        if clean and key and key not in seen:
            clean_values.append(clean)
            seen.add(key)
    return clean_values


def normalise(value: str | None) -> str:
    normalised = unicodedata.normalize("NFKD", value or "")
    ascii_value = normalised.encode("ascii", "ignore").decode("ascii")
    return " ".join(re.sub(r"[^a-zA-Z0-9]+", " ", ascii_value).lower().split())
