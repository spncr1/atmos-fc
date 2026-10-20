from types import SimpleNamespace

from backend.models.schemas import MatchSummary
from backend.scripts.export_showcase import ANALYSIS_VERSION, current_analysis
from backend.scripts.refresh_current_results import identity_key, normalise_match
from backend.scripts.validate_showcase import validate


def test_committed_showcase_bundle_is_offline_valid():
    counts = validate(
        showcase=__import__("pathlib").Path("frontend/data/showcase"),
        current=__import__("pathlib").Path("frontend/data/current"),
    )
    assert counts["showcase_fixtures"] == 8760
    assert counts["showcase_analyses"] == 25
    assert counts["current_fixtures"] > 0


def test_only_complete_current_analysis_is_public():
    fixture = SimpleNamespace(
        analysis_cache=SimpleNamespace(
            status="complete",
            payload={"meta": {"analysis_version": ANALYSIS_VERSION}},
        )
    )
    assert current_analysis(fixture) is True
    fixture.analysis_cache.payload["meta"]["analysis_version"] = "legacy"
    assert current_analysis(fixture) is False
    fixture.analysis_cache.status = "failed"
    assert current_analysis(fixture) is False


def test_current_fixture_uses_archive_team_aliases_and_has_no_analysis():
    names = {"arsenal": "42"}
    identities = {"42": {"team_key": "42", "name": "Arsenal", "provider_team_id": 42}}
    raw = {
        "id": 100,
        "utcDate": "2026-08-10T19:00:00Z",
        "status": "FINISHED",
        "matchday": 1,
        "stage": "REGULAR_SEASON",
        "homeTeam": {"id": 57, "name": "Arsenal", "shortName": "Arsenal", "tla": "ARS", "crest": "https://example.com/arsenal.png"},
        "awayTeam": {"id": 999, "name": "Newly Promoted FC", "shortName": "Newly Promoted", "tla": "NEW", "crest": "https://example.com/new.png"},
        "score": {"fullTime": {"home": 2, "away": 0}},
    }
    match = normalise_match(raw, "PL", 2026, names, identities)
    clean = dict(match)
    clean.pop("canonical_fixture_key")
    validated = MatchSummary.model_validate(clean)
    assert validated.home_team_key == "42"
    assert validated.away_team_key == "fd-999"
    assert validated.analysis_available is False
    assert match["canonical_fixture_key"] == identity_key("PL", 2026, raw["utcDate"], "42", "fd-999")
