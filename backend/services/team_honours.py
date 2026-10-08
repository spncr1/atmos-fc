"""Structured, cached major-honours enrichment for every stored club."""

from __future__ import annotations

from dataclasses import dataclass
from datetime import datetime, timezone
import re
from typing import Any

from backend.database.models import Team, TeamProfileEnrichment
from backend.providers.errors import ProviderError
from backend.providers.wikipedia import WikipediaClient
from backend.services.team_profile_content import TeamProfileContent


HONOURS_CACHE_KEY = "major_honours"
HONOURS_SECTION_NAMES = ("honours", "honors", "achievements", "trophies")
WIN_LABEL = re.compile(r"\b(?:champions?|winners?|co-winners?|shared)\b", re.IGNORECASE)
COUNT_LABEL = re.compile(r"\((\d+)\)")
LOWER_OR_MINOR = re.compile(
    r"\b(?:second|third|fourth) division\b|\b(?:level|tier) [2-9]\b|"
    r"championship play-offs?|league one|league two|segunda divisi[oó]n|serie b|"
    r"2\. bundesliga|ligue 2|eerste divisie|youth|under-?\d+|reserve|regional|county|"
    r"friendly|invitational|pre-season|war league|intermediate|amateur|"
    r"sheriff of london|intertoto|inter-cities fairs",
    re.IGNORECASE,
)
EUROPEAN_OR_WORLD = re.compile(
    r"champions league|european cup|europa league|uefa cup\b|conference league|"
    r"cup winners.? cup|uefa super cup|intercontinental cup|"
    r"club world cup|fifa intercontinental cup",
    re.IGNORECASE,
)
DOMESTIC_CUP = re.compile(
    r"\b(?:fa|efl|league|national|federation|football association) cup\b|"
    r"copa del rey|coppa italia|coupe de france|dfb-pokal|knvb cup|ta[cç]a de portugal|"
    r"t[uü]rkiye kupas[ıi]|belgian cup|scottish cup|welsh cup|irish cup|"
    r"super ?cup|superta[cç]a|community shield|charity shield|troph[eé]e des champions|"
    r"johan cruyff shield",
    re.IGNORECASE,
)
TOP_FLIGHT = re.compile(
    r"\b(?:level|tier) 1\b|premier league|la liga|bundesliga|serie a|ligue 1|"
    r"eredivisie|primeira liga|liga portugal|pro league|s[uü]per lig|"
    r"top division|top-flight|national championship",
    re.IGNORECASE,
)


@dataclass(frozen=True)
class Honour:
    title: str
    count: int


@dataclass(frozen=True)
class TeamHonours:
    status: str
    honours: tuple[Honour, ...]
    source_url: str | None

    @property
    def total(self) -> int | None:
        if self.status not in {"complete", "verified_zero"}:
            return None
        return sum(honour.count for honour in self.honours)


def manual_honours(content: TeamProfileContent | None) -> TeamHonours | None:
    """Convert reviewed editorial records into the shared structured shape."""

    if content is None or content.major_trophy_count is None:
        return None
    honours: list[Honour] = []
    for value in content.major_achievements:
        match = re.match(r"^(\d+)\s+(.+)$", value.strip())
        if match:
            honours.append(Honour(title=match.group(2), count=int(match.group(1))))
    return TeamHonours(
        status="complete" if honours else "verified_zero",
        honours=tuple(honours),
        source_url=content.achievements_source_url,
    )


def cached_honours(enrichment: TeamProfileEnrichment | None) -> TeamHonours | None:
    facts = enrichment.facts_json if enrichment and isinstance(enrichment.facts_json, dict) else {}
    payload = facts.get(HONOURS_CACHE_KEY)
    if not isinstance(payload, dict) or payload.get("status") not in {"complete", "verified_zero"}:
        return None
    honours = []
    for row in payload.get("honours") or []:
        if not isinstance(row, dict):
            continue
        title = " ".join(str(row.get("title") or "").split())
        try:
            count = int(row.get("count"))
        except (TypeError, ValueError):
            continue
        if title and count > 0:
            honours.append(Honour(title=title, count=count))
    return TeamHonours(str(payload["status"]), tuple(honours), clean_url(payload.get("source_url")))


async def ensure_team_honours(
    team: Team,
    enrichment: TeamProfileEnrichment | None,
    *,
    manual_content: TeamProfileContent | None = None,
    client: WikipediaClient | None = None,
    force: bool = False,
) -> TeamHonours | None:
    """Return cached/reviewed honours, enriching a resolved Wikipedia club once."""

    reviewed = manual_honours(manual_content)
    if reviewed is not None:
        return reviewed
    cached = None if force else cached_honours(enrichment)
    if cached is not None:
        return cached
    if enrichment is None or enrichment.needs_review or not enrichment.wikipedia_title:
        return None

    wikipedia = client or WikipediaClient()
    try:
        sections = await wikipedia.page_sections(enrichment.wikipedia_title)
        section = next(
            (
                row for row in sections
                if any(
                    name in normalise_heading(row.get("line")).split()
                    for name in HONOURS_SECTION_NAMES
                )
            ),
            None,
        )
        if section is None or section.get("index") is None:
            return None
        wikitext = await wikipedia.section_wikitext(enrichment.wikipedia_title, str(section["index"]))
    except ProviderError:
        return None

    honours = parse_major_honours(wikitext)
    if honours:
        status = "complete"
    elif can_verify_zero(wikitext):
        status = "verified_zero"
    else:
        return None
    source_url = enrichment.wikipedia_url
    result = TeamHonours(status, tuple(honours), source_url)
    facts = dict(enrichment.facts_json or {})
    facts[HONOURS_CACHE_KEY] = {
        "status": status,
        "honours": [{"title": honour.title, "count": honour.count} for honour in honours],
        "source_url": source_url,
        "updated_at": datetime.now(timezone.utc).isoformat(),
        "method": "wikipedia_honours_section",
    }
    enrichment.facts_json = facts
    enrichment.source_updated_at = datetime.now(timezone.utc)
    return result


def parse_major_honours(wikitext: str) -> list[Honour]:
    """Extract winner counts from the common nested-list honours format."""

    current_competition: tuple[str, str] | None = None
    counts: dict[str, int] = {}
    display_names: dict[str, str] = {}
    for raw_line in str(wikitext or "").splitlines():
        line = raw_line.strip()
        if line.startswith("*") and not line.startswith("**"):
            cleaned = clean_wikitext(line[1:])
            direct_count = winner_count(cleaned)
            title = competition_title(cleaned)
            current_competition = (title, cleaned) if title else None
            if current_competition and direct_count and is_major_competition(*current_competition):
                add_honour(counts, display_names, title, direct_count)
            continue
        if not line.startswith("**") or current_competition is None or not WIN_LABEL.search(line):
            continue
        if re.search(r"runners?-up|finalists?|semi-final", line, re.IGNORECASE):
            continue
        count = winner_count(clean_wikitext(line[2:]))
        if count and is_major_competition(*current_competition):
            add_honour(counts, display_names, current_competition[0], count)

    return [Honour(display_names[key], counts[key]) for key in sorted(counts, key=lambda item: display_names[item].casefold())]


def can_verify_zero(wikitext: str) -> bool:
    """Return true only when winner records were parsed and all were non-major."""

    current_competition: tuple[str, str] | None = None
    parsed_wins = 0
    for raw_line in str(wikitext or "").splitlines():
        line = raw_line.strip()
        if line.startswith("*") and not line.startswith("**"):
            cleaned = clean_wikitext(line[1:])
            title = competition_title(cleaned)
            current_competition = (title, cleaned) if title else None
            if current_competition and winner_count(cleaned):
                parsed_wins += 1
                if is_major_competition(*current_competition):
                    return False
            continue
        if line.startswith("**") and current_competition and WIN_LABEL.search(line):
            if re.search(r"runners?-up|finalists?|semi-final", line, re.IGNORECASE):
                continue
            if winner_count(clean_wikitext(line[2:])):
                parsed_wins += 1
                if is_major_competition(*current_competition):
                    return False
    return parsed_wins > 0


def is_major_competition(title: str, context: str) -> bool:
    value = f"{title} {context}"
    if LOWER_OR_MINOR.search(value):
        return False
    return bool(EUROPEAN_OR_WORLD.search(value) or DOMESTIC_CUP.search(value) or TOP_FLIGHT.search(value))


def winner_count(value: str) -> int | None:
    if not WIN_LABEL.search(value):
        return None
    match = COUNT_LABEL.search(value)
    return int(match.group(1)) if match else 1


def competition_title(value: str) -> str:
    clean = re.split(r"\s+\((?:level|tier)\s+\d+\)", value, maxsplit=1, flags=re.IGNORECASE)[0]
    clean = re.split(r"\s*[:–—]\s*(?:winners?|champions?)\b", clean, maxsplit=1, flags=re.IGNORECASE)[0]
    clean = re.sub(r"\s*\(\d+\)\s*$", "", clean).strip(" :–—")
    return clean


def clean_wikitext(value: str) -> str:
    clean = re.sub(r"<ref\b[^>/]*/>|<ref\b[^>]*>.*?</ref>", "", value, flags=re.IGNORECASE)
    clean = re.sub(r"\{\{[^{}]*\}\}", "", clean)
    clean = re.sub(r"\[\[(?:[^]|]+\|)?([^]]+)\]\]", r"\1", clean)
    clean = re.sub(r"'{2,}", "", clean)
    return " ".join(clean.split())


def add_honour(counts: dict[str, int], names: dict[str, str], title: str, count: int) -> None:
    key = re.sub(r"\W+", " ", title, flags=re.UNICODE).casefold().strip()
    counts[key] = max(counts.get(key, 0), count)
    names[key] = title


def normalise_heading(value: Any) -> str:
    return re.sub(r"[^a-z]+", " ", str(value or "").casefold()).strip()


def clean_url(value: Any) -> str | None:
    return str(value) if isinstance(value, str) and value.startswith(("https://", "http://")) else None
