from datetime import datetime, timedelta, timezone
from types import SimpleNamespace

from backend.scripts.check_analysis_freshness import stale_analysis
from backend.scripts.refresh_match_analyses import should_attempt, source_is_stale


NOW = datetime(2026, 11, 1, 12, tzinfo=timezone.utc)
LOOKBACK = timedelta(days=7)


def fixture(*, age_hours=36, cache=None, comments=None, events=None, event_status=None):
    return SimpleNamespace(
        kickoff_at=NOW - timedelta(hours=age_hours),
        analysis_cache=cache,
        youtube_comment_cache=comments,
        event_sync_status=event_status,
        events=events or [],
        home_goals=1,
        away_goals=0,
    )


def test_only_completed_reaction_windows_within_lookback_are_eligible():
    assert not should_attempt(fixture(age_hours=25), NOW, LOOKBACK)
    assert should_attempt(fixture(age_hours=26), NOW, LOOKBACK)
    assert not should_attempt(fixture(age_hours=8 * 24), NOW, LOOKBACK)


def test_current_saved_analysis_is_not_recreated():
    saved = SimpleNamespace(status="complete", payload={"meta": {"analysis_version": "match_context_v2"}})
    complete = fixture(
        cache=saved,
        events=[SimpleNamespace(type="goal")],
        event_status=SimpleNamespace(status="complete"),
    )
    assert not should_attempt(complete, NOW, LOOKBACK)
    assert should_attempt(fixture(cache=saved), NOW, LOOKBACK)
    saved.payload["meta"]["analysis_version"] = "legacy"
    assert should_attempt(complete, NOW, LOOKBACK)


def test_unavailable_comments_get_a_cooldown_then_retry():
    comments = SimpleNamespace(status="unavailable", checked_at=NOW - timedelta(hours=23))
    assert not should_attempt(fixture(comments=comments), NOW, LOOKBACK)
    comments.checked_at -= timedelta(hours=1)
    assert should_attempt(fixture(comments=comments), NOW, LOOKBACK)


def test_stale_analysis_alert_requires_recent_result_activity():
    recent = [{"date": (NOW - timedelta(days=1)).isoformat()}]
    old = {"one": {"match": {"date": (NOW - timedelta(days=8)).isoformat()}}}
    fresh = {"one": {"match": {"date": (NOW - timedelta(days=2)).isoformat()}}}
    assert stale_analysis(recent, old, NOW)
    assert not stale_analysis(recent, fresh, NOW)
    assert not stale_analysis([{"date": (NOW - timedelta(days=5)).isoformat()}], old, NOW)


def test_source_freshness_flags_a_stalled_fixture_feed():
    latest_result = NOW - timedelta(hours=12)
    assert source_is_stale(latest_result, NOW - timedelta(days=6), NOW)
    assert source_is_stale(latest_result, None, NOW)
    assert not source_is_stale(latest_result, NOW - timedelta(days=2), NOW)
    assert not source_is_stale(NOW - timedelta(days=5), NOW - timedelta(days=20), NOW)
