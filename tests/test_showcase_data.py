import json
from types import SimpleNamespace

import pytest

from backend.models.schemas import MatchSummary
from backend.scripts.import_reviewed_events import read_timeline
from backend.scripts.export_showcase import ANALYSIS_VERSION, current_analysis
from backend.services.public_analysis import timeline_is_complete
from backend.scripts.refresh_current_results import identity_key, normalise_match
from backend.scripts.validate_showcase import validate


def test_committed_showcase_bundle_is_offline_valid():
    counts = validate(
        showcase=__import__("pathlib").Path("frontend/data/showcase"),
        current=__import__("pathlib").Path("frontend/data/current"),
    )
    assert counts["showcase_fixtures"] == 8760
    assert counts["showcase_analyses"] == 19
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


def test_public_timeline_requires_complete_status_events_and_all_goals():
    match = {"score": "2-1", "event_feed_status": "complete"}
    events = [
        {"type": "goal"},
        {"type": "penalty-goal"},
        {"type": "own-goal"},
        {"type": "yellow-card"},
    ]
    assert timeline_is_complete(match, events) is True
    assert timeline_is_complete(match, events[:-2]) is False
    assert timeline_is_complete({**match, "event_feed_status": "failed"}, events) is False
    assert timeline_is_complete(
        {"score": "0-0", "event_feed_status": "complete"},
        [{"type": "yellow-card"}],
    ) is True


def test_reviewed_timeline_import_rejects_incomplete_scoring_events(tmp_path):
    path = tmp_path / "timeline.json"
    path.write_text(json.dumps({
        "fixture_id": "123",
        "home": "Home FC",
        "away": "Away FC",
        "date": "2026-10-20T10:00:00Z",
        "score": "2-0",
        "source_url": "https://example.com/match-report",
        "reviewed_at": "2026-10-21T00:00:00Z",
        "events": [{"minute": 10, "type": "goal", "description": "Home FC score"}],
    }))
    with pytest.raises(ValueError, match="timeline is incomplete"):
        read_timeline(path)


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
