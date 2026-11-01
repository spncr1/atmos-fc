import asyncio
from types import SimpleNamespace

from backend.providers.api_football import ApiFootballClient


def test_scheduled_client_spaces_provider_requests(monkeypatch):
    request_times = []

    class FakeHttpClient:
        def __init__(self, **kwargs):
            pass

        async def __aenter__(self):
            return self

        async def __aexit__(self, *args):
            pass

        async def get(self, path, params):
            request_times.append(asyncio.get_running_loop().time())
            return SimpleNamespace(status_code=200, json=lambda: {"errors": [], "response": {}})

    monkeypatch.setattr("backend.providers.api_football.httpx.AsyncClient", FakeHttpClient)

    async def run():
        client = ApiFootballClient(api_key="test", min_interval_seconds=0.03)
        await client.status()
        await client.status()

    asyncio.run(run())
    assert len(request_times) == 2
    assert request_times[1] - request_times[0] >= 0.025
