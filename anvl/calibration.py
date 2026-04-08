"""Auto-calibration: learn a GLOBAL baseline from historical sessions.

Instead of per-project baselines (fragile — needs 3+ sessions per project),
calibration collects baselines from ALL sessions globally and uses the
median as a stable reference for what a "normal" Claude Code turn costs.

Flow:
  1. Each time a session reaches 5+ turns, its baseline is recorded.
  2. The global calibrated baseline = median of ALL recorded baselines.
  3. Waste factor uses calibrated baseline when available, allowing
     health to be computed from turn 1 (no warmup needed).

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

# Maximum baselines to store (rolling window)
MAX_BASELINES = 200

# Default baseline for new users (derived from real-world median across 56 sessions).
# Used when no calibration data exists yet so health works from turn 1.
DEFAULT_BASELINE = 80_000


def _load_calibration() -> dict:
    """Load calibration data from disk."""
    if not CALIBRATION_FILE.exists():
        return {"baselines": [], "session_ids": [], "calibrated_baseline": None}
    try:
        with open(CALIBRATION_FILE, "r", encoding="utf-8") as f:
            data = json.load(f)
        # Migrate from old per-project format if needed
        if "projects" in data and "baselines" not in data:
            return _migrate_from_project_format(data)
        return data
    except (json.JSONDecodeError, OSError):
        return {"baselines": [], "session_ids": [], "calibrated_baseline": None}


def _migrate_from_project_format(old_data: dict) -> dict:
    """Migrate from per-project calibration to global."""
    all_baselines = []
    all_session_ids = []
    for proj_info in old_data.get("projects", {}).values():
        for i, bl in enumerate(proj_info.get("baselines", [])):
            sids = proj_info.get("session_ids", [])
            sid = sids[i] if i < len(sids) else f"migrated-{len(all_session_ids)}"
            if sid not in all_session_ids:
                all_baselines.append(bl)
                all_session_ids.append(sid)

    calibrated = None
    if len(all_baselines) >= MIN_SESSIONS_FOR_CALIBRATION:
        calibrated = int(statistics.median(all_baselines))

    new_data = {
        "baselines": all_baselines[-MAX_BASELINES:],
        "session_ids": all_session_ids[-MAX_BASELINES:],
        "calibrated_baseline": calibrated,
        "session_count": len(all_baselines),
        "last_updated": datetime.now(timezone.utc).isoformat(),
    }
    _save_calibration(new_data)
    return new_data


def _save_calibration(data: dict) -> None:
    """Persist calibration data to disk."""
    ANVL_CONFIG_DIR.mkdir(parents=True, exist_ok=True)
    with open(CALIBRATION_FILE, "w", encoding="utf-8") as f:
        json.dump(data, f, indent=2)
        f.write("\n")


def record_baseline(session_id: str, baseline: int) -> None:
    """Record a session's baseline globally.

    Called when a session has >= 5 turns. Idempotent per session_id.
    baseline = min(tokens for first 5 non-tool turns).
    """
    if baseline <= 0:
        return

    data = _load_calibration()

    # Don't double-record the same session
    if session_id in data.get("session_ids", []):
        return

    data.setdefault("baselines", []).append(baseline)
    data.setdefault("session_ids", []).append(session_id)

    # Trim to rolling window
    if len(data["baselines"]) > MAX_BASELINES:
        data["baselines"] = data["baselines"][-MAX_BASELINES:]
        data["session_ids"] = data["session_ids"][-MAX_BASELINES:]

    data["session_count"] = len(data["baselines"])
    data["last_updated"] = datetime.now(timezone.utc).isoformat()

    # Recompute calibrated baseline (median)
    if len(data["baselines"]) >= MIN_SESSIONS_FOR_CALIBRATION:
        data["calibrated_baseline"] = int(statistics.median(data["baselines"]))
    else:
        data["calibrated_baseline"] = None

    _save_calibration(data)


def get_calibrated_baseline() -> int:
    """Get the global calibrated baseline. Always returns a value.

    Returns calibrated median if enough data, otherwise DEFAULT_BASELINE.
    """
    data = _load_calibration()
    return data.get("calibrated_baseline") or DEFAULT_BASELINE


def get_calibration_info() -> dict:
    """Get full calibration info (for display)."""
    data = _load_calibration()
    return {
        "session_count": data.get("session_count", 0),
        "calibrated_baseline": data.get("calibrated_baseline"),
        "baselines": data.get("baselines", []),
        "last_updated": data.get("last_updated"),
        "min_needed": MIN_SESSIONS_FOR_CALIBRATION,
    }


def reset_calibration() -> None:
    """Reset all calibration data."""
    _save_calibration(
        {
            "baselines": [],
            "session_ids": [],
            "calibrated_baseline": None,
            "session_count": 0,
        }
    )


def export_calibration(path: Path) -> None:
    """Export calibration data to an external file."""
    import shutil

    if CALIBRATION_FILE.exists():
        shutil.copy2(CALIBRATION_FILE, path)
    else:
        _save_calibration(
            {
                "baselines": [],
                "session_ids": [],
                "calibrated_baseline": None,
                "session_count": 0,
            }
        )
        shutil.copy2(CALIBRATION_FILE, path)


def import_calibration(path: Path) -> int:
    """Import calibration data from an external file, merging with existing.

    Returns the number of new baselines added.
    """
    with open(path, "r", encoding="utf-8") as f:
        imported = json.load(f)

    # Handle both old per-project format and new global format
    if "projects" in imported and "baselines" not in imported:
        # Old format — extract all baselines
        imp_baselines = []
        imp_sids = []
        for proj_info in imported.get("projects", {}).values():
            for i, bl in enumerate(proj_info.get("baselines", [])):
                sids = proj_info.get("session_ids", [])
                sid = sids[i] if i < len(sids) else f"imported-{len(imp_sids)}"
                imp_baselines.append(bl)
                imp_sids.append(sid)
    else:
        imp_baselines = imported.get("baselines", [])
        imp_sids = imported.get("session_ids", [])

    # Merge with existing
    data = _load_calibration()
    existing_sids = set(data.get("session_ids", []))
    added = 0

    for i, sid in enumerate(imp_sids):
        if sid not in existing_sids and i < len(imp_baselines):
            bl = imp_baselines[i]
            if bl > 0:
                data.setdefault("baselines", []).append(bl)
                data.setdefault("session_ids", []).append(sid)
                existing_sids.add(sid)
                added += 1

    # Trim and recompute
    if len(data["baselines"]) > MAX_BASELINES:
        data["baselines"] = data["baselines"][-MAX_BASELINES:]
        data["session_ids"] = data["session_ids"][-MAX_BASELINES:]

    data["session_count"] = len(data["baselines"])
    data["last_updated"] = datetime.now(timezone.utc).isoformat()

    if len(data["baselines"]) >= MIN_SESSIONS_FOR_CALIBRATION:
        data["calibrated_baseline"] = int(statistics.median(data["baselines"]))
    else:
        data["calibrated_baseline"] = None

    _save_calibration(data)
    return added
