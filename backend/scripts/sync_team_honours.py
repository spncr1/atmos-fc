"""Queue or process major-honours enrichment for all clubs Atmos FC represents."""

from __future__ import annotations

import argparse
import asyncio

from backend.database.session import get_sessionmaker
from backend.repositories import football_data as repo
from backend.services.background_jobs import JOB_TEAM_HONOURS, process_queued_jobs
from backend.services.matches import SUPPORTED_COMPETITIONS


def parse_args() -> argparse.Namespace:
    parser = argparse.ArgumentParser(description="Backfill structured club honours.")
    parser.add_argument("--process", action="store_true", help="Process queued honours jobs after queuing them.")
    parser.add_argument("--limit", type=int, default=25, help="Maximum jobs to process in this run.")
    return parser.parse_args()


async def main() -> None:
    args = parse_args()
    competition_ids = {int(row["provider_id"]) for row in SUPPORTED_COMPETITIONS}
    sessionmaker = get_sessionmaker()
    async with sessionmaker() as session:
        teams = await repo.teams_represented_in_competitions(session, competition_ids)
        await repo.queue_team_honours_jobs(session, teams)
        await session.commit()
    result = {"clubs_discovered": len(teams), "jobs_processed": 0, "jobs_complete": 0, "jobs_failed": 0}
    if args.process:
        stats = await process_queued_jobs(limit=max(1, args.limit), job_types=[JOB_TEAM_HONOURS])
        result.update(
            jobs_processed=stats["seen"],
            jobs_complete=stats["complete"],
            jobs_failed=stats["failed"],
        )
    print(result)


if __name__ == "__main__":
    asyncio.run(main())
