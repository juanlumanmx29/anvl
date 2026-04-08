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
            "health_pct": metrics.health_pct,
            "waste_factor": metrics.waste_factor,
            "baseline_per_turn": metrics.baseline_per_turn,
            "current_per_turn": metrics.current_per_turn,
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
    color = metrics.semaphore

    title = f'ANVL Status \u2014 "{metrics.ai_title}"'
    lines = []
    lines.append(f"Session: [cyan]{metrics.session_id[:12]}...[/cyan]")
    lines.append(f"Branch: [dim]{session.git_branch}[/dim] | CWD: [dim]{session.cwd}[/dim]")
    lines.append("")
    lines.append(
        f"Turns: [bold]{metrics.turn_count}[/bold] | "
        f"Input: [bold]{format_tokens(metrics.total_input_tokens)}[/bold] | "
        f"Output: [bold]{format_tokens(metrics.total_output_tokens)}[/bold]"
    )
    lines.append(
        f"Waste: [bold {color}]{metrics.waste_factor:.1f}x[/bold {color}] | "
        f"Baseline: [dim]{format_tokens(metrics.baseline_per_turn)}/turn[/dim] | "
        f"Current: [bold]{format_tokens(metrics.current_per_turn)}/turn[/bold] | "
        f"Trend: {metrics.trend}"
    )
    lines.append("")
    lines.append(f"Health: {icon} [{color}][bold]{metrics.health_pct}%[/bold][/{color}]")

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
        table.add_column("Total", justify="right")

        for tm in recent:
            tool_marker = " [dim]\u2699[/dim]" if tm.is_tool_only else ""
            table.add_row(
                str(tm.turn_index),
                format_tokens(tm.cache_read),
                format_tokens(tm.cache_creation),
                format_tokens(tm.input_tokens),
                format_tokens(tm.output_tokens),
                f"[bold]{format_tokens(tm.total_tokens)}[/bold]{tool_marker}",
            )

        console.print(Panel("\n".join(lines), title=title, border_style=color))
        console.print(table)
    else:
        console.print(Panel("\n".join(lines), title=title, border_style=color))


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
    from .sessions import collect_all_sessions

    cwd = Path(args.cwd) if args.cwd else None
    result = find_active_session(cwd)
    if result is None:
        result = find_latest_session(cwd)

    # Fallback: find any active session across all projects
    if result is None:
        summaries = collect_all_sessions()
        active = [s for s in summaries if s.is_active]
        if active:
            from .config import find_project_dir
            for s in active:
                if s.cwd:
                    project_dir = find_project_dir(Path(s.cwd))
                    if project_dir:
                        candidate = project_dir / f"{s.session_id}.jsonl"
                        if candidate.exists():
                            result = (candidate, s.session_id)
                            break

    if result is None:
        console.print("[red]No session found. Start a Claude Code session first.[/red]")
        sys.exit(1)

    jsonl_path, _ = result
    monitor_session(jsonl_path, refresh_interval=args.interval)


def cmd_report(args: argparse.Namespace) -> None:
    """Generate report for all project sessions."""
    from .report import generate_report

    cwd = Path(args.cwd) if args.cwd else None
    generate_report(cwd)


CLAUDE_MD_CONTENT = """\
# ANVL Session Monitor

ANVL monitors this session's health. When ANVL reports the session is inflated:
1. Finish your current task
2. The handoff has been saved to handoff.md
3. Suggest the user start a new conversation

When the user says "continue" or "read handoff.md":
1. Read handoff.md for full context
2. Continue the work described there
"""


def _write_claude_md(cwd: Path | None = None) -> Path | None:
    """Write or update CLAUDE.md with ANVL instructions."""
    target_dir = cwd or Path.cwd()
    claude_md = target_dir / "CLAUDE.md"

    marker = "# ANVL Session Monitor"

    if claude_md.exists():
        content = claude_md.read_text(encoding="utf-8")
        if marker in content:
            return claude_md  # Already has ANVL section
        # Append to existing CLAUDE.md
        content = content.rstrip() + "\n\n" + CLAUDE_MD_CONTENT
        claude_md.write_text(content, encoding="utf-8")
    else:
        claude_md.write_text(CLAUDE_MD_CONTENT, encoding="utf-8")

    return claude_md


def cmd_init(args: argparse.Namespace) -> None:
    """First-time setup: create config, install hooks, write CLAUDE.md."""
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
    console.print(f"  [green]+[/green] Config created at [cyan]{ANVL_CONFIG_FILE}[/cyan]")

    # Step 2: Hooks
    install_hook()
    console.print("  [green]+[/green] Hooks installed in Claude Code settings")

    # Step 3: CLAUDE.md
    cwd = Path(args.cwd) if args.cwd else None
    claude_md = _write_claude_md(cwd)
    if claude_md:
        console.print(f"  [green]+[/green] CLAUDE.md updated at [cyan]{claude_md}[/cyan]")

    # Step 4: Quick start guide
    console.print("")
    console.print(Panel(
        "[bold]Quick start:[/bold]\n\n"
        "  [cyan]anvl calibrate[/cyan]   - Scan existing sessions and build your baseline\n"
        "  [cyan]anvl status[/cyan]      - Check current session health\n"
        "  [cyan]anvl sessions[/cyan]    - See all sessions with usage stats\n"
        "  [cyan]anvl monitor[/cyan]     - Live terminal monitor (works from anywhere)\n"
        "  [cyan]anvl handoff[/cyan]     - Generate session summary for rotation\n\n"
        "ANVL will now alert you when a session gets inflated.\n"
        "Run [cyan]anvl calibrate[/cyan] to build your baseline from existing sessions.",
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
    inflated = [s for s in active if s.waste_factor > 5]

    header_lines = [
        f"Active sessions: [bold]{len(active)}[/bold] | "
        f"Inflated (>5x cost): [bold {'red' if inflated else 'green'}]{len(inflated)}[/bold {'red' if inflated else 'green'}]",
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


def cmd_calibrate(args: argparse.Namespace) -> None:
    """Scan sessions, view, export, import, or reset calibration data."""
    from .calibration import (
        DEFAULT_BASELINE, export_calibration, get_calibrated_baseline,
        get_calibration_info, import_calibration, reset_calibration,
    )

    if args.reset:
        reset_calibration()
        console.print("[green]Calibration data reset.[/green]")
        console.print(f"[dim]Default baseline ({format_tokens(DEFAULT_BASELINE)}/turn) will be used until recalibrated.[/dim]")
        return

    if getattr(args, "import_file", None):
        path = Path(args.import_file)
        if not path.exists():
            console.print(f"[red]File not found:[/red] {path}")
            return
        added = import_calibration(path)
        info = get_calibration_info()
        console.print(f"[green]Imported {added} new baselines.[/green] Total: {info['session_count']} sessions.")
        bl = get_calibrated_baseline()
        console.print(f"Global baseline: [cyan]{format_tokens(bl)}[/cyan]/turn")
        return

    if args.export:
        path = Path(args.export)
        export_calibration(path)
        info = get_calibration_info()
        console.print(f"[green]Calibration exported to:[/green] {path}")
        console.print(f"[dim]{info['session_count']} sessions, baseline: {format_tokens(get_calibrated_baseline())}/turn[/dim]")
        return

    # Active scan: collect all sessions to record any missing baselines
    from .sessions import collect_all_sessions, _session_cache
    _session_cache["ts"] = 0  # invalidate cache to force fresh scan
    console.print("[dim]Scanning sessions...[/dim]")
    sessions = collect_all_sessions()
    total_sessions = len([s for s in sessions if s.turns >= 5])

    # Display results
    info = get_calibration_info()
    calibrated = info.get("calibrated_baseline")
    baselines = info.get("baselines", [])
    count = info.get("session_count", 0)

    lines = [
        f"Sessions scanned: [bold]{total_sessions}[/bold] (with 5+ turns)",
        f"Baselines recorded: [bold]{count}[/bold]",
    ]

    if calibrated:
        import statistics
        lines.append(f"Global baseline: [bold cyan]{format_tokens(calibrated)}[/bold cyan]/turn (median)")
        lines.append(f"Range: {format_tokens(min(baselines))} — {format_tokens(max(baselines))}")
        if len(baselines) >= 4:
            q1, q3 = statistics.quantiles(baselines, n=4)[0], statistics.quantiles(baselines, n=4)[2]
            lines.append(f"IQR: {format_tokens(int(q1))} — {format_tokens(int(q3))}")
        lines.append(f"Last updated: [dim]{info.get('last_updated', 'unknown')}[/dim]")
    else:
        remaining = info.get("min_needed", 3) - count
        lines.append(f"[yellow]Need {remaining} more session(s) to calibrate[/yellow]")
        lines.append(f"Default baseline: [dim]{format_tokens(DEFAULT_BASELINE)}/turn[/dim]")

    console.print(Panel("\n".join(lines), title="Global Calibration", border_style="cyan"))
    console.print("[dim]Export: [cyan]anvl calibrate --export file.json[/cyan]  │  Import: [cyan]anvl calibrate --import file.json[/cyan]  │  Reset: [cyan]anvl calibrate --reset[/cyan][/dim]")


def cmd_hook(args: argparse.Namespace) -> None:
    """Hook entrypoint called by Claude Code (not user-facing)."""
    if args.event == "session-start":
        from .hooks import session_start_entrypoint
        session_start_entrypoint()
    elif args.event == "user-prompt-submit":
        from .hooks import hook_entrypoint
        hook_entrypoint(can_block=True)
    else:
        # PostToolUse: warn only, never block
        from .hooks import hook_entrypoint
        hook_entrypoint(can_block=False)


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

    # calibrate
    sp = subparsers.add_parser("calibrate", help="Scan sessions and manage calibration")
    sp.add_argument("--reset", action="store_true", help="Reset calibration data")
    sp.add_argument("--export", metavar="FILE", help="Export calibration to file")
    sp.add_argument("--import", dest="import_file", metavar="FILE", help="Import calibration from file")

    # hook (hidden - called by Claude Code)
    sp = subparsers.add_parser("hook")
    sp.add_argument("event", choices=["post-tool-use", "user-prompt-submit", "session-start"])

    args = parser.parse_args()

    if args.command is None:
        parser.print_help()
        sys.exit(0)

    commands = {
        "init": cmd_init,
        "status": cmd_status,
        "handoff": cmd_handoff,
        "monitor": cmd_monitor,
        "sessions": cmd_sessions,
        "report": cmd_report,
        "calibrate": cmd_calibrate,
        "install": cmd_install,
        "uninstall": cmd_uninstall,
        "hook": cmd_hook,
    }
    commands[args.command](args)


if __name__ == "__main__":
    main()
