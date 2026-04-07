"""ANVL CLI entry point."""

import argparse
import os
import sys
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
from rich.panel import Panel
from rich.table import Table
from rich.text import Text

from .analyzer import SessionMetrics, analyze_session, format_tokens
from .parser import (
    find_active_session,
    find_latest_session,
    find_project_sessions,
    parse_session_file,
)

console = Console(force_terminal=True)

SEMAPHORE_ICONS = {
    "green": "[bold green]●[/bold green]",
    "yellow": "[bold yellow]●[/bold yellow]",
    "red": "[bold red]●[/bold red]",
}

SEMAPHORE_LABELS = {
    "green": "Session is healthy",
    "yellow": "Session is getting inflated",
    "red": "Session is heavily inflated",
}


def cmd_status(args: argparse.Namespace) -> None:
    """Show status of the current/specified session."""
    cwd = Path(args.cwd) if args.cwd else None

    if args.session:
        from .config import find_project_dir

        project_dir = find_project_dir(cwd)
        if project_dir is None:
            console.print("[red]No Claude Code project found for this directory.[/red]")
            sys.exit(1)
        jsonl_path = project_dir / f"{args.session}.jsonl"
        if not jsonl_path.exists():
            console.print(f"[red]Session file not found: {jsonl_path}[/red]")
            sys.exit(1)
    else:
        result = find_active_session(cwd)
        if result is None:
            result = find_latest_session(cwd)
        if result is None:
            console.print("[red]No session found. Run this from a project with Claude Code history.[/red]")
            sys.exit(1)
        jsonl_path, _ = result

    session = parse_session_file(jsonl_path)
    metrics = analyze_session(session)

    if args.json:
        import json

        data = {
            "session_id": metrics.session_id,
            "ai_title": metrics.ai_title,
            "turns": metrics.turn_count,
            "current_waste": round(metrics.current_waste_factor, 1),
            "average_waste": round(metrics.average_waste_factor, 1),
            "total_input": metrics.total_input_tokens,
            "total_output": metrics.total_output_tokens,
            "semaphore": metrics.semaphore,
            "trend": metrics.trend,
        }
        console.print_json(json.dumps(data))
        return

    _print_status(metrics, session)


def _print_status(metrics: SessionMetrics, session) -> None:
    """Render status to terminal with rich."""
    icon = SEMAPHORE_ICONS[metrics.semaphore]
    label = SEMAPHORE_LABELS[metrics.semaphore]
    waste_color = {"green": "green", "yellow": "yellow", "red": "red"}[metrics.semaphore]

    title = f'ANVL Status \u2014 "{metrics.ai_title}"'
    lines = []
    lines.append(f"Session: [cyan]{metrics.session_id[:12]}...[/cyan]")
    lines.append(f"Branch: [dim]{session.git_branch}[/dim] | CWD: [dim]{session.cwd}[/dim]")
    lines.append("")
    lines.append(
        f"Turns: [bold]{metrics.turn_count}[/bold] | "
        f"Total input: [bold]{format_tokens(metrics.total_input_tokens)}[/bold] | "
        f"Total output: [bold]{format_tokens(metrics.total_output_tokens)}[/bold]"
    )
    lines.append(
        f"Current waste: [bold {waste_color}]{metrics.current_waste_factor:.1f}x[/bold {waste_color}] | "
        f"Avg waste: [bold]{metrics.average_waste_factor:.1f}x[/bold] | "
        f"Trend: {metrics.trend}"
    )
    lines.append("")
    lines.append(f"Semaphore: {icon} {label}")

    # Token breakdown per turn (last 10)
    if metrics.per_turn:
        lines.append("")
        recent = metrics.per_turn[-10:]
        table = Table(show_header=True, header_style="bold", box=None, padding=(0, 1))
        table.add_column("Turn", justify="right", style="dim")
        table.add_column("Cache Read", justify="right")
        table.add_column("Cache Create", justify="right")
        table.add_column("Input", justify="right")
        table.add_column("Output", justify="right")
        table.add_column("Waste", justify="right")

        for tm in recent:
            waste_style = "green" if tm.waste_factor < 3 else "yellow" if tm.waste_factor <= 7 else "red"
            tool_marker = " \u2699" if tm.is_tool_only else ""
            table.add_row(
                str(tm.turn_index),
                format_tokens(tm.cache_read),
                format_tokens(tm.cache_creation),
                format_tokens(tm.input_tokens),
                format_tokens(tm.output_tokens),
                f"[{waste_style}]{tm.waste_factor:.1f}x{tool_marker}[/{waste_style}]",
            )

        console.print(Panel("\n".join(lines), title=title, border_style=waste_color))
        console.print(table)
    else:
        console.print(Panel("\n".join(lines), title=title, border_style=waste_color))


def cmd_handoff(args: argparse.Namespace) -> None:
    """Generate handoff.md for the current session."""
    from .handoff import generate_handoff

    cwd = Path(args.cwd) if args.cwd else None
    result = find_active_session(cwd)
    if result is None:
        result = find_latest_session(cwd)
    if result is None:
        console.print("[red]No session found.[/red]")
        sys.exit(1)

    jsonl_path, _ = result
    session = parse_session_file(jsonl_path)
    metrics = analyze_session(session)

    output_path = Path(args.output) if args.output else Path(session.cwd or ".") / "handoff.md"
    generate_handoff(session, metrics, output_path)
    console.print(f"[green]Handoff generated:[/green] {output_path}")


def cmd_monitor(args: argparse.Namespace) -> None:
    """Launch live terminal monitor."""
    from .monitor import monitor_session

    cwd = Path(args.cwd) if args.cwd else None
    result = find_active_session(cwd)
    if result is None:
        result = find_latest_session(cwd)
    if result is None:
        console.print("[red]No session found.[/red]")
        sys.exit(1)

    jsonl_path, _ = result
    monitor_session(jsonl_path, refresh_interval=args.interval)


def cmd_dashboard(args: argparse.Namespace) -> None:
    """Launch web dashboard."""
    from .web.server import start_server

    cwd = Path(args.cwd) if args.cwd else None
    from .config import load_config

    config = load_config()
    port = args.port or config["dashboard_port"]
    start_server(port=port, cwd=cwd)


def cmd_report(args: argparse.Namespace) -> None:
    """Generate report for all project sessions."""
    from .report import generate_report

    cwd = Path(args.cwd) if args.cwd else None
    generate_report(cwd)


def cmd_init(args: argparse.Namespace) -> None:
    """First-time setup: create config and install hook."""
    from .config import save_default_config, ANVL_CONFIG_FILE
    from .hooks import install_hook

    console.print(Panel(
        "[bold]Welcome to ANVL[/bold]\n"
        "Session monitor and handoff tool for Claude Code\n"
        "[dim]Developed by IronDevz[/dim]",
        border_style="blue",
    ))

    # Step 1: Config
    save_default_config()
    console.print(f"  [green]\u2713[/green] Config created at [cyan]{ANVL_CONFIG_FILE}[/cyan]")

    # Step 2: Hook
    install_hook()
    console.print("  [green]\u2713[/green] Hook installed in Claude Code settings")

    # Step 3: Quick start guide
    console.print("")
    console.print(Panel(
        "[bold]Quick start:[/bold]\n\n"
        "  [cyan]anvl status[/cyan]      \u2014 Check current session health\n"
        "  [cyan]anvl sessions[/cyan]    \u2014 See all sessions with usage stats\n"
        "  [cyan]anvl monitor[/cyan]     \u2014 Live terminal monitor\n"
        "  [cyan]anvl dashboard[/cyan]   \u2014 Web dashboard at localhost:3000\n"
        "  [cyan]anvl handoff[/cyan]     \u2014 Generate session summary for rotation\n\n"
        "ANVL will now alert you when a session gets inflated.\n"
        "Green (<3x) \u2192 Yellow (3-7x) \u2192 Red (>7x waste factor)",
        title="Setup complete",
        border_style="green",
    ))


def cmd_install(args: argparse.Namespace) -> None:
    """Install ANVL hook in Claude Code settings."""
    from .hooks import install_hook

    install_hook()
    console.print("[green]\u2713 ANVL hook installed.[/green]")
    console.print("[dim]Tip: Run `anvl init` for full first-time setup.[/dim]")


def cmd_uninstall(args: argparse.Namespace) -> None:
    """Remove ANVL hook from Claude Code settings."""
    from .hooks import uninstall_hook

    uninstall_hook()
    console.print("[green]\u2713 ANVL hook removed.[/green]")


def cmd_sessions(args: argparse.Namespace) -> None:
    """Show all Claude sessions across projects with usage stats."""
    from .sessions import collect_all_sessions
    from .analyzer import format_tokens

    console.print("[dim]Scanning sessions...[/dim]")
    summaries = collect_all_sessions()

    if not summaries:
        console.print("[red]No sessions found.[/red]")
        sys.exit(1)

    # Count active and inflated sessions
    active = [s for s in summaries if s.is_active]
    inflated = [s for s in active if s.waste_factor > 7]

    header_lines = [
        f"Active sessions: [bold]{len(active)}[/bold] | "
        f"Inflated (>7x waste): [bold {'red' if inflated else 'green'}]{len(inflated)}[/bold {'red' if inflated else 'green'}]",
    ]
    if inflated:
        worst = max(inflated, key=lambda s: s.waste_factor)
        header_lines.append(
            f"Worst: [bold red]{worst.project}[/bold red] — {worst.waste_factor:.0f}x waste, "
            f"{worst.turns} turns. Run [bold]anvl handoff[/bold] to rotate."
        )

    border = "red" if inflated else "green"
    console.print(Panel("\n".join(header_lines), title="ANVL — Sessions", border_style=border))

    # Sessions table
    table = Table(show_header=True, header_style="bold", expand=True)
    table.add_column("", width=2)  # active indicator
    table.add_column("Project", max_width=20)
    table.add_column("Title", max_width=30)
    table.add_column("Turns", justify="right", width=6)
    table.add_column("Input", justify="right", width=8)
    table.add_column("Output", justify="right", width=8)
    table.add_column("Started", width=16)

    # Filter sessions
    from datetime import datetime as dt, timezone as tz
    now = dt.now(tz.utc)
    today_start = now.replace(hour=0, minute=0, second=0, microsecond=0)

    filtered = []
    for s in summaries:
        if args.active and not s.is_active:
            continue
        if args.today and s.started_at < today_start:
            continue
        filtered.append(s)

    # Limit display unless --all
    display = filtered if args.show_all else filtered[:20]

    active_count = 0
    for s in display:
        indicator = "[bold green]●[/bold green]" if s.is_active else "[dim]○[/dim]"
        if s.is_active:
            active_count += 1

        started_str = s.started_at.astimezone().strftime("%Y-%m-%d %H:%M")

        table.add_row(
            indicator,
            s.project[:20],
            s.ai_title[:30],
            str(s.turns),
            format_tokens(s.total_input),
            format_tokens(s.total_output),
            started_str,
        )

    console.print(table)
    shown = len(display)
    total = len(filtered)
    extra = f" (showing {shown}/{total}, use --all for full list)" if shown < total else ""
    console.print(f"\n[dim]Active: {active_count} | Total: {total} sessions{extra}[/dim]")


def cmd_hook(args: argparse.Namespace) -> None:
    """Hook entrypoint called by Claude Code (not user-facing)."""
    from .hooks import hook_entrypoint

    hook_entrypoint()


def main() -> None:
    parser = argparse.ArgumentParser(
        prog="anvl",
        description="Session monitor and handoff tool for Claude Code",
    )
    parser.add_argument("--cwd", help="Override working directory")
    subparsers = parser.add_subparsers(dest="command")

    # status
    sp = subparsers.add_parser("status", help="Show current session metrics")
    sp.add_argument("--session", help="Session ID (default: auto-detect)")
    sp.add_argument("--json", action="store_true", help="Output as JSON")

    # handoff
    sp = subparsers.add_parser("handoff", help="Generate handoff.md")
    sp.add_argument("-o", "--output", help="Output file path")

    # monitor
    sp = subparsers.add_parser("monitor", help="Live terminal monitor")
    sp.add_argument("--interval", type=float, default=2.0, help="Refresh interval in seconds")

    # dashboard
    sp = subparsers.add_parser("dashboard", help="Launch web dashboard")
    sp.add_argument("--port", type=int, help="Server port")

    # sessions
    sp = subparsers.add_parser("sessions", help="Show all sessions with usage stats")
    sp.add_argument("--active", action="store_true", help="Show only active sessions")
    sp.add_argument("--today", action="store_true", help="Show only today's sessions")
    sp.add_argument("--all", action="store_true", dest="show_all", help="Show all sessions (default: last 20)")

    # report
    subparsers.add_parser("report", help="Report on all project sessions")

    # init / install / uninstall
    subparsers.add_parser("init", help="First-time setup (config + hook)")
    subparsers.add_parser("install", help="Install ANVL hook in Claude Code")
    subparsers.add_parser("uninstall", help="Remove ANVL hook from Claude Code")

    # hook (hidden - called by Claude Code)
    sp = subparsers.add_parser("hook")
    sp.add_argument("event", choices=["post-tool-use", "user-prompt-submit"])

    args = parser.parse_args()

    if args.command is None:
        parser.print_help()
        sys.exit(0)

    commands = {
        "init": cmd_init,
        "status": cmd_status,
        "handoff": cmd_handoff,
        "monitor": cmd_monitor,
        "dashboard": cmd_dashboard,
        "sessions": cmd_sessions,
        "report": cmd_report,
        "install": cmd_install,
        "uninstall": cmd_uninstall,
        "hook": cmd_hook,
    }
    commands[args.command](args)


if __name__ == "__main__":
    main()
