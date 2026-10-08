"""Railway process entrypoint for Atmos FC services."""

from __future__ import annotations

import argparse
import asyncio
import json
import os
import subprocess
import sys
from datetime import datetime, timezone


PROCESS_ALIASES = {
    "web": "web",
    "api": "web",
    "fixture_sync_worker": "fixture_sync_worker",
    "fixture-sync-worker": "fixture_sync_worker",
    "fixtures": "fixture_sync_worker",
    "fixture_sync_once": "fixture_sync_once",
    "fixture-sync-once": "fixture_sync_once",
    "fixture_sync_cron": "fixture_sync_once",
    "fixture-sync-cron": "fixture_sync_once",
    "fixture_event_sync_once": "fixture_event_sync_once",
    "fixture-event-sync-once": "fixture_event_sync_once",
    "fixture_events_sync_once": "fixture_event_sync_once",
    "fixture-events-sync-once": "fixture_event_sync_once",
    "fixture_event_sync_cron": "fixture_event_sync_once",
    "fixture-event-sync-cron": "fixture_event_sync_once",
    "team_honours_once": "team_honours_once",
    "team-honours-once": "team_honours_once",
    "team_honours_cron": "team_honours_once",
    "team-honours-cron": "team_honours_once",
}


def parse_args() -> argparse.Namespace:
    parser = argparse.ArgumentParser(description="Start an Atmos FC Railway process.")
    parser.add_argument(
        "--process-type",
        default=os.getenv("ATMOS_PROCESS_TYPE", "web"),
        help="Process to run. Defaults to ATMOS_PROCESS_TYPE or web.",
    )
    return parser.parse_args()


def run_migrations() -> None:
    subprocess.run(
        [sys.executable, "-m", "alembic", "upgrade", "head"],
        check=True,
    )


def exec_module(module: str, *args: str) -> None:
    os.execvp(sys.executable, [sys.executable, "-m", module, *args])


def run_fixture_event_sync_once() -> None:
    from backend.services.sync import sync_recent_fixture_events

    limit = int(os.getenv("FIXTURE_EVENT_SYNC_LIMIT", "50"))

    async def runner() -> None:
        print(
            json.dumps(
                {
                    "message": "fixture_event_sync_started",
                    "started_at": datetime.now(timezone.utc).isoformat(),
                    "limit": limit,
                }
            ),
            flush=True,
        )
        result = await sync_recent_fixture_events(limit=limit)
        print(
            json.dumps(
                {
                    "message": "fixture_event_sync_finished",
                    "finished_at": datetime.now(timezone.utc).isoformat(),
                    "result": result,
                },
                default=str,
            ),
            flush=True,
        )

    asyncio.run(runner())


def main() -> None:
    args = parse_args()
    raw_process_type = args.process_type.strip().lower()
    process_type = PROCESS_ALIASES.get(raw_process_type)

    if process_type is None:
        valid = ", ".join(sorted(PROCESS_ALIASES))
        raise SystemExit(
            f"Unknown ATMOS_PROCESS_TYPE={raw_process_type!r}. Expected one of: {valid}"
        )

    run_migrations()

    if process_type == "web":
        port = os.getenv("PORT", "8000")
        exec_module(
            "uvicorn",
            "backend.main:app",
            "--host",
            "0.0.0.0",
            "--port",
            port,
        )

    if process_type == "fixture_sync_once":
        recent_limit = os.getenv("FIXTURE_SYNC_RECENT_LIMIT", "50")
        exec_module(
            "backend.scripts.run_fixture_sync_worker",
            "--once",
            "--recent-limit",
            recent_limit,
        )

    if process_type == "fixture_event_sync_once":
        run_fixture_event_sync_once()
        return

    if process_type == "team_honours_once":
        limit = os.getenv("TEAM_HONOURS_SYNC_LIMIT", "25")
        exec_module(
            "backend.scripts.sync_team_honours",
            "--process",
            "--limit",
            limit,
        )

    exec_module("backend.scripts.run_fixture_sync_worker")


if __name__ == "__main__":
    main()
