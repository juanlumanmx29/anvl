"""Live terminal monitor for Claude Code sessions using rich."""

import os
import sys
import time
from datetime import datetime, timezone

# Ensure UTF-8 output on Windows
if sys.platform == "win32":
    os.environ.setdefault("PYTHONIOENCODING", "utf-8")
    if hasattr(sys.stdout, "reconfigure"):
        try:
            sys.stdout.reconfigure(encoding="utf-8")
            sys.stderr.reconfigure(encoding="utf-8")
        except Exception:
            pass

from rich.align import Align
from rich.console import Console, Group
from rich.live import Live
from rich.panel import Panel
from rich.text import Text

from . import __version__
from .analyzer import format_tokens
from .branding import styled_banner, styled_subtitle, styled_tagline
from .sessions import collect_all_sessions, compute_savings

TIER_COLORS = {
    "green": "green",
    "yellow": "yellow",
    "red": "red",
    "critical": "bright_red",
}
TIER_ICONS = {
    "green": "🟢",
    "yellow": "🟡",
    "red": "🔴",
    "critical": "⛔",
}

# Update check cache (check at most once per hour)
_update_cache: dict = {"latest": None, "checked_at": 0.0}
_UPDATE_CHECK_INTERVAL = 3600  # seconds


def _check_for_update() -> str | None:
    """Check PyPI for a newer version. Returns latest version or None.

    Cached for 1 hour.  Never blocks the monitor — fails silently.
    """
    now = time.monotonic()
    if now - _update_cache["checked_at"] < _UPDATE_CHECK_INTERVAL:
        return _update_cache["latest"]

    _update_cache["checked_at"] = now
    try:
        import json as _json
        from urllib.request import urlopen

        with urlopen("https://pypi.org/pypi/anvl-monitor/json", timeout=3) as resp:
            data = _json.loads(resp.read())
        latest = data.get("info", {}).get("version", "")
        if latest and latest != __version__:
            # Only notify if PyPI version is actually newer
            def _to_tuple(v: str) -> tuple:
                return tuple(int(x) for x in v.split("."))

            if _to_tuple(latest) > _to_tuple(__version__):
                _update_cache["latest"] = latest
            else:
                _update_cache["latest"] = None
        else:
            _update_cache["latest"] = None
    except Exception:
        _update_cache["latest"] = None
    return _update_cache["latest"]


def _churn_bar(churn: float, color: str, bar_len: int = 20, max_churn: float = 3.0) -> str:
    """Render a colored bar — fills up as churn approaches max_churn."""
    pct = min(1.0, churn / max_churn) if max_churn > 0 else 0.0
    filled = int(pct * bar_len)
    return f"[{color}]{'█' * filled}{'░' * (bar_len - filled)}[/{color}]"


def _elapsed(started_at: datetime) -> str:
    """Human-readable elapsed time."""
    delta = datetime.now(timezone.utc) - started_at
    total_secs = int(delta.total_seconds())
    if total_secs < 60:
        return f"{total_secs}s"
    if total_secs < 3600:
        return f"{total_secs // 60}m"
    hours = total_secs // 3600
    mins = (total_secs % 3600) // 60
    return f"{hours}h {mins}m"


def _current_avg(s) -> int:
    """Get current avg tokens/turn for a session (last 5 turns)."""
    if not s.per_turn_tokens:
        return 0
    window = min(5, len(s.per_turn_tokens))
    return sum(s.per_turn_tokens[-window:]) // window


def build_monitor_display() -> Group:
    """Build the monitor display — one line per active session, churn-based."""
    summaries = collect_all_sessions()
    active = [s for s in summaries if s.is_active]

    header = (
        "  Churn thresholds: [green]<0.5[/green] [yellow]<1.5[/yellow] "
        f"[red]<3[/red] [bright_red]≥3[/bright_red]  │  Active: [bold]{len(active)}[/bold]"
    )

    session_lines: list[str] = []

    if not active:
        session_lines.append("  [dim]No active sessions[/dim]")
    else:
        titles = []
        for s in active:
            proj = s.project or "?"
            ai = s.ai_title or "Untitled"
            titles.append(f"{proj} > {ai}"[:40])

        for i, s in enumerate(active):
            tier = s.health_tier
            color = TIER_COLORS.get(tier, "white")
            icon = TIER_ICONS.get(tier, "●")
            title = titles[i]
            elapsed = _elapsed(s.started_at)
            cost_str = format_tokens(int(s.weighted_cost))

            if s.turns == 0:
                session_lines.append(f"  {icon} {title:<40s}  [dim]waiting...[/dim]")
            elif s.turns < 3:
                turns_str = f"{s.turns} turn{'s' if s.turns != 1 else ''}"
                session_lines.append(
                    f"  {icon} {title:<40s}  {turns_str:>8s}  {elapsed:>5s}"
                    f"  [dim]warming up...[/dim]  [cyan]{cost_str}[/cyan]"
                )
            else:
                bar = _churn_bar(s.churn_score, color)
                turns_str = f"{s.turns} turn{'s' if s.turns != 1 else ''}"
                churn_str = f"[{color}]{s.churn_score:.2f}[/{color}]"

                session_lines.append(
                    f"  {icon} {title:<40s}  {turns_str:>8s}  {elapsed:>5s}  "
                    f"churn {churn_str} {bar}  [cyan]{cost_str}[/cyan]"
                )

                if tier != "green" and s.turns >= 5:
                    cur = _current_avg(s)
                    bl = s.session_baseline_tpt
                    if bl > 0:
                        ratio = round(cur / bl, 1) if bl else 1.0
                        cost_detail = f"~{format_tokens(bl)} → ~{format_tokens(cur)}/turn ({ratio}x)"
                    else:
                        cost_detail = f"~{format_tokens(cur)}/turn"
                    session_lines.append(f"    [{color}]⚠ {tier.upper()} — {s.health_reason} · {cost_detail}[/{color}]")

    # Compose panel content
    content_parts = [
        Align.center(styled_banner()),
        Align.center(styled_tagline()),
        Align.center(styled_subtitle()),
        Text(""),
        Text.from_markup(header),
        Text(""),
    ]
    for line in session_lines:
        content_parts.append(Text.from_markup(line))

    panel = Panel(
        Group(*content_parts),
        subtitle="[dim]Ctrl+C to exit  │  Refreshes every 2s[/dim]",
        border_style="#00afff",
    )

    parts: list = [panel]

    # Savings footer
    savings = compute_savings(summaries)
    wasted = savings["total_wasted"]
    saved = savings["saved_tokens"]
    if wasted > 0 or saved > 0:
        footer_parts = []
        if saved > 0:
            footer_parts.append(f"[green]{format_tokens(saved)}[/green] saved by rotation")
        if wasted > 0:
            footer_parts.append(f"[red]{format_tokens(wasted)}[/red] wasted by inflation")
        parts.append(Text.from_markup(f"  {' │ '.join(footer_parts)}"))

    parts.append(
        Text.from_markup(
            "  [dim]churn = redundant reads / productive edits (10-turn window) │ cost = weighted tokens[/dim]"
        )
    )

    # Update notice
    latest = _check_for_update()
    if latest:
        parts.append(
            Text.from_markup(
                f"  [bold yellow]Update available:[/bold yellow]"
                f" {__version__} → {latest}"
                f"  [dim]pip install --upgrade anvl-monitor[/dim]"
            )
        )

    return Group(*parts)


def monitor_session(refresh_interval: float = 2.0) -> None:
    """Main monitor loop with rich Live display. Works from any directory."""
    console = Console()

    console.print("[dim]Press Ctrl+C to exit[/dim]\n")

    with Live(console=console, refresh_per_second=1) as live:
        try:
            while True:
                display = build_monitor_display()
                live.update(display)
                time.sleep(refresh_interval)
        except KeyboardInterrupt:
            console.print("\n[dim]Monitor stopped.[/dim]")
