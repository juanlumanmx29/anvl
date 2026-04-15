"""Configuration and path resolution for ANVL."""

import json
from pathlib import Path

CLAUDE_HOME = Path.home() / ".claude"
ANVL_CONFIG_DIR = Path.home() / ".anvl"
ANVL_CONFIG_FILE = ANVL_CONFIG_DIR / "config.json"

DEFAULT_CONFIG = {
    "auto_detect_project": True,
    "window_hours": 5,  # Rolling quota window
    "weighted_quota_limit": 105_000_000,
    # Churn-based alert thresholds (redundant reads / productive edits)
    "churn_yellow": 0.5,
    "churn_red": 1.5,
    "churn_critical": 3.0,
    "churn_window": 10,  # rolling turn window for churn
    "handoffs_dir": ".anvl/handoffs",
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

    Rule: replace ':', '\\', '/', ' ', and '.' with '-', then lowercase.
    Lowercasing ensures Windows case-insensitive paths produce the same slug.
    Example: C:\\Users\\foo\\bar -> c--users-foo-bar
    """
    s = str(path)
    # Normalize to forward slashes first, then apply replacements
    s = s.replace("\\", "/")
    s = s.replace(":", "-")
    s = s.replace("/", "-")
    s = s.replace(" ", "-")
    s = s.replace(".", "-")
    # Lowercase for case-insensitive matching (Windows paths vary in case)
    s = s.lower()
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
