from __future__ import annotations

import unittest
from datetime import date, datetime, timezone
from types import SimpleNamespace
from unittest.mock import AsyncMock, patch

from sqlalchemy.exc import SQLAlchemyError

from backend.services.competition_profiles import (
    COMPETITION_EDITORIAL_CONTENT,
    SUPPORTED_BY_PROVIDER_ID,
    CompetitionProfileNotFoundError,
    CompetitionProfileUnavailableError,
    competition_profile,
    safe_official_url,
)


class FakeSession:
    async def __aenter__(self):
        return self

    async def __aexit__(self, exc_type, exc, traceback):
        return False


def fake_sessionmaker():
    return FakeSession()


def team(team_id: int, name: str):
    return SimpleNamespace(
        provider_team_id=team_id,
        name=name,
        logo_url=f"https://images.example/{team_id}.png",
        country_name="England",
    )


def fixture(index: int, home, away):
    return SimpleNamespace(
        provider_fixture_id=1000 + index,
        home_team=home,
        away_team=away,
        home_goals=2,
        away_goals=1,
        kickoff_at=datetime(2026, 10, max(1, 20 - index), tzinfo=timezone.utc),
    )


class CompetitionProfileTests(unittest.IsolatedAsyncioTestCase):
    @patch("backend.services.competition_profiles.fixture_to_summary")
    @patch("backend.services.competition_profiles.repo.competition_finished_fixtures", new_callable=AsyncMock)
    @patch("backend.services.competition_profiles.repo.competition_season_by_provider_and_year", new_callable=AsyncMock)
    @patch("backend.services.competition_profiles.repo.competition_by_provider_id", new_callable=AsyncMock)
    @patch("backend.services.competition_profiles.get_sessionmaker", return_value=fake_sessionmaker)
    async def test_profile_uses_requested_season_and_builds_database_snapshot(
        self,
        _get_sessionmaker,
        competition_by_provider_id,
        competition_season_by_provider_and_year,
        competition_finished_fixtures,
        fixture_to_summary,
    ):
        competition_by_provider_id.return_value = SimpleNamespace(
            name="Premier League",
            type="League",
            country_name="England",
            country_code="GB-ENG",
            logo_url="https://images.example/pl.png",
            flag_url="https://images.example/england.png",
        )
        competition_season_by_provider_and_year.return_value = SimpleNamespace(
            season=SimpleNamespace(year=2024, label="2024/25"),
            start_date=date(2024, 8, 16),
            end_date=date(2025, 5, 25),
            is_current=False,
        )
        arsenal = team(42, "Arsenal")
        chelsea = team(49, "Chelsea")
        fixtures = [
            fixture(
                index,
                arsenal if index % 2 == 0 else chelsea,
                chelsea if index % 2 == 0 else arsenal,
            )
            for index in range(8)
        ]
        competition_finished_fixtures.return_value = fixtures
        fixture_to_summary.side_effect = lambda row: {
            "id": str(row.provider_fixture_id),
            "home": row.home_team.name,
            "away": row.away_team.name,
            "competition": "Premier League",
            "competition_code": "PL",
            "date": row.kickoff_at.isoformat(),
        }

        profile = await competition_profile(39, 2024)

        self.assertEqual(profile.season_year, 2024)
        self.assertEqual(profile.season_label, "2024/25")
        self.assertEqual(profile.finished_match_count, 8)
        self.assertEqual(profile.goals_scored, 24)
        self.assertEqual(profile.club_count, 2)
        self.assertIn("since 1992", profile.summary)
        self.assertEqual(profile.wikipedia_url, "https://en.wikipedia.org/wiki/Premier_League")
        self.assertEqual([club.name for club in profile.clubs], ["Arsenal", "Chelsea"])
        self.assertEqual(len(profile.latest_matches), 6)
        self.assertEqual(profile.latest_result_at, fixtures[0].kickoff_at)
        competition_season_by_provider_and_year.assert_awaited_once_with(
            unittest.mock.ANY,
            39,
            2024,
        )

    @patch("backend.services.competition_profiles.repo.competition_finished_fixtures", new_callable=AsyncMock, return_value=[])
    @patch("backend.services.competition_profiles.repo.competition_season_by_provider_and_year", new_callable=AsyncMock, return_value=None)
    @patch("backend.services.competition_profiles.repo.competition_by_provider_id", new_callable=AsyncMock, return_value=None)
    @patch("backend.services.competition_profiles.get_sessionmaker", return_value=fake_sessionmaker)
    async def test_supported_competition_has_static_empty_fallback(
        self,
        _get_sessionmaker,
        _competition_by_provider_id,
        _competition_season,
        _fixtures,
    ):
        profile = await competition_profile(2, 2026)

        self.assertEqual(profile.name, "UEFA Champions League")
        self.assertEqual(profile.country_name, "Europe")
        self.assertEqual(profile.season_label, "2026/27")
        self.assertEqual(profile.finished_match_count, 0)
        self.assertEqual(profile.clubs, [])
        self.assertEqual(profile.data_source, "supported_definition")

    async def test_unsupported_competition_is_rejected(self):
        with self.assertRaises(CompetitionProfileNotFoundError):
            await competition_profile(999999, 2026)

    @patch("backend.services.competition_profiles.repo.competition_by_provider_id", new_callable=AsyncMock)
    @patch("backend.services.competition_profiles.get_sessionmaker", return_value=fake_sessionmaker)
    async def test_database_failure_is_reported(self, _get_sessionmaker, competition_by_provider_id):
        competition_by_provider_id.side_effect = SQLAlchemyError("database offline")

        with self.assertRaises(CompetitionProfileUnavailableError):
            await competition_profile(39, 2026)

    def test_official_urls_accept_only_http_and_https(self):
        self.assertEqual(safe_official_url("https://www.premierleague.com/"), "https://www.premierleague.com/")
        self.assertIsNone(safe_official_url("javascript:alert(1)"))
        self.assertIsNone(safe_official_url("/relative/path"))

    def test_every_supported_competition_has_reviewed_editorial_content(self):
        self.assertEqual(set(COMPETITION_EDITORIAL_CONTENT), set(SUPPORTED_BY_PROVIDER_ID))
        for content in COMPETITION_EDITORIAL_CONTENT.values():
            self.assertTrue(content["summary"].strip())
            self.assertTrue(safe_official_url(content["wikipedia_url"]))


if __name__ == "__main__":
    unittest.main()
