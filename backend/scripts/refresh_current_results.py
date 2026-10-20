"""Refresh the free current-season result pack without mutating the database."""

from __future__ import annotations

import argparse
import asyncio
import hashlib
import json
import os
import re
import tempfile
import unicodedata
from datetime import datetime, timezone
from pathlib import Path
from typing import Any

import httpx
from dotenv import load_dotenv

from backend.models.schemas import MatchSummary

FEED_CODES = {"PL": "PL", "PD": "PD", "BL1": "BL1", "SA": "SA", "FL1": "FL1", "NED1": "DED", "POR1": "PPL", "CL": "CL"}
COMPETITION_NAMES = {"PL": "Premier League", "PD": "La Liga", "BL1": "Bundesliga", "SA": "Serie A", "FL1": "Ligue 1", "NED1": "Eredivisie", "POR1": "Liga Portugal", "CL": "UEFA Champions League"}
COMPETITION_IDS = {"PL": 39, "PD": 140, "BL1": 78, "SA": 135, "FL1": 61, "NED1": 88, "POR1": 94, "CL": 2}
API_ROOT = "https://api.football-data.org/v4"
ARCHIVE_NAME_ALIASES = {
    "psv": "psveindhoven",
    "nec": "necnijmegen",
    "az": "azalkmaar",
    "como1907": "como",
    "racingclubdelens": "lens",
    "olympiquelyonnais": "lyon",
    "staderennaisfc1901": "rennes",
    "fkbodglimt": "bodoglimt",
    "paeaek": "aekathensfc",
    "sabahfk": "sabahfa",
}


def read_json(path: Path, fallback: Any) -> Any:
    try:
        return json.loads(path.read_text())
    except (FileNotFoundError, json.JSONDecodeError):
        return fallback


def normalise_name(value: str) -> str:
    ascii_value = unicodedata.normalize("NFKD", str(value)).encode("ascii", "ignore").decode("ascii")
    return re.sub(r"[^a-z0-9]+", "", ascii_value.casefold().replace("ß", "ss"))


def simplified_name(value: str) -> str:
    words = re.findall(r"[a-z0-9]+", unicodedata.normalize("NFKD", str(value)).encode("ascii", "ignore").decode("ascii").casefold())
    ignored = {"fc", "cf", "ac", "sc", "afc", "ss", "us", "sv", "tsg", "gd", "sl", "club", "football", "futbol", "calcio", "de", "do", "da", "e"}
    return "".join(word for word in words if word not in ignored)


def resolve_archive_key(value: str, by_name: dict[str, str]) -> str | None:
    normalised = normalise_name(value)
    exact = by_name.get(normalised) or by_name.get(ARCHIVE_NAME_ALIASES.get(normalised, ""))
    if exact:
        return exact
    simplified = simplified_name(value)
    if len(simplified) < 5:
        return None
    candidates = {key for name, key in by_name.items() if simplified == simplified_name(name) or (len(simplified_name(name)) >= 5 and (simplified in simplified_name(name) or simplified_name(name) in simplified))}
    return next(iter(candidates)) if len(candidates) == 1 else None


def season_label(year: int) -> str:
    return f"{year}/{str(year + 1)[-2:]}"


def score(match: dict[str, Any]) -> str | None:
    full_time = (match.get("score") or {}).get("fullTime") or {}
    home, away = full_time.get("home"), full_time.get("away")
    return f"{home}-{away}" if isinstance(home, int) and isinstance(away, int) else None


def identity_key(code: str, season: int, utc_date: str, home_key: str, away_key: str) -> str:
    kickoff = datetime.fromisoformat(utc_date.replace("Z", "+00:00")).astimezone(timezone.utc).strftime("%Y-%m-%dT%H:%MZ")
    return "|".join((code, str(season), kickoff, home_key, away_key))


def baseline_team_maps(showcase: Path) -> tuple[dict[str, str], dict[str, dict[str, Any]]]:
    payload = read_json(showcase / "teams.json", {"teams": {}})
    by_name: dict[str, str] = {}
    identities: dict[str, dict[str, Any]] = {}
    for key, row in (payload.get("teams") or {}).items():
        profiles = row.get("profiles") or {}
        latest = profiles[sorted(profiles, reverse=True)[0]] if profiles else {}
        name = latest.get("name") or ""
        if name:
            by_name[normalise_name(name)] = str(key)
        identities[str(key)] = {"team_key": str(key), "name": name, "code": latest.get("code"), "crest": latest.get("logo_url"), "country": latest.get("country_name"), "provider_team_id": latest.get("provider_team_id")}
    return by_name, identities


def team_identity(raw: dict[str, Any], by_name: dict[str, str], identities: dict[str, dict[str, Any]]) -> tuple[str, dict[str, Any]]:
    name = str(raw.get("name") or raw.get("shortName") or "Unknown club")
    mapped = resolve_archive_key(name, by_name) or resolve_archive_key(raw.get("shortName") or "", by_name)
    key = mapped or f"fd-{int(raw['id'])}"
    identity = {
        "team_key": key,
        "name": name,
        "code": raw.get("tla"),
        "crest": raw.get("crest"),
        "country": None,
        "provider_team_id": identities.get(key, {}).get("provider_team_id"),
    }
    identities[key] = {**identities.get(key, {}), **{k: v for k, v in identity.items() if v is not None}}
    return key, identities[key]


def normalise_match(raw: dict[str, Any], code: str, season: int, by_name: dict[str, str], identities: dict[str, dict[str, Any]]) -> dict[str, Any]:
    home_key, home = team_identity(raw["homeTeam"], by_name, identities)
    away_key, away = team_identity(raw["awayTeam"], by_name, identities)
    utc_date = str(raw["utcDate"])
    result = MatchSummary(
        id=f"fd:{raw['id']}",
        home=home["name"], away=away["name"],
        home_team_id=home.get("provider_team_id"), away_team_id=away.get("provider_team_id"),
        home_team_key=home_key, away_team_key=away_key,
        home_short_name=(raw["homeTeam"].get("shortName") or home["name"]),
        away_short_name=(raw["awayTeam"].get("shortName") or away["name"]),
        home_tla=home.get("code"), away_tla=away.get("code"),
        home_crest=home.get("crest"), away_crest=away.get("crest"),
        score=score(raw), competition=COMPETITION_NAMES[code], competition_code=code,
        competition_id=COMPETITION_IDS[code], date=utc_date, round=raw.get("stage") or (f"Matchday {raw['matchday']}" if raw.get("matchday") else None),
        season=season_label(season), season_year=season, status="FINISHED", analysis_available=False,
    ).model_dump(mode="json")
    result["canonical_fixture_key"] = identity_key(code, season, utc_date, home_key, away_key)
    return result


async def fetch_competition(client: httpx.AsyncClient, feed_code: str, season: int) -> list[dict[str, Any]]:
    response = await client.get(f"{API_ROOT}/competitions/{feed_code}/matches", params={"status": "FINISHED", "season": season})
    response.raise_for_status()
    payload = response.json()
    rows = payload.get("matches")
    if not isinstance(rows, list):
        raise ValueError("Current result response did not contain a match list")
    return rows


async def refresh(output: Path, showcase: Path, season: int, api_key: str) -> dict[str, Any]:
    previous = read_json(output / "current-season.json", {"matches": [], "teams": {}, "competitions": {}})
    by_name, identities = baseline_team_maps(showcase)
    identities.update(previous.get("teams") or {})
    merged = {}
    previous_by_id = {str(row.get("id")): row for row in previous.get("matches") or [] if row.get("id")}
    for row in previous_by_id.values():
        home_key = resolve_archive_key(row.get("home") or "", by_name) or str(row.get("home_team_key") or row.get("home_team_id") or "")
        away_key = resolve_archive_key(row.get("away") or "", by_name) or str(row.get("away_team_key") or row.get("away_team_id") or "")
        if not home_key or not away_key or not row.get("date"):
            continue
        row = {**row, "home_team_key": home_key, "away_team_key": away_key}
        row["canonical_fixture_key"] = identity_key(str(row.get("competition_code")), int(row.get("season_year")), str(row["date"]), home_key, away_key)
        merged[row["canonical_fixture_key"]] = row
    statuses = dict(previous.get("competitions") or {})
    now = datetime.now(timezone.utc).isoformat()
    successful = 0
    async with httpx.AsyncClient(headers={"X-Auth-Token": api_key}, timeout=30) as client:
        for code, feed_code in FEED_CODES.items():
            try:
                raw_matches = await fetch_competition(client, feed_code, season)
                normalised = [normalise_match(row, code, season, by_name, identities) for row in raw_matches]
                if not normalised and any(row.get("competition_code") == code for row in merged.values()):
                    raise ValueError("Empty response would remove previously valid coverage")
                for row in normalised:
                    merged[row["canonical_fixture_key"]] = row
                statuses[code] = {"status": "fresh", "updated_at": now, "finished_match_count": len(normalised)}
                successful += 1
            except (httpx.HTTPError, KeyError, TypeError, ValueError) as exc:
                prior = statuses.get(code) or {}
                statuses[code] = {**prior, "status": "retained", "last_attempt_at": now}
                print(f"{code}: retained previous data ({exc})")
    if successful == 0:
        raise RuntimeError("No competition refreshed successfully; nothing will be published")

    matches_by_id = {str(row["id"]): row for row in merged.values()}
    matches = sorted(matches_by_id.values(), key=lambda row: row["date"], reverse=True)
    for row in matches:
        clean = dict(row)
        clean.pop("canonical_fixture_key", None)
        MatchSummary.model_validate(clean)
    payload = {"schema_version": 1, "season_year": season, "generated_at": now, "matches": matches, "teams": identities, "competitions": statuses}
    checksum = hashlib.sha256(json.dumps(payload, ensure_ascii=False, sort_keys=True, separators=(",", ":")).encode("utf-8")).hexdigest()
    payload["integrity"] = {"algorithm": "sha256", "pack_checksum": checksum}
    recent = {"generated_at": now, "matches": matches[:18], "competition_status": statuses}
    publish(output, {"current-season.json": payload, "recent.json": recent})
    return {"updated_at": now, "successful_competitions": successful, "fixture_count": len(matches), "checksum": checksum}


def publish(output: Path, documents: dict[str, Any]) -> None:
    output.mkdir(parents=True, exist_ok=True)
    temporary = Path(tempfile.mkdtemp(prefix=".current-", dir=output.parent))
    try:
        for name, value in documents.items():
            (temporary / name).write_text(json.dumps(value, ensure_ascii=False, separators=(",", ":"), sort_keys=True) + "\n")
        for name in documents:
            os.replace(temporary / name, output / name)
    finally:
        temporary.rmdir()


def default_season_year() -> int:
    now = datetime.now(timezone.utc)
    return now.year if now.month >= 7 else now.year - 1


def main() -> None:
    load_dotenv("backend/.env")
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--output", type=Path, default=Path("frontend/data/current"))
    parser.add_argument("--showcase", type=Path, default=Path("frontend/data/showcase"))
    parser.add_argument("--season", type=int, default=default_season_year())
    args = parser.parse_args()
    api_key = os.getenv("FOOTBALL_DATA_API_KEY", "").strip()
    if not api_key:
        raise SystemExit("FOOTBALL_DATA_API_KEY is required")
    result = asyncio.run(refresh(args.output.resolve(), args.showcase.resolve(), args.season, api_key))
    print(json.dumps(result, indent=2))


if __name__ == "__main__":
    main()
