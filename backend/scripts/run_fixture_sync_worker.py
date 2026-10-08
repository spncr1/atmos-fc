"""Continuously sync recent API-Football fixtures into Postgres."""

from __future__ import annotations

import argparse
import asyncio
import json
import os
from datetime import datetime, timedelta, timezone

from backend.services.background_jobs import JOB_TEAM_HONOURS, process_queued_jobs
from backend.services.sync import sync_core_football_data


DEFAULT_INTERVAL_SECONDS = 1800
DEFAULT_RECENT_LIMIT = 30
DEFAULT_HONOURS_JOB_LIMIT = 5


def env_int(name: str, default: int) -> int:
    raw = os.getenv(name)
    if raw is None or raw.strip() == "":
        return default
    return int(raw)


def env_bool(name: str, default: bool = False) -> bool:
    raw = os.getenv(name)
    if raw is None or raw.strip() == "":
        return default
    return raw.strip().lower() in {"1", "true", "yes", "on"}


def parse_args() -> argparse.Namespace:
    parser = argparse.ArgumentParser(description="Run the API-Football fixture sync worker.")
    parser.add_argument(
        "--interval-seconds",
        type=int,
        default=env_int("FIXTURE_SYNC_INTERVAL_SECONDS", DEFAULT_INTERVAL_SECONDS),
        help="Seconds to wait between sync runs.",
    )
    parser.add_argument(
        "--recent-limit",
        type=int,
        default=env_int("FIXTURE_SYNC_RECENT_LIMIT", DEFAULT_RECENT_LIMIT),
        help="Recent fixtures to sync per supported competition/season.",
    )
    parser.add_argument(
        "--include-events",
        action="store_true",
        default=env_bool("FIXTURE_SYNC_INCLUDE_EVENTS", False),
        help="Also sync fixture events. Leave off for fast homepage freshness.",
    )
    parser.add_argument(
        "--once",
        action="store_true",
        help="Run one sync and exit.",
    )
    parser.add_argument(
        "--honours-job-limit",
        type=int,
        default=env_int("TEAM_HONOURS_SYNC_LIMIT", DEFAULT_HONOURS_JOB_LIMIT),
        help="Maximum queued club-honours jobs to process after each fixture sync.",
    )
    return parser.parse_args()


async def run_once(recent_limit: int, include_events: bool, honours_job_limit: int) -> None:
    started_at = datetime.now(timezone.utc).isoformat()
    print(
        json.dumps(
            {
                "message": "fixture_sync_started",
                "started_at": started_at,
                "recent_limit": recent_limit,
                "include_events": include_events,
            }
        ),
        flush=True,
    )
    result = await sync_core_football_data(
        recent_limit=recent_limit,
        include_events=include_events,
    )
    honours = await process_queued_jobs(
        limit=max(0, honours_job_limit),
        job_types=[JOB_TEAM_HONOURS],
    ) if honours_job_limit > 0 else {"seen": 0, "complete": 0, "failed": 0}
    print(
        json.dumps(
            {
                "message": "fixture_sync_finished",
                "finished_at": datetime.now(timezone.utc).isoformat(),
                "result": result,
                "team_honours": honours,
            },
            default=str,
        ),
        flush=True,
    )


async def main() -> None:
    args = parse_args()
    interval_seconds = max(60, args.interval_seconds)
    print(
        json.dumps(
            {
                "message": "fixture_sync_worker_started",
                "interval_seconds": interval_seconds,
                "recent_limit": args.recent_limit,
                "include_events": args.include_events,
                "once": args.once,
                "honours_job_limit": args.honours_job_limit,
            }
        ),
        flush=True,
    )

    while True:
        try:
            await run_once(args.recent_limit, args.include_events, args.honours_job_limit)
        except Exception as exc:  # noqa: BLE001 - worker must keep running after transient provider errors.
            print(
                json.dumps(
                    {
                        "message": "fixture_sync_failed",
                        "failed_at": datetime.now(timezone.utc).isoformat(),
                        "error": str(exc),
                    }
                ),
                flush=True,
            )

        if args.once:
            return
        print(
            json.dumps(
                {
                    "message": "fixture_sync_worker_sleeping",
                    "interval_seconds": interval_seconds,
                    "next_run_at": (datetime.now(timezone.utc) + timedelta(seconds=interval_seconds)).isoformat(),
                }
            ),
            flush=True,
        )
        await asyncio.sleep(interval_seconds)


if __name__ == "__main__":
    asyncio.run(main())
