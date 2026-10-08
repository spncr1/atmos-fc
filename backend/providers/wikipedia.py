"""Small client for sourced English Wikipedia page summaries."""

from __future__ import annotations

from typing import Any
from urllib.parse import quote

import httpx

from backend.config import get_settings
from backend.providers.errors import ProviderRequestError, ProviderResponseError


class WikipediaClient:
    """Load a concise page introduction through Wikimedia's REST API."""

    def __init__(self, timeout: float = 12.0) -> None:
        self.timeout = timeout
        self.headers = {"User-Agent": get_settings().wikimedia_user_agent}

    async def page_summary(self, title: str) -> dict[str, Any]:
        clean_title = " ".join(str(title or "").split())
        if not clean_title:
            raise ProviderResponseError("Wikipedia title is unavailable.")

        url = f"https://en.wikipedia.org/api/rest_v1/page/summary/{quote(clean_title, safe='')}"
        try:
            async with httpx.AsyncClient(timeout=self.timeout, headers=self.headers) as client:
                response = await client.get(url)
        except httpx.HTTPError as exc:
            raise ProviderRequestError(f"Wikipedia request failed: {exc}") from exc

        if response.status_code >= 400:
            raise ProviderResponseError(f"Wikipedia returned HTTP {response.status_code}.")
        try:
            payload = response.json()
        except ValueError as exc:
            raise ProviderResponseError("Wikipedia returned invalid JSON.") from exc
        if not isinstance(payload, dict):
            raise ProviderResponseError("Wikipedia summary response was not an object.")
        return payload

    async def page_sections(self, title: str) -> list[dict[str, Any]]:
        """Return the section index for one English Wikipedia article."""

        payload = await self._parse(title, prop="sections")
        sections = payload.get("sections")
        if not isinstance(sections, list):
            raise ProviderResponseError("Wikipedia sections response was not a list.")
        return [section for section in sections if isinstance(section, dict)]

    async def section_wikitext(self, title: str, section_index: str) -> str:
        """Return source wikitext for one article section."""

        payload = await self._parse(title, prop="wikitext", section=section_index)
        wikitext = payload.get("wikitext")
        if not isinstance(wikitext, str):
            raise ProviderResponseError("Wikipedia section response did not include wikitext.")
        return wikitext

    async def _parse(self, title: str, *, prop: str, section: str | None = None) -> dict[str, Any]:
        clean_title = " ".join(str(title or "").split())
        if not clean_title:
            raise ProviderResponseError("Wikipedia title is unavailable.")

        params = {
            "action": "parse",
            "page": clean_title,
            "prop": prop,
            "format": "json",
            "formatversion": 2,
        }
        if section is not None:
            params["section"] = section
        try:
            async with httpx.AsyncClient(timeout=self.timeout, headers=self.headers, follow_redirects=True) as client:
                response = await client.get("https://en.wikipedia.org/w/api.php", params=params)
        except httpx.HTTPError as exc:
            raise ProviderRequestError(f"Wikipedia request failed: {exc}") from exc
        if response.status_code >= 400:
            raise ProviderResponseError(f"Wikipedia returned HTTP {response.status_code}.")
        try:
            data = response.json()
        except ValueError as exc:
            raise ProviderResponseError("Wikipedia returned invalid JSON.") from exc
        parsed = data.get("parse") if isinstance(data, dict) else None
        if not isinstance(parsed, dict):
            raise ProviderResponseError("Wikipedia parse response was unavailable.")
        return parsed
