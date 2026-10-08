from __future__ import annotations

import unittest
from datetime import datetime, timezone
from types import SimpleNamespace
from unittest.mock import patch

from backend.services.team_profile_content import profile_content_for
from backend.services.team_honours import parse_major_honours
from backend.services.team_profile_resolver import concise_wikipedia_summary
from backend.services.team_profiles import (
    build_season_snapshot,
    latest_match_analysis,
    major_achievements,
    normalise_squad_rows,
    profile_response,
)


def team(team_id: int = 1):
    return SimpleNamespace(id=team_id, name="Example FC")


def fixture(
    fixture_id: int,
    *,
    team_id: int,
    home: bool,
    home_goals: int,
    away_goals: int,
    analysis_status: str | None = None,
):
    opponent = SimpleNamespace(id=2, name="Opposition")
    selected = team(team_id)
    cache = None
    if analysis_status:
        cache = SimpleNamespace(
            status=analysis_status,
            total_comments=48,
            payload={
                "meta": {
                    "overall_vibe": {"label": "Positive", "subtext": "Strong reaction"},
                    "crowd_energy": {"label": "High", "subtext": "Sustained activity"},
                    "peak_window": {"hour_start": 1, "hour_end": 2},
                    "total_comments": 48,
                }
            },
        )
    return SimpleNamespace(
        provider_fixture_id=fixture_id,
        home_team_id=team_id if home else 2,
        away_team_id=2 if home else team_id,
        home_team=selected if home else opponent,
        away_team=opponent if home else selected,
        home_goals=home_goals,
        away_goals=away_goals,
        analysis_cache=cache,
        kickoff_at=datetime(2026, 10, fixture_id, tzinfo=timezone.utc),
    )


class TeamProfileTests(unittest.TestCase):
    def test_squad_rows_are_groupable_and_deduplicated(self):
        players = normalise_squad_rows([
            {
                "players": [
                    {"id": 2, "name": "Midfielder", "position": "Midfielder", "number": 8},
                    {"id": 1, "name": "Keeper", "position": "Goalkeeper", "number": 1},
                    {"id": 1, "name": "Keeper", "position": "Goalkeeper", "number": 1},
                ]
            }
        ])

        self.assertEqual([player.name for player in players], ["Keeper", "Midfielder"])

    @patch("backend.services.team_profiles.fixture_to_summary", side_effect=lambda row: {"id": str(row.provider_fixture_id), "home": "A", "away": "B", "competition": "Test", "competition_code": "T", "date": row.kickoff_at.isoformat()})
    def test_snapshot_counts_from_team_perspective(self, _fixture_to_summary):
        selected = team()
        fixtures = [
            fixture(3, team_id=1, home=True, home_goals=3, away_goals=1),
            fixture(2, team_id=1, home=False, home_goals=1, away_goals=1),
            fixture(1, team_id=1, home=False, home_goals=2, away_goals=0),
        ]

        snapshot = build_season_snapshot(selected, fixtures, 2026)

        self.assertEqual(snapshot.matches_played, 3)
        self.assertEqual((snapshot.wins, snapshot.draws, snapshot.losses), (1, 1, 1))
        self.assertEqual((snapshot.goals_for, snapshot.goals_against), (4, 4))
        self.assertEqual(snapshot.form, ["W", "D", "L"])
        self.assertIn("Example FC", snapshot.overview)

    @patch("backend.services.team_profiles.fixture_to_summary", return_value={"id": "2", "home": "A", "away": "B", "competition": "Test", "competition_code": "T", "date": "2026-10-02T00:00:00+00:00"})
    def test_latest_match_analysis_requires_complete_cache_on_latest_fixture(self, _fixture_to_summary):
        fixtures = [
            fixture(3, team_id=1, home=True, home_goals=2, away_goals=0, analysis_status="failed"),
            fixture(2, team_id=1, home=True, home_goals=1, away_goals=0, analysis_status="complete"),
        ]

        analysis = latest_match_analysis(fixtures)

        self.assertIsNone(analysis)

        analysis = latest_match_analysis(fixtures[1:])

        self.assertIsNotNone(analysis)
        self.assertEqual(analysis.match.id, "2")
        self.assertEqual(analysis.total_comments, 48)

    def test_wikipedia_summary_is_bounded_to_three_sentences(self):
        summary = concise_wikipedia_summary("One. Two. Three. Four.")
        self.assertEqual(summary, "One. Two. Three.")

    def test_reviewed_club_honours_use_exact_counts_and_breakdowns(self):
        expected = {
            "Arsenal": (47, "14 FA Cup"),
            "FC Barcelona": (96, "5 UEFA Champions League"),
            "Bayern Munich": (89, "35 German championship"),
            "Inter": (48, "21 Serie A"),
            "Liverpool": (68, "20 English top-flight league"),
            "Manchester City": (38, "1 FIFA Club World Cup"),
            "Manchester United": (68, "13 FA Cup"),
            "Paris Saint-Germain": (59, "14 Ligue 1"),
            "FC Nantes": (15, "8 French league"),
            "Sheffield Utd": (5, "4 FA Cup"),
            "Real Madrid CF": (102, "15 UEFA Champions League"),
        }

        for club_name, (count, achievement) in expected.items():
            with self.subTest(club_name=club_name):
                content = profile_content_for(club_name)
                self.assertIsNotNone(content)
                self.assertEqual(content.major_trophy_count, count)
                self.assertIn(achievement, major_achievements(content))
                self.assertTrue(content.official_website.startswith("https://"))
                self.assertTrue(content.achievements_source_url.startswith("https://"))

    def test_wikipedia_honours_parser_keeps_only_major_wins(self):
        honours = parse_major_honours("""
        ===Domestic===
        '''League'''
        *[[Football League First Division|First Division]] / [[Premier League]] (level 1)
        **Champions (7): 1893–94, 1980–81
        *[[Football League Second Division|Second Division]] (level 2)
        **Champions (2): 1937–38, 1959–60
        '''Cups'''
        *[[FA Cup]]
        **Winners (7): 1886–87, 1956–57
        *[[EFL Cup|Football League Cup / EFL Cup]]
        **Winners (5): 1960–61, 1995–96
        *[[FA Community Shield|FA Charity Shield]]
        **Winners: 1981
        *[[Sheriff of London Charity Shield]]
        **Winners (2): 1899, 1901
        ===European===
        *[[UEFA Champions League|European Cup]]
        **Winners: 1981–82
        *[[UEFA Europa League]]
        **Winners: 2025–26
        *[[UEFA Super Cup|European Super Cup]]
        **Winners: 1982
        *[[UEFA Intertoto Cup]]
        **Winners: 2001
        """)

        self.assertEqual(
            {(honour.title, honour.count) for honour in honours},
            {
                ("First Division / Premier League", 7),
                ("FA Cup", 7),
                ("Football League Cup / EFL Cup", 5),
                ("FA Charity Shield", 1),
                ("European Cup", 1),
                ("UEFA Europa League", 1),
                ("European Super Cup", 1),
            },
        )

    def test_wikipedia_honours_parser_can_verify_zero(self):
        honours = parse_major_honours("""
        ==Honours==
        *Regional invitational cup
        **Winners (3): 2001, 2002, 2003
        *Second Division (level 2)
        **Champions: 1999
        """)
        self.assertEqual(honours, [])

    def test_unreviewed_club_honours_remain_explicitly_unknown(self):
        content = profile_content_for("Example FC")

        self.assertIsNone(content)
        self.assertEqual(major_achievements(content), [])

    def test_curated_profile_supplies_reviewed_official_website(self):
        selected = SimpleNamespace(
            id=83,
            provider_team_id=83,
            name="Nantes",
            code="NAN",
            country_name="France",
            founded=1943,
            is_national=False,
            logo_url=None,
            venue_name="Stade de la Beaujoire",
            venue_city="Nantes",
        )

        response = profile_response(
            selected,
            data_source="local",
            selected_season_year=2025,
            fixtures=[],
        )

        self.assertEqual(response.official_website, "https://www.fcnantes.com/")
        self.assertEqual(response.major_trophy_count, 15)
        self.assertIsNone(response.latest_match_analysis)


if __name__ == "__main__":
    unittest.main()
