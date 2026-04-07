"""Configuration and path resolution for ANVL."""

import json
import os
import platform
from pathlib import Path

CLAUDE_HOME = Path.home() / ".claude"
ANVL_CONFIG_DIR = Path.home() / ".anvl"
ANVL_CONFIG_FILE = ANVL_CONFIG_DIR / "config.json"

DEFAULT_CONFIG = {
    "waste_threshold": 7,
    "dashboard_port": 3000,
    "handoff_template": "default",
    "auto_detect_project": True,
    "window_hours": 5,  # Rolling window size (Max 5x = 5h)
    "weighted_quota_limit": 105_000_000,  # Weighted token budget (calibrated for Max 5x)
    "handoff_waste_threshold": 50,  # Auto-handoff when cumulative waste exceeds this
}


def load_config() -> dict:
    """Load ANVL config, creating defaults if needed."""
    if ANVL_CONFIG_FILE.exists():
        with open(ANVL_CONFIG_FILE, "r", encoding="utf-8") as f:
            user_config = json.load(f)
        return {**DEFAULT_CONFIG, **user_config}
    return dict(DEFAULT_CONFIG)


def save_default_config() -> None:
    """Create default config file if it doesn't exist."""
    ANVL_CONFIG_DIR.mkdir(parents=True, exist_ok=True)
    if not ANVL_CONFIG_FILE.exists():
        with open(ANVL_CONFIG_FILE, "w", encoding="utf-8") as f:
            json.dump(DEFAULT_CONFIG, f, indent=2)


def get_projects_dir() -> Path:
    """Return path to Claude Code projects directory."""
    return CLAUDE_HOME / "projects"


def get_sessions_dir() -> Path:
    """Return path to Claude Code active sessions directory."""
    return CLAUDE_HOME / "sessions"


def path_to_slug(path: str | Path) -> str:
    """Convert a filesystem path to Claude Code's project slug format.

    Rule: replace ':', '\\', '/', and ' ' with '-'.
    Example: c:\\Users\\foo\\bar -> c--Users-foo-bar
    """
    s = str(path)
    # Normalize to forward slashes first, then apply replacements
    s = s.replace("\\", "/")
    s = s.replace(":", "-")
    s = s.replace("/", "-")
    s = s.replace(" ", "-")
    # Remove trailing dash if path ended with separator
    return s.rstrip("-")


def find_project_dir(cwd: Path | None = None) -> Path | None:
    """Find the Claude Code project directory for the given cwd.

    Tries exact slug match first, then case-insensitive fallback.
    """
    if cwd is None:
        cwd = Path.cwd()

    slug = path_to_slug(cwd)
    projects_dir = get_projects_dir()

    if not projects_dir.exists():
        return None

    # Exact match
    exact = projects_dir / slug
    if exact.exists():
        return exact

    # Case-insensitive fallback (Windows paths can vary in case)
    slug_lower = slug.lower()
    for entry in projects_dir.iterdir():
        if entry.is_dir() and entry.name.lower() == slug_lower:
            return entry

    return None
