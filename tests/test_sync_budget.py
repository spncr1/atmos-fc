import asyncio

from backend.scripts import run_fixture_sync_worker
from backend.services import sync


def test_scheduled_fixture_sync_uses_one_current_showcase_season(monkeypatch):
    calls = []

    async def fake_sync(**kwargs):
        calls.append(kwargs)
        return {"records_seen": 0, "records_changed": 0, "synced": {}}

    monkeypatch.setattr(run_fixture_sync_worker, "sync_core_football_data", fake_sync)
    monkeypatch.setattr(sync, "current_european_season_year", lambda: 2026)

    asyncio.run(run_fixture_sync_worker.run_once(50, False, 0, showcase_scope=True))

    assert calls == [{
        "recent_limit": 50,
        "include_events": False,
        "season_years": [2026],
        "competition_ids": [39, 140, 78, 135, 61, 88, 94, 2],
    }]
