"""Live terminal monitor for Claude Code sessions using rich."""

import os
import sys
import time
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

from rich.console import Console
from rich.live import Live
from rich.panel import Panel
from rich.table import Table

from .analyzer import SessionMetrics, analyze_session, format_tokens
from .config import load_config
from .parser import SessionData, parse_session_file
from .sessions import collect_all_sessions


HEALTH_LABELS = {
    "green": "Healthy",
    "yellow": "Inflating",
    "red": "Critical",
}


def build_monitor_panel(
    metrics: SessionMetrics, session: SessionData, config: dict
) -> Panel:
    """Build a compact monitor panel with semaphore health."""
    color = metrics.semaphore
    label = HEALTH_LABELS[color]

    title = metrics.ai_title or "Untitled"
    if len(title) > 45:
        title = title[:42] + "..."

    lines = [
        "",
        f'  Session: [bold]"{title}"[/bold]',
        f"  Branch: [dim]{session.git_branch or '-'}[/dim]  |  "
        f"Turns: [bold]{metrics.turn_count}[/bold]  |  "
        f"Input: [bold]{format_tokens(metrics.total_input_tokens)}[/bold]  |  "
        f"Output: [bold]{format_tokens(metrics.total_output_tokens)}[/bold]",
        "",
        f"  Session Health:  [{color}][bold]{label}[/bold][/{color}]",
        "",
    ]

    return Panel(
        "\n".join(lines),
        title="ANVL - IronDevz",
        subtitle="Ctrl+C to exit",
        border_style=color,
    )


def build_sessions_table() -> Table:
    """Build a compact table of all active sessions."""
    summaries = collect_all_sessions()
    active = [s for s in summaries if s.is_active]

    table = Table(
        title="Active Sessions",
        show_header=True,
        header_style="bold",
        expand=True,
        border_style="dim",
    )
    table.add_column("", width=2)
    table.add_column("Project", max_width=18)
    table.add_column("Title", max_width=30)
    table.add_column("Turns", justify="right", width=6)
    table.add_column("Health", width=12)

    if not active:
        table.add_row("", "[dim]No active sessions[/dim]", "", "", "")
        return table

    for s in active:
        color = s.efficiency
        label = HEALTH_LABELS.get(color, "?")
        dot = f"[{color}]*[/{color}]"
        health_str = f"[{color}]{label}[/{color}]"

        table.add_row(
            dot,
            s.project[:18],
            (s.ai_title or "Untitled")[:30],
            str(s.turns),
            health_str,
        )

    return table


def monitor_session(session_path: Path, refresh_interval: float = 2.0) -> None:
    """Main monitor loop with rich Live display."""
    console = Console()
    config = load_config()
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

                panel = build_monitor_panel(metrics, session, config)
                sessions_table = build_sessions_table()

                from rich.console import Group
                live.update(Group(panel, sessions_table))

                time.sleep(refresh_interval)
        except KeyboardInterrupt:
            console.print("\n[dim]Monitor stopped.[/dim]")
