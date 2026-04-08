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

from .analyzer import format_tokens
from .branding import styled_banner, styled_subtitle, styled_tagline
from .calibration import DEFAULT_BASELINE, get_calibration_info
from .sessions import collect_all_sessions, compute_savings


def _health_bar(pct: int, color: str, bar_len: int = 20) -> str:
    """Render a colored health bar."""
    filled = int(pct / 100 * bar_len)
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
    """Build the redesigned monitor display — clean, scannable, one line per session."""
    summaries = collect_all_sessions()
    active = [s for s in summaries if s.is_active]

    # Calibration info
    cal_info = get_calibration_info()
    cal_bl = cal_info.get("calibrated_baseline")
    cal_count = cal_info.get("session_count", 0)
    if cal_bl:
        cal_str = f"[cyan]{format_tokens(cal_bl)}[/cyan]/turn [dim]({cal_count} sessions)[/dim]"
    else:
        cal_str = f"[dim]{format_tokens(DEFAULT_BASELINE)}/turn (default)[/dim]"

    header = f"  Baseline: {cal_str}  │  Active: [bold]{len(active)}[/bold]"

    # Build session lines — one per session, health bar prominent
    session_lines: list[str] = []

    if not active:
        session_lines.append("  [dim]No active sessions[/dim]")
    else:
        # Find max title length for alignment
        titles = []
        for s in active:
            proj = s.project or "?"
            ai = s.ai_title or "Untitled"
            title = f"{proj} > {ai}"[:40]
            titles.append(title)

        for i, s in enumerate(active):
            color = s.efficiency
            pct = s.health_pct
            title = titles[i]
            elapsed = _elapsed(s.started_at)
            waste = s.waste_factor
            dot = f"[{color}]●[/{color}]"

            if s.turns == 0:
                session_lines.append(f"  {dot} {title:<40s}  [dim]waiting...[/dim]")
            elif s.turns < 5:
                turns_str = f"{s.turns} turn{'s' if s.turns != 1 else ''}"
                session_lines.append(f"  {dot} {title:<40s}  {turns_str:>8s}  {elapsed:>5s}  [dim]warming up...[/dim]")
            else:
                bar = _health_bar(pct, color, bar_len=20)
                turns_str = f"{s.turns} turn{'s' if s.turns != 1 else ''}"
                waste_str = f"[{color}]{waste:.1f}x[/{color}]"

                session_lines.append(
                    f"  {dot} {title:<40s}  {turns_str:>8s}  {elapsed:>5s}  "
                    f"[{color}]{pct:>3}%[/{color}] {bar} {waste_str}"
                )

                # Inline detail for unhealthy sessions (only with enough data)
                if pct < 60 and s.turns >= 5:
                    bl = s.effective_baseline
                    cur = _current_avg(s)
                    cost_str = f"~{format_tokens(bl)} → ~{format_tokens(cur)}/turn"
                    if pct < 10:
                        session_lines.append(f"    [red]⚠ BLOCKED — {waste:.0f}x cost ({cost_str})[/red]")
                    elif pct < 30:
                        session_lines.append(f"    [red]⚠ INFLATED — {waste:.0f}x cost ({cost_str})[/red]")
                    else:
                        session_lines.append(f"    [yellow]● warming — {waste:.1f}x cost ({cost_str})[/yellow]")

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
            "  [dim]% = session health (100% fresh, 0% depleted) │ Nx = cost multiplier vs fresh session[/dim]"
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
