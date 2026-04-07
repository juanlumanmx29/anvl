"""Multi-session report generation."""

from pathlib import Path

from rich.console import Console
from rich.panel import Panel
from rich.table import Table

from .analyzer import SessionMetrics, analyze_session, format_tokens
from .parser import find_project_sessions, parse_session_file

console = Console()


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

    # Sort by total input tokens descending
    all_metrics.sort(key=lambda m: m.total_input_tokens, reverse=True)

    total_input = sum(m.total_input_tokens for m in all_metrics)
    total_output = sum(m.total_output_tokens for m in all_metrics)

    # Header
    console.print(
        Panel(
            f"Total sessions: [bold]{len(all_metrics)}[/bold] | "
            f"Total input: [bold]{format_tokens(total_input)}[/bold] | "
            f"Total output: [bold]{format_tokens(total_output)}[/bold]",
            title="ANVL Report",
            border_style="blue",
        )
    )

    # Sessions table
    table = Table(show_header=True, header_style="bold", expand=True)
    table.add_column("Session", width=10)
    table.add_column("Title", max_width=35)
    table.add_column("Turns", justify="right", width=6)
    table.add_column("Avg Waste", justify="right", width=10)
    table.add_column("Peak Waste", justify="right", width=10)
    table.add_column("Input", justify="right", width=8)
    table.add_column("Output", justify="right", width=8)
    table.add_column("Semaphore", justify="center", width=10)

    for m in all_metrics:
        peak_waste = max((t.waste_factor for t in m.per_turn if not t.is_tool_only), default=0)
        semaphore_icon = {
            "green": "[green]\u2b24[/green]",
            "yellow": "[yellow]\u2b24[/yellow]",
            "red": "[red]\u2b24[/red]",
        }[m.semaphore]

        table.add_row(
            m.session_id[:8],
            m.ai_title[:35] if m.ai_title else "Untitled",
            str(m.turn_count),
            f"{m.average_waste_factor:.1f}x",
            f"{peak_waste:.0f}x",
            format_tokens(m.total_input_tokens),
            format_tokens(m.total_output_tokens),
            semaphore_icon,
        )

    console.print(table)

    # Most expensive session
    if all_metrics:
        most_expensive = all_metrics[0]
        console.print(
            f"\nMost expensive session: [bold]{most_expensive.session_id[:8]}[/bold] "
            f'"{most_expensive.ai_title}" '
            f"({format_tokens(most_expensive.total_input_tokens)} input tokens)"
        )

    # Recommendation
    high_waste = [m for m in all_metrics if m.average_waste_factor > 100]
    if high_waste:
        console.print(
            f"\n[yellow]\u26a0 {len(high_waste)} session(s) averaging >100x waste. "
            f"Use `anvl handoff` earlier to reduce token consumption.[/yellow]"
        )
