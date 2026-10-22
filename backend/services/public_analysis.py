"""Publication rules for durable, static match analyses."""

from __future__ import annotations

import re
from typing import Any, Iterable


SCORING_EVENT_TYPES = {"goal", "own-goal", "penalty-goal"}


def score_goal_total(score: str | None) -> int | None:
    match = re.fullmatch(r"\s*(\d+)\s*-\s*(\d+)\s*", str(score or ""))
    if match is None:
        return None
    return int(match.group(1)) + int(match.group(2))


def timeline_is_complete(match: Any, events: Iterable[Any]) -> bool:
    """Return True only when a finished match has a complete public timeline."""

    event_rows = list(events)
    status = field(match, "event_feed_status")
    expected_goals = score_goal_total(field(match, "score"))
    if status != "complete" or not event_rows or expected_goals is None:
        return False
    recorded_goals = sum(
        1 for event in event_rows if field(event, "type") in SCORING_EVENT_TYPES
    )
    return recorded_goals == expected_goals


def field(value: Any, name: str) -> Any:
    if isinstance(value, dict):
        return value.get(name)
    return getattr(value, name, None)
