"""Fail when recent results are available but published analyses are stale."""

from __future__ import annotations

import json
from datetime import datetime, timedelta, timezone
from pathlib import Path


def parse_date(value: str | None) -> datetime | None:
    if not value:
        return None
    return datetime.fromisoformat(value.replace("Z", "+00:00")).astimezone(timezone.utc)


def stale_analysis(recent_matches: list[dict], analyses: dict, now: datetime) -> bool:
    recent_dates = [date for row in recent_matches if (date := parse_date(row.get("date")))]
    if not recent_dates or now - max(recent_dates) > timedelta(days=3):
        return False
    analysis_dates = [date for row in analyses.values() if (date := parse_date((row.get("match") or {}).get("date")))]
    return not analysis_dates or now - max(analysis_dates) > timedelta(days=7)


def main() -> None:
    recent = json.loads(Path("frontend/data/current/recent.json").read_text()).get("matches") or []
    analyses = json.loads(Path("frontend/data/showcase/analyses.json").read_text()).get("analyses") or {}
    if stale_analysis(recent, analyses, datetime.now(timezone.utc)):
        raise SystemExit("Recent results exist, but the newest published match analysis is over seven days old.")
    print("Published match-analysis freshness check passed.")


if __name__ == "__main__":
    main()
