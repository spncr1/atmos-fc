"""Validate and store one source-neutral, human-reviewed match timeline."""

from __future__ import annotations

import argparse
import json
import os
import tempfile
from datetime import datetime
from pathlib import Path
from typing import Any
from urllib.parse import urlparse

from pydantic import BaseModel, Field, field_validator

from backend.models.schemas import MatchEvent
from backend.services.public_analysis import timeline_is_complete


class ReviewedTimeline(BaseModel):
    fixture_id: str = Field(min_length=1)
    home: str = Field(min_length=1)
    away: str = Field(min_length=1)
    date: str = Field(min_length=10)
    score: str = Field(pattern=r"^\s*\d+\s*-\s*\d+\s*$")
    source_url: str
    reviewed_at: datetime
    events: list[MatchEvent] = Field(min_length=1)

    @field_validator("source_url")
    @classmethod
    def safe_source_url(cls, value: str) -> str:
        parsed = urlparse(value)
        if parsed.scheme not in {"http", "https"} or not parsed.netloc:
            raise ValueError("source_url must be a valid HTTP or HTTPS URL")
        return value


def read_timeline(path: Path) -> ReviewedTimeline:
    timeline = ReviewedTimeline.model_validate_json(path.read_text())
    public_match = {"score": timeline.score, "event_feed_status": "complete"}
    if not timeline_is_complete(public_match, timeline.events):
        raise ValueError(
            "Reviewed timeline is incomplete: it needs key events and one scoring event "
            "for every goal in the final score."
        )
    return timeline


def write_timeline(timeline: ReviewedTimeline, destination: Path) -> Path:
    destination.mkdir(parents=True, exist_ok=True)
    target = destination / f"{timeline.fixture_id}.json"
    payload: dict[str, Any] = timeline.model_dump(mode="json")
    handle, temporary_name = tempfile.mkstemp(
        prefix=f".{timeline.fixture_id}-", suffix=".json", dir=destination
    )
    try:
        with os.fdopen(handle, "w", encoding="utf-8") as temporary:
            json.dump(payload, temporary, ensure_ascii=False, indent=2, sort_keys=True)
            temporary.write("\n")
        os.replace(temporary_name, target)
    except Exception:
        Path(temporary_name).unlink(missing_ok=True)
        raise
    return target


def main() -> None:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("input", type=Path)
    parser.add_argument(
        "--output",
        type=Path,
        default=Path("backend/data/reviewed_event_timelines"),
    )
    args = parser.parse_args()
    timeline = read_timeline(args.input)
    target = write_timeline(timeline, args.output)
    print(target)


if __name__ == "__main__":
    main()
