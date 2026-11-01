"""Create a bounded batch of recent, publishable match analyses."""

from __future__ import annotations

import argparse
import asyncio
import json
from datetime import datetime, timedelta, timezone
from pathlib import Path

from fastapi import HTTPException
from sqlalchemy import func, select
from sqlalchemy.orm import joinedload

from backend.config import get_settings
from backend.database.models import Competition, Fixture
from backend.database.session import get_sessionmaker
from backend.models.schemas import AnalyseRequest
from backend.repositories.football_data import FINISHED_STATUS_CODES, PROVIDER
from backend.scripts.check_analysis_freshness import parse_date
from backend.scripts.export_showcase import ANALYSIS_VERSION, REFRESHED_CODES
from backend.services.matches import SUPPORTED_COMPETITIONS
from backend.services.public_analysis import timeline_is_complete

CURRENT_COMPETITION_IDS = {
    int(row["provider_id"])
    for row in SUPPORTED_COMPETITIONS
    if row["code"] in REFRESHED_CODES
}
MIN_ANALYSIS_AGE = timedelta(hours=26)
RETRY_STATUSES = {"rate_limited", "unavailable", "no_comments", "failed"}
SOURCE_MAX_LAG = timedelta(days=3)


def should_attempt(fixture: Fixture, now: datetime, lookback: timedelta) -> bool:
    kickoff = fixture.kickoff_at.astimezone(timezone.utc)
    age = now - kickoff
    if age < MIN_ANALYSIS_AGE or age > lookback:
        return False
    cache = fixture.analysis_cache
    if cache and cache.status == "complete" and isinstance(cache.payload, dict):
        meta = cache.payload.get("meta")
        if isinstance(meta, dict) and meta.get("analysis_version") == ANALYSIS_VERSION:
            status = fixture.event_sync_status
            if status and status.status == "unavailable":
                return False
            match = {
                "score": f"{fixture.home_goals}-{fixture.away_goals}",
                "event_feed_status": status.status if status else "unchecked",
            }
            if timeline_is_complete(match, fixture.events):
                return False
    comments = fixture.youtube_comment_cache
    if comments and comments.checked_at:
        retry_after = timedelta(hours=12 if comments.status == "rate_limited" else 24)
        if comments.status in RETRY_STATUSES:
            if now - comments.checked_at.astimezone(timezone.utc) < retry_after:
                return False
    return True


async def candidate_ids(limit: int, lookback: timedelta, now: datetime) -> list[int]:
    sessionmaker = get_sessionmaker()
    async with sessionmaker() as session:
        query = (
            select(Fixture)
            .join(Competition, Competition.id == Fixture.competition_id)
            .options(
                joinedload(Fixture.analysis_cache),
                joinedload(Fixture.youtube_comment_cache),
                joinedload(Fixture.event_sync_status),
                joinedload(Fixture.events),
            )
            .where(
                Fixture.provider == PROVIDER,
                Fixture.status_short.in_(FINISHED_STATUS_CODES),
                Fixture.kickoff_at >= now - lookback,
                Fixture.kickoff_at <= now - MIN_ANALYSIS_AGE,
                Competition.provider_competition_id.in_(CURRENT_COMPETITION_IDS),
            )
            .order_by(Fixture.kickoff_at.desc())
            .limit(200)
        )
        fixtures = list((await session.scalars(query)).unique())
    eligible = [row for row in fixtures if should_attempt(row, now, lookback)]
    fresh = [
        row.provider_fixture_id for row in eligible
        if not row.youtube_comment_cache or row.youtube_comment_cache.status not in RETRY_STATUSES
    ]
    retries = [
        row.provider_fixture_id for row in eligible
        if row.youtube_comment_cache and row.youtube_comment_cache.status in RETRY_STATUSES
    ]
    return (fresh + retries)[:limit]


def source_is_stale(latest_result: datetime | None, latest_source: datetime | None, now: datetime) -> bool:
    if latest_result is None or now - latest_result > timedelta(days=3):
        return False
    return latest_source is None or latest_result - latest_source > SOURCE_MAX_LAG


async def check_source_freshness(now: datetime) -> None:
    recent = json.loads(Path("frontend/data/current/recent.json").read_text()).get("matches") or []
    result_dates = [date for row in recent if (date := parse_date(row.get("date")))]
    latest_result = max(result_dates, default=None)
    sessionmaker = get_sessionmaker()
    async with sessionmaker() as session:
        latest_source = await session.scalar(
            select(func.max(Fixture.kickoff_at))
            .join(Competition, Competition.id == Fixture.competition_id)
            .where(
                Fixture.provider == PROVIDER,
                Fixture.status_short.in_(FINISHED_STATUS_CODES),
                Competition.provider_competition_id.in_(CURRENT_COMPETITION_IDS),
            )
        )
    if latest_source is not None:
        latest_source = latest_source.astimezone(timezone.utc)
    if source_is_stale(latest_result, latest_source, now):
        raise RuntimeError("Recent results exist, but API-Football fixtures are over three days behind. Check the Railway fixture sync and provider account.")


async def refresh(limit: int, lookback_days: int) -> dict[str, int]:
    settings = get_settings()
    missing = [name for name, value in (
        ("DATABASE_URL", settings.database_url),
        ("API_FOOTBALL_API_KEY", settings.api_football_api_key),
        ("YOUTUBE_API_KEY", settings.youtube_api_key),
    ) if not value]
    if missing:
        raise RuntimeError(f"Missing required analysis configuration: {', '.join(missing)}")

    now = datetime.now(timezone.utc)
    await check_source_freshness(now)
    from backend.main import analyse_match

    ids = await candidate_ids(limit, timedelta(days=lookback_days), now)
    result = {"eligible_selected": len(ids), "publishable": 0, "unavailable": 0, "failed": 0, "rate_limited": 0}
    for fixture_id in ids:
        try:
            analysis = await analyse_match(AnalyseRequest(match_id=str(fixture_id)))
        except HTTPException as exc:
            print(json.dumps({"fixture_id": fixture_id, "result": "failed", "status_code": exc.status_code}))
            result["failed"] += 1
            continue
        except Exception as exc:
            print(json.dumps({"fixture_id": fixture_id, "result": "failed", "error_type": type(exc).__name__}))
            result["failed"] += 1
            continue
        if analysis.match.youtube_comment_status == "rate_limited":
            result["rate_limited"] += 1
            break
        if analysis.meta.analysis_mode == "youtube_unavailable" or not timeline_is_complete(analysis.match, analysis.events):
            result["unavailable"] += 1
        else:
            result["publishable"] += 1
    return result


def main() -> None:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--limit", type=int, default=5)
    parser.add_argument("--lookback-days", type=int, default=7)
    args = parser.parse_args()
    if args.limit < 1 or args.lookback_days < 2:
        parser.error("--limit must be positive and --lookback-days must be at least 2")
    result = asyncio.run(refresh(args.limit, args.lookback_days))
    print(json.dumps(result, sort_keys=True))
    if result["rate_limited"] or (result["failed"] and not result["publishable"]):
        raise SystemExit("Analysis batch could not complete; the previous published bundle is retained.")


if __name__ == "__main__":
    main()
