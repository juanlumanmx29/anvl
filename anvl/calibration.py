"""Auto-calibration: learn per-project baselines from historical sessions.

Instead of using min(first 5 turns) of the current session as the baseline
(fragile — one cheap read skews everything), calibration collects baselines
from ALL sessions in a project and uses the median as a stable reference.

Flow:
  1. Each time a session reaches 5+ turns, its baseline is recorded.
  2. The project's calibrated baseline = median of all recorded baselines.
  3. Waste factor uses calibrated baseline when available, per-session otherwise.

Storage: ~/.anvl/calibration.json
"""

import json
import statistics
from datetime import datetime, timezone
from pathlib import Path

from .config import ANVL_CONFIG_DIR

CALIBRATION_FILE = ANVL_CONFIG_DIR / "calibration.json"

# Minimum sessions needed before calibration kicks in
MIN_SESSIONS_FOR_CALIBRATION = 3

# Maximum baselines to store per project (rolling window)
MAX_BASELINES_PER_PROJECT = 50


def _load_calibration() -> dict:
    """Load calibration data from disk."""
    if not CALIBRATION_FILE.exists():
        return {"projects": {}}
    try:
        with open(CALIBRATION_FILE, "r", encoding="utf-8") as f:
            return json.load(f)
    except (json.JSONDecodeError, OSError):
        return {"projects": {}}


def _save_calibration(data: dict) -> None:
    """Persist calibration data to disk."""
    ANVL_CONFIG_DIR.mkdir(parents=True, exist_ok=True)
    with open(CALIBRATION_FILE, "w", encoding="utf-8") as f:
        json.dump(data, f, indent=2)
        f.write("\n")


def record_baseline(project_slug: str, session_id: str, baseline: int) -> None:
    """Record a session's baseline for a project.

    Called when a session has >= 5 turns. Idempotent per session_id.
    baseline = min(tokens for first 5 non-tool turns).
    """
    if baseline <= 0:
        return

    data = _load_calibration()
    projects = data.setdefault("projects", {})
    proj = projects.setdefault(project_slug, {
        "baselines": [],
        "session_ids": [],
        "calibrated_baseline": None,
        "last_updated": None,
        "session_count": 0,
    })

    # Don't double-record the same session
    if session_id in proj.get("session_ids", []):
        return

    proj.setdefault("baselines", []).append(baseline)
    proj.setdefault("session_ids", []).append(session_id)

    # Trim to rolling window
    if len(proj["baselines"]) > MAX_BASELINES_PER_PROJECT:
        proj["baselines"] = proj["baselines"][-MAX_BASELINES_PER_PROJECT:]
        proj["session_ids"] = proj["session_ids"][-MAX_BASELINES_PER_PROJECT:]

    proj["session_count"] = len(proj["baselines"])
    proj["last_updated"] = datetime.now(timezone.utc).isoformat()

    # Recompute calibrated baseline (median)
    if len(proj["baselines"]) >= MIN_SESSIONS_FOR_CALIBRATION:
        proj["calibrated_baseline"] = int(statistics.median(proj["baselines"]))
    else:
        proj["calibrated_baseline"] = None

    _save_calibration(data)


def get_calibrated_baseline(project_slug: str) -> int | None:
    """Get the calibrated baseline for a project, or None if not enough data.

    Returns the median baseline across all recorded sessions.
    """
    data = _load_calibration()
    proj = data.get("projects", {}).get(project_slug)
    if proj is None:
        return None
    return proj.get("calibrated_baseline")


def get_calibration_info(project_slug: str) -> dict | None:
    """Get full calibration info for a project (for display)."""
    data = _load_calibration()
    proj = data.get("projects", {}).get(project_slug)
    if proj is None:
        return None
    return {
        "session_count": proj.get("session_count", 0),
        "calibrated_baseline": proj.get("calibrated_baseline"),
        "baselines": proj.get("baselines", []),
        "last_updated": proj.get("last_updated"),
        "min_needed": MIN_SESSIONS_FOR_CALIBRATION,
    }


def get_all_calibration() -> dict:
    """Get calibration data for all projects."""
    data = _load_calibration()
    return data.get("projects", {})


def reset_calibration(project_slug: str | None = None) -> None:
    """Reset calibration data for a project, or all projects if None."""
    if project_slug is None:
        _save_calibration({"projects": {}})
        return

    data = _load_calibration()
    projects = data.get("projects", {})
    if project_slug in projects:
        del projects[project_slug]
        _save_calibration(data)
