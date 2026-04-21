"""ANVL - Session monitor and handoff tool for Claude Code.

Developed by IronDevz.
"""

__version__ = "0.3.3"
__author__ = "IronDevz"


def _migrate_legacy_state() -> None:
    """One-shot migration from v0.2.x calibration state.

    Renames ~/.anvl/calibration.json → calibration.json.bak so users keep
    their data but the new code doesn't read it. Safe to run every import.
    """
    try:
        from pathlib import Path

        anvl_dir = Path.home() / ".anvl"
        for legacy in ("calibration.json", "growth_curve.json"):
            src = anvl_dir / legacy
            if src.exists():
                dst = src.with_suffix(src.suffix + ".bak")
                if not dst.exists():
                    src.rename(dst)
    except Exception:
        pass


_migrate_legacy_state()
