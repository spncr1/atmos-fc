"""API-Football client.

This module is the only place that should know API-Football's endpoint names,
headers, and raw response envelope.
"""

from __future__ import annotations

from typing import Any

import httpx

from backend.config import get_settings
from backend.providers.errors import ProviderConfigError, ProviderRequestError, ProviderResponseError


class ApiFootballClient:
    """Small async client for API-Football's v3 API."""

    def __init__(
        self,
        api_key: str | None = None,
        base_url: str | None = None,
        timeout: float = 20.0,
    ) -> None:
        settings = get_settings()
        self.api_key = api_key or settings.api_football_api_key
        self.base_url = (base_url or settings.api_football_base_url).rstrip("/")
        self.timeout = timeout
        if not self.api_key:
            raise ProviderConfigError("API_FOOTBALL_API_KEY is not configured.")

    async def status(self) -> dict[str, Any]:
        """Return API account status and quota information."""

        return await self.get("/status")

    async def seasons(self) -> list[int]:
        """Return available API-Football season years."""

        data = await self.get("/leagues/seasons")
        seasons = data.get("response", [])
        if not isinstance(seasons, list):
            raise ProviderResponseError("API-Football seasons response was not a list.")
        return [int(season) for season in seasons]

    async def leagues(
        self,
        *,
        league_id: int | None = None,
        season: int | None = None,
        country: str | None = None,
        search: str | None = None,
        current: bool | None = None,
        league_type: str | None = None,
    ) -> list[dict[str, Any]]:
        """Return leagues/cups matching the supplied filters."""

        return await self.response_list(
            "/leagues",
            {
                "id": league_id,
                "season": season,
                "country": country,
                "search": search,
                "current": _bool_param(current),
                "type": league_type,
            },
        )

    async def teams(
        self,
        *,
        team_id: int | None = None,
        league_id: int | None = None,
        season: int | None = None,
        search: str | None = None,
        country: str | None = None,
    ) -> list[dict[str, Any]]:
        """Return teams matching the supplied filters."""

        return await self.response_list(
            "/teams",
            {
                "id": team_id,
                "league": league_id,
                "season": season,
                "search": search,
                "country": country,
            },
        )

    async def squad(self, team_id: int) -> list[dict[str, Any]]:
        """Return the current squad for one team."""

        return await self.response_list(
            "/players/squads",
            {"team": team_id},
        )

    async def fixtures(
        self,
        *,
        fixture_id: int | None = None,
        fixture_ids: list[int] | None = None,
        league_id: int | None = None,
        season: int | None = None,
        team_id: int | None = None,
        date: str | None = None,
        date_from: str | None = None,
        date_to: str | None = None,
        status: str | None = None,
        last: int | None = None,
        next: int | None = None,
    ) -> list[dict[str, Any]]:
        """Return fixtures matching the supplied filters."""

        return await self.response_list(
            "/fixtures",
            {
                "id": fixture_id,
                "ids": _dash_join(fixture_ids),
                "league": league_id,
                "season": season,
                "team": team_id,
                "date": date,
                "from": date_from,
                "to": date_to,
                "status": status,
                "last": last,
                "next": next,
            },
        )

    async def fixture_events(
        self,
        fixture_id: int,
        *,
        team_id: int | None = None,
        player_id: int | None = None,
        event_type: str | None = None,
    ) -> list[dict[str, Any]]:
        """Return event timeline data for one fixture."""

        return await self.response_list(
            "/fixtures/events",
            {
                "fixture": fixture_id,
                "team": team_id,
                "player": player_id,
                "type": event_type,
            },
        )

    async def response_list(self, path: str, params: dict[str, Any] | None = None) -> list[dict[str, Any]]:
        """Return the response array from an API-Football endpoint."""

        data = await self.get(path, params)
        response = data.get("response", [])
        if not isinstance(response, list):
            raise ProviderResponseError(f"API-Football {path} response was not a list.")
        return response

    async def get(self, path: str, params: dict[str, Any] | None = None) -> dict[str, Any]:
        """Request one API-Football endpoint and return its JSON payload."""

        clean_params = {
            key: value
            for key, value in (params or {}).items()
            if value is not None and value != ""
        }
        headers = {"x-apisports-key": self.api_key}
        try:
            async with httpx.AsyncClient(base_url=self.base_url, timeout=self.timeout, headers=headers) as client:
                response = await client.get(path, params=clean_params)
        except httpx.HTTPError as exc:
            raise ProviderRequestError(f"API-Football request failed: {exc}") from exc

        if response.status_code >= 400:
            raise ProviderResponseError(f"API-Football returned HTTP {response.status_code}: {_trim(response.text)}")

        try:
            data = response.json()
        except ValueError as exc:
            raise ProviderResponseError("API-Football returned invalid JSON.") from exc

        errors = data.get("errors")
        if _has_errors(errors):
            raise ProviderResponseError(f"API-Football returned errors: {_trim(str(errors))}")

        return data


def _bool_param(value: bool | None) -> str | None:
    if value is None:
        return None
    return "true" if value else "false"


def _dash_join(values: list[int] | None) -> str | None:
    if not values:
        return None
    return "-".join(str(value) for value in values)


def _has_errors(value: Any) -> bool:
    if value is None:
        return False
    if isinstance(value, list):
        return bool(value)
    if isinstance(value, dict):
        return bool(value)
    if isinstance(value, str):
        return bool(value.strip())
    return True


def _trim(value: str, limit: int = 500) -> str:
    clean = " ".join(value.split())
    return clean if len(clean) <= limit else f"{clean[: limit - 1]}..."
