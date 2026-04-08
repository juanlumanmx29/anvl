"""Live terminal monitor for Claude Code sessions using rich."""

import os
import sys
import time
from datetime import datetime, timezone
from pathlib import Path

# Ensure UTF-8 output on Windows
if sys.platform == "win32":
    os.environ.setdefault("PYTHONIOENCODING", "utf-8")
    if hasattr(sys.stdout, "reconfigure"):
        try:
            sys.stdout.reconfigure(encoding="utf-8")
            sys.stderr.reconfigure(encoding="utf-8")
        except Exception:
            pass

from rich.console import Console, Group
from rich.live import Live
from rich.panel import Panel
from rich.table import Table
from rich.text import Text

from .analyzer import format_tokens
from .calibration import get_calibrated_baseline
from .config import load_config, path_to_slug
from .sessions import collect_all_sessions, compute_savings


def _health_bar(pct: int, color: str, bar_len: int = 15) -> str:
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


def build_monitor_display() -> Group:
    """Build the unified monitor display — one table with all session info."""
    summaries = collect_all_sessions()
    active = [s for s in summaries if s.is_active]

    # Header stats
    total_input = sum(s.total_input for s in active)
    total_output = sum(s.total_output for s in active)
    mature = [s for s in active if s.turns >= 5]
    worst_waste = max((s.waste_factor for s in mature), default=0)
    worst_health = min((s.health_pct for s in mature), default=100)
    worst_color = "green" if worst_health >= 60 else ("yellow" if worst_health >= 30 else "red")

    worst_str = f"[{worst_color}][bold]{worst_waste:.1f}x[/bold][/{worst_color}]" if mature else "[dim]--[/dim]"
    header = (
        f"  Active: [bold]{len(active)}[/bold]  │  "
        f"Total Input: [bold]{format_tokens(total_input)}[/bold]  │  "
        f"Total Output: [bold]{format_tokens(total_output)}[/bold]  │  "
        f"Worst: {worst_str}"
    )

    # Sessions table
    table = Table(
        show_header=True,
        header_style="bold",
        expand=True,
        border_style="dim",
        pad_edge=True,
        padding=(0, 1),
    )
    table.add_column("", width=1)
    table.add_column("Project", max_width=14, no_wrap=True)
    table.add_column("Title", max_width=22, no_wrap=True)
    table.add_column("Turns", justify="right", width=5)
    table.add_column("Input", justify="right", width=7)
    table.add_column("Output", justify="right", width=7)
    table.add_column("Baseline", justify="right", width=8)
    table.add_column("Current", justify="right", width=8)
    table.add_column("Waste", justify="right", width=5)
    table.add_column("Health", width=22, no_wrap=True)
    table.add_column("Time", justify="right", width=5)

    if not active:
        table.add_row(
            "", "[dim]No active sessions[/dim]",
            "", "", "", "", "", "", "", "", "",
        )
    else:
        for s in active:
            color = s.efficiency
            pct = s.health_pct
            too_new = s.turns < 5
            dot = f"[{color}]●[/{color}]"

            if too_new:
                health_str = "[dim]  waiting…  --[/dim]"
                waste_str = "[dim] --[/dim]"
            else:
                bar = _health_bar(pct, color, bar_len=10)
                health_str = f"{bar} [{color}]{pct:>3}%[/{color}]"
                waste_str = f"[{color}]{s.waste_factor:.1f}x[/{color}]"

            # Baseline info
            bl = s.effective_baseline
            calibrated = s.calibrated_baseline
            if calibrated and calibrated > 0:
                bl_str = f"[cyan]{format_tokens(bl)}[/cyan]"  # cyan = calibrated
            elif bl > 0:
                bl_str = f"{format_tokens(bl)}"
            else:
                bl_str = "[dim]-[/dim]"

            # Current avg (last 5 turns)
            window = 5
            if len(s.per_turn_tokens) >= window:
                current_avg = sum(s.per_turn_tokens[-window:]) // window
                cur_str = format_tokens(current_avg)
            elif s.per_turn_tokens:
                current_avg = sum(s.per_turn_tokens) // len(s.per_turn_tokens)
                cur_str = format_tokens(current_avg)
            else:
                cur_str = "[dim]-[/dim]"

            elapsed = _elapsed(s.started_at)

            table.add_row(
                dot,
                s.project[:14],
                (s.ai_title or "Untitled")[:22],
                str(s.turns),
                format_tokens(s.total_input),
                format_tokens(s.total_output),
                bl_str,
                cur_str,
                waste_str,
                health_str,
                f"[dim]{elapsed}[/dim]",
            )

    # Savings footer
    savings = compute_savings(summaries)
    saved_pct = savings["pct_saved"]

    parts: list = []

    # Wrap header + table in a panel
    panel_content = Group(Text.from_markup(header), Text(""), table)
    border_color = worst_color if active else "dim"
    panel = Panel(
        panel_content,
        title="ANVL Monitor — IronDevz",
        subtitle="[dim]Ctrl+C to exit  │  Refreshes every 2s  │  [cyan]Cyan[/cyan] baseline = calibrated[/dim]",
        border_style=border_color,
    )
    parts.append(panel)

    if saved_pct > 0:
        parts.append(Text.from_markup(
            f"  [dim]Estimated savings from session rotation: [green]{saved_pct:.0f}%[/green] quota saved[/dim]"
        ))

    return Group(*parts)


def monitor_session(session_path: Path, refresh_interval: float = 2.0) -> None:
    """Main monitor loop with rich Live display."""
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
