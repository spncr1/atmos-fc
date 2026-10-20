"""Validate committed showcase and current-result artifacts without network access."""

from __future__ import annotations

import argparse
import hashlib
import json
import re
from pathlib import Path
from typing import Any

from backend.models.schemas import AnalysisResponse, CompetitionProfileResponse, MatchSummary, MetadataResponse, TeamProfileResponse

SHOWCASE_FILES = ("metadata.json", "matches.json", "teams.json", "competitions.json", "analyses.json")
FORBIDDEN_KEYS = {"raw_payload", "error_message", "needs_review", "profile_needs_review", "wikidata_qid", "matching_diagnostics"}
SECRET_PATTERN = re.compile(r"(?:api[_-]?key|authorization|bearer\s+[a-z0-9._-]{12,}|database_url)", re.I)


def compact(value: Any) -> bytes:
    return json.dumps(value, ensure_ascii=False, separators=(",", ":"), sort_keys=True).encode("utf-8")


def load(path: Path) -> Any:
    return json.loads(path.read_text())


def scan(value: Any, path: str = "$") -> None:
    if isinstance(value, dict):
        for key, child in value.items():
            if str(key).lower() in FORBIDDEN_KEYS:
                raise ValueError(f"Prohibited field {path}.{key}")
            scan(child, f"{path}.{key}")
    elif isinstance(value, list):
        for index, child in enumerate(value):
            scan(child, f"{path}[{index}]")
    elif isinstance(value, str) and SECRET_PATTERN.search(value):
        raise ValueError(f"Possible secret at {path}")


def validate(showcase: Path, current: Path) -> dict[str, int]:
    documents = {name: load(showcase / name) for name in SHOWCASE_FILES}
    manifest = load(showcase / "manifest.json")
    expected = hashlib.sha256(b"".join(compact(documents[name]) for name in sorted(documents))).hexdigest()
    if manifest.get("integrity", {}).get("bundle_checksum") != expected:
        raise ValueError("Showcase bundle checksum does not match its manifest")
    MetadataResponse.model_validate(documents["metadata.json"])
    matches = documents["matches.json"].get("matches") or []
    for row in matches:
        MatchSummary.model_validate(row)
    if len(matches) != manifest.get("fixture_count"):
        raise ValueError("Showcase fixture count does not match its manifest")
    analyses = documents["analyses.json"].get("analyses") or {}
    for row in analyses.values():
        parsed = AnalysisResponse.model_validate(row)
        if parsed.meta.analysis_version != "match_context_v2":
            raise ValueError("Showcase contains a non-current analysis payload")
    if len(analyses) != manifest.get("analysis_count"):
        raise ValueError("Showcase analysis count does not match its manifest")
    for team in (documents["teams.json"].get("teams") or {}).values():
        for profile in (team.get("profiles") or {}).values():
            TeamProfileResponse.model_validate(profile)
    for competition in (documents["competitions.json"].get("competitions") or {}).values():
        for profile in competition.values():
            CompetitionProfileResponse.model_validate(profile)
    for name, document in {**documents, "manifest.json": manifest}.items():
        scan(document, name)

    current_pack = load(current / "current-season.json")
    pack_copy = dict(current_pack)
    integrity = pack_copy.pop("integrity", {})
    checksum = hashlib.sha256(compact(pack_copy)).hexdigest()
    if integrity.get("pack_checksum") != checksum:
        raise ValueError("Current pack checksum does not match")
    current_matches = current_pack.get("matches") or []
    for row in current_matches:
        clean = dict(row)
        clean.pop("canonical_fixture_key", None)
        parsed = MatchSummary.model_validate(clean)
        if parsed.status != "FINISHED" or parsed.analysis_available:
            raise ValueError("Current pack includes an unfinished or analysis-enabled fixture")
    scan(current_pack, "current-season.json")
    return {"showcase_fixtures": len(matches), "showcase_analyses": len(analyses), "current_fixtures": len(current_matches)}


def main() -> None:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--showcase", type=Path, default=Path("frontend/data/showcase"))
    parser.add_argument("--current", type=Path, default=Path("frontend/data/current"))
    args = parser.parse_args()
    print(json.dumps(validate(args.showcase, args.current), indent=2))


if __name__ == "__main__":
    main()
