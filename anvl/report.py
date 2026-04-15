"""Multi-session report generation."""

from pathlib import Path

from rich.console import Console
from rich.panel import Panel
from rich.table import Table

from .analyzer import SessionMetrics, analyze_session, format_tokens
from .parser import find_project_sessions, parse_session_file

console = Console()

TIER_ICONS = {
    "green": "[green]🟢[/green]",
    "yellow": "[yellow]🟡[/yellow]",
    "red": "[red]🔴[/red]",
    "critical": "[bright_red]⛔[/bright_red]",
}


def generate_report(cwd: Path | None = None) -> None:
    """Analyze all sessions for a project and display a summary report."""
    session_paths = find_project_sessions(cwd)

    if not session_paths:
        console.print("[red]No sessions found for this project.[/red]")
        return

    all_metrics: list[SessionMetrics] = []
    for path in session_paths:
        session = parse_session_file(path)
        metrics = analyze_session(session)
        all_metrics.append(metrics)

    all_metrics.sort(key=lambda m: m.total_input_tokens, reverse=True)

    total_input = sum(m.total_input_tokens for m in all_metrics)
    total_output = sum(m.total_output_tokens for m in all_metrics)

    console.print(
        Panel(
            f"Total sessions: [bold]{len(all_metrics)}[/bold] | "
            f"Total input: [bold]{format_tokens(total_input)}[/bold] | "
            f"Total output: [bold]{format_tokens(total_output)}[/bold]",
            title="ANVL Report",
            border_style="blue",
        )
    )

    table = Table(show_header=True, header_style="bold", expand=True)
    table.add_column("Session", width=10)
    table.add_column("Title", max_width=35)
    table.add_column("Turns", justify="right", width=6)
    table.add_column("Churn", justify="right", width=7)
    table.add_column("Redund.", justify="right", width=7)
    table.add_column("Edits", justify="right", width=6)
    table.add_column("Input", justify="right", width=8)
    table.add_column("Output", justify="right", width=8)
    table.add_column("Tier", justify="center", width=6)

    for m in all_metrics:
        icon = TIER_ICONS.get(m.health_tier, "[white]●[/white]")
        table.add_row(
            m.session_id[:8],
            (m.ai_title or "Untitled")[:35],
            str(m.turn_count),
            f"{m.churn_score}",
            str(m.redundant_read_count),
            str(m.productive_edit_count),
            format_tokens(m.total_input_tokens),
            format_tokens(m.total_output_tokens),
            icon,
        )

    console.print(table)

    if all_metrics:
        most_expensive = all_metrics[0]
        console.print(
            f"\nMost expensive session: [bold]{most_expensive.session_id[:8]}[/bold] "
            f'"{most_expensive.ai_title}" '
            f"({format_tokens(most_expensive.total_input_tokens)} input tokens)"
        )

    churning = [m for m in all_metrics if m.health_tier != "green"]
    if churning:
        console.print(
            f"\n[yellow]⚠ {len(churning)} session(s) with yellow+ churn. "
            f"Rotate with `anvl handoff` earlier to avoid context pollution.[/yellow]"
        )
