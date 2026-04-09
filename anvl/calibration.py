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
GROWTH_CURVE_FILE = ANVL_CONFIG_DIR / "growth_curve.json"

# Minimum sessions needed before calibration kicks in
MIN_SESSIONS_FOR_CALIBRATION = 3

# Maximum baselines to store (rolling window)
MAX_BASELINES = 200

# Minimum sessions to generate a growth curve
MIN_SESSIONS_FOR_CURVE = 10

# Default baseline for new users (derived from real-world median across 56 sessions).
# Used when no calibration data exists yet so health works from turn 1.
DEFAULT_BASELINE = 80_000

# Default growth curve (p75 growth multipliers by turn index, smoothed).
# Derived from 59 real sessions.  Used before enough local data exists.
# fmt: off
DEFAULT_GROWTH_CURVE: dict = {
    "version": 1,
    "session_count": 0,
    "fresh_cost_p50": 207_000,
    "growth_p75": [
        2.5, 7.9, 3.0, 8.6, 6.8,       # turns 0-4
        8.2, 9.3, 11.2, 11.7, 12.8,     # turns 5-9
        11.8, 12.7, 10.8, 13.5, 13.5,   # turns 10-14
        13.5, 13.5, 13.5, 13.5, 13.5,   # turns 15-19
        13.5, 13.5, 13.5, 13.5, 17.4,   # turns 20-24
        17.4, 17.4, 17.4, 33.5, 33.5,   # turns 25-29
        33.5, 33.5, 33.5, 47.6, 47.6,   # turns 30-34
        47.6, 47.6, 47.6, 47.6, 47.6,   # turns 35-39
    ],
}
# fmt: on


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

    # Periodically rebuild the growth curve
    maybe_rebuild_growth_curve()


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


# ---------------------------------------------------------------------------
# Growth curve: expected cost growth by turn index
# ---------------------------------------------------------------------------

# Module-level cache for the growth curve (mtime-based)
_curve_cache: dict = {"mtime": 0.0, "data": None}


def build_growth_curve(sessions: list) -> dict:
    """Build a growth curve from historical session data.

    Each session contributes per-turn growth multipliers (cost / session_baseline).
    We compute the 75th percentile at each turn index, then smooth with a
    cumulative max so the curve never decreases.

    *sessions* is a list of objects with `per_turn_tokens: list[int]` and
    `session_baseline: int` attributes (e.g., SessionSummary).
    """
    qualified = [s for s in sessions if len(s.per_turn_tokens) >= 5 and s.session_baseline > 0]

    if len(qualified) < MIN_SESSIONS_FOR_CURVE:
        return dict(DEFAULT_GROWTH_CURVE)

    # Collect growth multipliers per turn index
    max_turns = max(len(s.per_turn_tokens) for s in qualified)
    growth_by_turn: list[list[float]] = [[] for _ in range(max_turns)]

    for s in qualified:
        bl = s.session_baseline
        for i, cost in enumerate(s.per_turn_tokens):
            growth_by_turn[i].append(cost / bl)

    # Compute p75, only for turns with enough data points
    raw_p75: list[float] = []
    for turn_data in growth_by_turn:
        if len(turn_data) >= 5:
            raw_p75.append(statistics.quantiles(turn_data, n=4)[2])
        else:
            break  # stop when data gets too sparse

    # Smooth: cumulative max so curve never decreases
    smoothed: list[float] = []
    running_max = 1.0
    for v in raw_p75:
        running_max = max(running_max, v)
        smoothed.append(round(running_max, 1))

    # Fresh cost: median of avg(turns 0-2) across all sessions
    fresh_costs = []
    for s in qualified:
        n = min(3, len(s.per_turn_tokens))
        fresh_costs.append(int(sum(s.per_turn_tokens[:n]) / n))
    fresh_p50 = int(statistics.median(fresh_costs))

    return {
        "version": 1,
        "generated_at": datetime.now(timezone.utc).isoformat(),
        "session_count": len(qualified),
        "fresh_cost_p50": fresh_p50,
        "growth_p75": smoothed,
    }


def save_growth_curve(curve: dict) -> None:
    """Persist growth curve to disk."""
    ANVL_CONFIG_DIR.mkdir(parents=True, exist_ok=True)
    with open(GROWTH_CURVE_FILE, "w", encoding="utf-8") as f:
        json.dump(curve, f, indent=2)
        f.write("\n")


def load_growth_curve() -> dict:
    """Load growth curve from disk with mtime caching."""
    if not GROWTH_CURVE_FILE.exists():
        return {}
    try:
        mtime = GROWTH_CURVE_FILE.stat().st_mtime
        if _curve_cache["data"] is not None and mtime == _curve_cache["mtime"]:
            return _curve_cache["data"]
        with open(GROWTH_CURVE_FILE, "r", encoding="utf-8") as f:
            data = json.load(f)
        _curve_cache["mtime"] = mtime
        _curve_cache["data"] = data
        return data
    except (json.JSONDecodeError, OSError):
        return {}


def get_growth_curve() -> dict:
    """Get the growth curve. Falls back to DEFAULT_GROWTH_CURVE."""
    curve = load_growth_curve()
    return curve if curve else dict(DEFAULT_GROWTH_CURVE)


def maybe_rebuild_growth_curve() -> None:
    """Rebuild the growth curve if enough new sessions have been recorded.

    Triggers when session_count is a multiple of 5.  Avoids circular
    imports by importing collect_all_sessions lazily.
    """
    data = _load_calibration()
    count = data.get("session_count", 0)
    if count < MIN_SESSIONS_FOR_CURVE or count % 5 != 0:
        return

    existing = load_growth_curve()
    if existing.get("session_count", 0) >= count:
        return  # already up to date

    from .sessions import collect_all_sessions

    sessions = collect_all_sessions()
    curve = build_growth_curve(sessions)
    if curve.get("growth_p75"):
        save_growth_curve(curve)
