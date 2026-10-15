"""Load Atmos FC's authoritative project version."""

from __future__ import annotations

import re
from pathlib import Path

VERSION_FILE = Path(__file__).resolve().parents[1] / "VERSION"
SEMVER_PATTERN = re.compile(
    r"^(0|[1-9]\d*)\.(0|[1-9]\d*)\.(0|[1-9]\d*)"
    r"(?:-[0-9A-Za-z.-]+)?(?:\+[0-9A-Za-z.-]+)?$"
)


def load_project_version() -> str:
    """Return the validated product version from the repository root."""

    version = VERSION_FILE.read_text(encoding="utf-8").strip()
    if not SEMVER_PATTERN.fullmatch(version):
        raise RuntimeError(f"VERSION must contain one valid semantic version; received {version!r}")
    return version


PROJECT_VERSION = load_project_version()
