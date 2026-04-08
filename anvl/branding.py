"""ANVL branding — shared colors, banner, and IronDevz identity."""

from rich.text import Text

from . import __version__

# ── Color palette ──────────────────────────────────────────────
CYAN = "#00d7ff"
BLUE = "#5f87ff"
STEEL = "#8787af"
DIM = "#585858"
ORANGE = "#ff8700"

# Gradient stops for the banner (top → bottom)
BANNER_GRADIENT = [
    "#00d7ff",  # bright cyan
    "#00afff",
    "#5f87ff",  # mid blue
    "#5f87ff",
    "#8787d7",  # steel
    "#8787af",
    "#8787af",
    "#6c6c9c",  # fade
]

# ── Raw ASCII lines ───────────────────────────────────────────
ANVL_LINES = [
    "   █████████   ██████   █████ █████   █████ █████",
    "  ███▒▒▒▒▒███ ▒▒██████ ▒▒███ ▒▒███   ▒▒███ ▒▒███",
    " ▒███    ▒███  ▒███▒███ ▒███  ▒███    ▒███  ▒███",
    " ▒███████████  ▒███▒▒███▒███  ▒███    ▒███  ▒███",
    " ▒███▒▒▒▒▒███  ▒███ ▒▒██████  ▒▒███   ███   ▒███",
    " ▒███    ▒███  ▒███  ▒▒█████   ▒▒▒█████▒    ▒███      █",
    " █████   █████ █████  ▒▒█████    ▒▒███      ███████████",
    "▒▒▒▒▒   ▒▒▒▒▒ ▒▒▒▒▒    ▒▒▒▒▒      ▒▒▒      ▒▒▒▒▒▒▒▒▒▒▒",
]

# ── Tagline ───────────────────────────────────────────────────
TAGLINE = "forged by IronDevz"
SUBTITLE = f"Session monitor for Claude Code  ·  v{__version__}"


def styled_banner() -> Text:
    """Return the ANVL banner with a cyan→steel vertical gradient."""
    result = Text()
    for i, line in enumerate(ANVL_LINES):
        color = BANNER_GRADIENT[i % len(BANNER_GRADIENT)]
        result.append(line, style=f"bold {color}")
        if i < len(ANVL_LINES) - 1:
            result.append("\n")
    return result


def styled_tagline() -> Text:
    """Return the 'forged by IronDevz' tagline with hammer motif."""
    t = Text()
    t.append("  ⚒  ", style=f"bold {ORANGE}")
    t.append(TAGLINE, style=f"bold {STEEL}")
    return t


def styled_subtitle() -> Text:
    """Return the version/description subtitle."""
    return Text(f"  {SUBTITLE}", style=DIM)


def version_text() -> Text:
    """Full branded version block for --version."""
    t = Text()
    t.append("anvl-monitor ", style=f"bold {CYAN}")
    t.append(f"v{__version__}", style="bold white")
    t.append(f"  ·  {TAGLINE}", style=STEEL)
    return t
