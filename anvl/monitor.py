"""Live terminal monitor for Claude Code sessions using rich."""

import time
from pathlib import Path

from rich.console import Console
from rich.layout import Layout
from rich.live import Live
from rich.panel import Panel
from rich.table import Table
from rich.text import Text

from .analyzer import SessionMetrics, analyze_session, format_tokens
from .parser import SessionData, parse_session_file


def build_header(metrics: SessionMetrics, session: SessionData) -> Panel:
    """Build header panel with session info and waste gauge."""
    semaphore_colors = {"green": "green", "yellow": "yellow", "red": "red"}
    color = semaphore_colors[metrics.semaphore]

    # Waste gauge bar
    max_waste_display = 20.0
    filled = min(int(metrics.current_waste_factor / max_waste_display * 20), 20)
    bar = "\u2588" * filled + "\u2591" * (20 - filled)

    lines = [
        f"Session: [cyan]{metrics.session_id[:12]}...[/cyan] | "
        f'"{metrics.ai_title}"',
        f"Branch: [dim]{session.git_branch}[/dim] | Turns: [bold]{metrics.turn_count}[/bold]",
        "",
        f"Waste: [{color}]{bar}[/{color}] [{color}][bold]{metrics.current_waste_factor:.1f}x[/bold][/{color}]"
        f"  (avg: {metrics.average_waste_factor:.1f}x, trend: {metrics.trend})",
        "",
        f"Input: [bold]{format_tokens(metrics.total_input_tokens)}[/bold] | "
        f"Output: [bold]{format_tokens(metrics.total_output_tokens)}[/bold] | "
        f"Cache read: [bold]{format_tokens(metrics.total_cache_read)}[/bold]",
    ]
    return Panel("\n".join(lines), title="ANVL Monitor", border_style=color)


def build_turn_table(metrics: SessionMetrics) -> Table:
    """Build token usage table for recent turns."""
    table = Table(
        title="Token Usage per Turn (recent)",
        show_header=True,
        header_style="bold",
        expand=True,
    )
    table.add_column("Turn", justify="right", style="dim", width=5)
    table.add_column("Cache Read", justify="right", width=12)
    table.add_column("Bar", width=30)
    table.add_column("Output", justify="right", width=10)
    table.add_column("Waste", justify="right", width=10)

    recent = metrics.per_turn[-15:]
    if not recent:
        return table

    max_cache_read = max((t.cache_read for t in recent), default=1) or 1

    for tm in recent:
        bar_len = int(tm.cache_read / max_cache_read * 25)
        bar_color = "green" if tm.waste_factor < 3 else "yellow" if tm.waste_factor <= 7 else "red"
        bar = f"[{bar_color}]{'\u2588' * bar_len}{'░' * (25 - bar_len)}[/{bar_color}]"

        waste_style = bar_color
        tool_marker = " \u2699" if tm.is_tool_only else ""

        table.add_row(
            str(tm.turn_index),
            format_tokens(tm.cache_read),
            bar,
            format_tokens(tm.output_tokens),
            f"[{waste_style}]{tm.waste_factor:.1f}x{tool_marker}[/{waste_style}]",
        )

    return table


def monitor_session(session_path: Path, refresh_interval: float = 2.0) -> None:
    """Main monitor loop with rich Live display."""
    console = Console()
    last_mtime = 0.0

    console.print(f"[dim]Monitoring: {session_path}[/dim]")
    console.print("[dim]Press Ctrl+C to exit[/dim]\n")

    session = parse_session_file(session_path)
    metrics = analyze_session(session)

    with Live(console=console, refresh_per_second=1) as live:
        try:
            while True:
                current_mtime = session_path.stat().st_mtime
                if current_mtime != last_mtime:
                    last_mtime = current_mtime
                    session = parse_session_file(session_path)
                    metrics = analyze_session(session)

                # Build display
                header = build_header(metrics, session)
                table = build_turn_table(metrics)

                from rich.console import Group
                live.update(Group(header, table))

                time.sleep(refresh_interval)
        except KeyboardInterrupt:
            console.print("\n[dim]Monitor stopped.[/dim]")
