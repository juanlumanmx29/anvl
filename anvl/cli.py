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

from .analyzer import SessionMetrics, analyze_session, format_tokens
from .branding import (
    CYAN,
    DIM,
    styled_banner,
    styled_subtitle,
    styled_tagline,
    version_text,
)
from .parser import (
    find_active_session,
    find_latest_session,
    parse_session_file,
)

console = Console(force_terminal=True)

TIER_ICONS = {
    "green": "[bold green]🟢[/bold green]",
    "yellow": "[bold yellow]🟡[/bold yellow]",
    "red": "[bold red]🔴[/bold red]",
    "critical": "[bold bright_red]⛔[/bold bright_red]",
}
TIER_COLORS = {
    "green": "green",
    "yellow": "yellow",
    "red": "red",
    "critical": "bright_red",
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
            "churn_score": metrics.churn_score,
            "churn_tier": metrics.churn_tier,
            "churn_reason": metrics.churn_reason,
            "context_tokens": metrics.context_tokens,
            "context_pct": metrics.context_pct,
            "context_tier": metrics.context_tier,
            "health_tier": metrics.health_tier,
            "health_reason": metrics.health_reason,
            "redundant_reads": metrics.redundant_read_count,
            "productive_edits": metrics.productive_edit_count,
            "baseline_per_turn": metrics.baseline_per_turn,
            "current_per_turn": metrics.current_per_turn,
            "inflation_ratio": metrics.inflation_ratio,
            "total_input": metrics.total_input_tokens,
            "total_output": metrics.total_output_tokens,
            "trend": metrics.trend,
        }
        console.print_json(json.dumps(data))
        return

    _print_status(metrics, session)


def _print_status(metrics: SessionMetrics, session) -> None:
    """Render status to terminal with rich."""
    icon = TIER_ICONS.get(metrics.health_tier, "●")
    color = TIER_COLORS.get(metrics.health_tier, "white")

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
    churn_color = TIER_COLORS.get(metrics.churn_tier, "white")
    ctx_color = TIER_COLORS.get(metrics.context_tier, "white")
    lines.append(
        f"Health: {icon} [{color}][bold]{metrics.health_tier.upper()}[/bold][/{color}]  "
        f"[dim]({metrics.health_reason})[/dim]"
    )
    lines.append(
        f"Churn: [bold {churn_color}]{metrics.churn_score}[/bold {churn_color}] "
        f"[dim]({metrics.churn_tier})[/dim] | "
        f"Context: [bold {ctx_color}]{int(metrics.context_pct * 100)}%[/bold {ctx_color}] "
        f"[dim]({format_tokens(metrics.context_tokens)} / 200K)[/dim]"
    )
    lines.append(
        f"Baseline: [dim]{format_tokens(metrics.baseline_per_turn)}/turn[/dim] | "
        f"Current: [bold]{format_tokens(metrics.current_per_turn)}/turn[/bold] | "
        f"Inflation: {metrics.inflation_ratio}x | "
        f"Trend: {metrics.trend}"
    )

    if metrics.most_reread_files:
        lines.append("")
        top = ", ".join(f"{Path(p).name}({n})" for p, n in metrics.most_reread_files[:5])
        lines.append(f"Most re-read: [dim]{top}[/dim]")

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
    """Generate a handoff for the current session (auto-saved under .anvl/handoffs/)."""
    from .handoff import generate_handoff

    cwd = Path(args.cwd) if args.cwd else Path.cwd()
    result = find_active_session(cwd)
    if result is None:
        result = find_latest_session(cwd)
    if result is None:
        console.print("[red]No session found.[/red]")
        sys.exit(1)

    jsonl_path, _ = result
    session = parse_session_file(jsonl_path)
    if not session.cwd:
        session.cwd = str(cwd)
    metrics = analyze_session(session)

    output_path = generate_handoff(session, metrics, project_dir=cwd)
    console.print(f"[green]Handoff saved:[/green] {output_path}")
    console.print("[dim]CLAUDE.md index updated.[/dim]")


def cmd_handoffs(args: argparse.Namespace) -> None:
    """List, show, or archive handoffs for the current project."""
    from .handoff import archive_handoff, list_handoffs

    cwd = Path(args.cwd) if args.cwd else Path.cwd()

    if args.handoffs_action == "archive":
        if not args.session_short:
            console.print("[red]Usage: anvl handoffs archive <session_short>[/red]")
            sys.exit(1)
        result = archive_handoff(cwd, args.session_short)
        if result:
            console.print(f"[green]Archived:[/green] {result}")
        else:
            console.print(f"[red]No handoff found for session {args.session_short}[/red]")
        return

    if args.handoffs_action == "show":
        if not args.session_short:
            console.print("[red]Usage: anvl handoffs show <session_short>[/red]")
            sys.exit(1)
        handoffs = list_handoffs(cwd, include_archived=True)
        for h in handoffs:
            if h.session_short == args.session_short:
                console.print(h.path.read_text(encoding="utf-8"))
                return
        console.print(f"[red]No handoff found for session {args.session_short}[/red]")
        return

    # list (default)
    handoffs = list_handoffs(cwd, include_archived=args.all)
    if not handoffs:
        console.print("[dim]No handoffs in this project.[/dim]")
        return

    table = Table(show_header=True, header_style="bold")
    table.add_column("Status", width=8)
    table.add_column("When", width=18)
    table.add_column("Session", width=10)
    table.add_column("Title", max_width=40)
    table.add_column("Turns", justify="right", width=6)
    table.add_column("Churn", justify="right", width=6)
    for h in handoffs:
        when = h.generated_at[:16].replace("T", " ") if h.generated_at else "-"
        status = "[green]active[/green]" if h.status == "active" else "[dim]archived[/dim]"
        table.add_row(status, when, h.session_short, h.ai_title[:40], str(h.turns), str(h.churn))
    console.print(table)


def cmd_monitor(args: argparse.Namespace) -> None:
    """Launch live terminal monitor."""
    from .monitor import monitor_session

    monitor_session(refresh_interval=args.interval)


def cmd_report(args: argparse.Namespace) -> None:
    """Generate report for all project sessions."""
    from .report import generate_report

    cwd = Path(args.cwd) if args.cwd else None
    generate_report(cwd)


CLAUDE_MD_CONTENT = """\
# ANVL Session Monitor

ANVL tracks session health via the **churn** metric (redundant file reads
vs productive edits). When ANVL reports churn climbing:

1. Finish your current task
2. Suggest the user start a new conversation — the handoff is saved automatically

When handoffs exist in `.anvl/handoffs/`:
1. Read the relevant handoff file for full context
2. Continue the work described there

See the `## Active handoffs` section below (auto-managed by ANVL) for the
current index of saved handoffs.
"""


def _write_claude_md(cwd: Path | None = None) -> Path | None:
    """Write or update CLAUDE.md with ANVL instructions."""
    target_dir = cwd or Path.cwd()
    claude_md = target_dir / "CLAUDE.md"

    marker = "# ANVL Session Monitor"

    if claude_md.exists():
        content = claude_md.read_text(encoding="utf-8")
        if marker in content:
            return claude_md
        content = content.rstrip() + "\n\n" + CLAUDE_MD_CONTENT
        claude_md.write_text(content, encoding="utf-8")
    else:
        claude_md.write_text(CLAUDE_MD_CONTENT, encoding="utf-8")

    # Also refresh the handoff index block
    from .handoff import update_claude_md_index

    update_claude_md_index(target_dir)
    return claude_md


def cmd_init(args: argparse.Namespace) -> None:
    """First-time setup."""
    from rich.align import Align
    from rich.console import Group
    from rich.text import Text

    from .config import ANVL_CONFIG_FILE, save_default_config
    from .hooks import install_hook

    console.print(
        Panel(
            Group(
                Align.center(styled_banner()),
                Align.center(styled_tagline()),
                Text(""),
                Align.center(Text("Session monitor and handoff tool for Claude Code", style=DIM)),
            ),
            border_style=CYAN,
        )
    )

    save_default_config()
    console.print(f"  [green]+[/green] Config created at [cyan]{ANVL_CONFIG_FILE}[/cyan]")

    install_hook()
    console.print("  [green]+[/green] Hooks installed in Claude Code settings")

    cwd = Path(args.cwd) if args.cwd else None
    claude_md = _write_claude_md(cwd)
    if claude_md:
        console.print(f"  [green]+[/green] CLAUDE.md updated at [cyan]{claude_md}[/cyan]")

    console.print("")
    console.print(
        Panel(
            "[bold]Quick start:[/bold]\n\n"
            "  [cyan]anvl status[/cyan]      - Check current session health (churn)\n"
            "  [cyan]anvl sessions[/cyan]    - See all sessions with usage stats\n"
            "  [cyan]anvl monitor[/cyan]     - Live terminal monitor (works from anywhere)\n"
            "  [cyan]anvl handoff[/cyan]     - Save handoff for current session\n"
            "  [cyan]anvl handoffs[/cyan]    - List / show / archive saved handoffs\n\n"
            "ANVL auto-saves a handoff on every user message and alerts you when\n"
            "a session becomes churny (reading the same files repeatedly).",
            title="[bold]Setup complete[/bold]",
            border_style=CYAN,
        )
    )


def cmd_install(args: argparse.Namespace) -> None:
    from .hooks import install_hook

    install_hook()
    console.print("[green]\u2713 ANVL hook installed.[/green]")


def cmd_uninstall(args: argparse.Namespace) -> None:
    from .hooks import uninstall_hook

    uninstall_hook()
    console.print("[green]\u2713 ANVL hook removed.[/green]")


def cmd_sessions(args: argparse.Namespace) -> None:
    """Show all Claude sessions across projects with usage stats."""
    from .sessions import collect_all_sessions

    console.print("[dim]Scanning sessions...[/dim]")
    summaries = collect_all_sessions()

    if not summaries:
        console.print("[red]No sessions found.[/red]")
        sys.exit(1)

    active = [s for s in summaries if s.is_active]
    churning = [s for s in active if s.health_tier != "green"]

    color = "red" if churning else "green"
    header_lines = [
        f"Active sessions: [bold]{len(active)}[/bold] | Churning: [bold {color}]{len(churning)}[/bold {color}]",
    ]
    if churning:
        worst = max(churning, key=lambda s: s.churn_score)
        header_lines.append(
            f"Worst: [bold red]{worst.project}[/bold red] — churn {worst.churn_score} ({worst.health_tier}), "
            f"{worst.turns} turns. Run [bold]anvl handoff[/bold] and rotate."
        )

    border = "red" if churning else "green"
    console.print(Panel("\n".join(header_lines), title="[bold]ANVL — Sessions[/bold]", border_style=border))

    table = Table(show_header=True, header_style="bold", expand=True)
    table.add_column("", width=2)
    table.add_column("Project", max_width=20)
    table.add_column("Title", max_width=30)
    table.add_column("Turns", justify="right", width=6)
    table.add_column("Churn", justify="right", width=6)
    table.add_column("Ctx", justify="right", width=5)
    table.add_column("Input", justify="right", width=8)
    table.add_column("Output", justify="right", width=8)
    table.add_column("Started", width=16)

    from datetime import datetime as dt
    from datetime import timezone as tz

    now = dt.now(tz.utc)
    today_start = now.replace(hour=0, minute=0, second=0, microsecond=0)

    filtered = []
    for s in summaries:
        if args.active and not s.is_active:
            continue
        if args.today and s.started_at < today_start:
            continue
        filtered.append(s)

    display = filtered if args.show_all else filtered[:20]

    active_count = 0
    for s in display:
        indicator = "[bold green]●[/bold green]" if s.is_active else "[dim]○[/dim]"
        if s.is_active:
            active_count += 1

        started_str = s.started_at.astimezone().strftime("%Y-%m-%d %H:%M")
        churn_c = TIER_COLORS.get(s.churn_tier, "white")
        ctx_c = TIER_COLORS.get(s.context_tier, "white")

        table.add_row(
            indicator,
            s.project[:20],
            s.ai_title[:30],
            str(s.turns),
            f"[{churn_c}]{s.churn_score}[/{churn_c}]",
            f"[{ctx_c}]{int(s.context_pct * 100)}%[/{ctx_c}]",
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
    if args.event == "session-start":
        from .hooks import session_start_entrypoint

        session_start_entrypoint()
    elif args.event == "user-prompt-submit":
        from .hooks import hook_entrypoint

        hook_entrypoint(can_block=True)
    else:
        from .hooks import post_tool_use_entrypoint

        post_tool_use_entrypoint()


COMMANDS_HELP = [
    ("init", "First-time setup (config + hooks + CLAUDE.md)"),
    ("status", "Show current session health (churn)"),
    ("sessions", "List all sessions with usage stats"),
    ("monitor", "Live terminal dashboard"),
    ("handoff", "Save handoff for current session"),
    ("handoffs", "List / show / archive saved handoffs"),
    ("report", "Generate report for all project sessions"),
    ("install", "Install ANVL hook in Claude Code"),
    ("uninstall", "Remove ANVL hook from Claude Code"),
]


def _print_styled_help() -> None:
    from rich.align import Align
    from rich.console import Group
    from rich.text import Text

    banner_group = Group(
        Align.center(styled_banner()),
        Align.center(styled_tagline()),
        Align.center(styled_subtitle()),
    )
    console.print(Panel(banner_group, border_style=CYAN))

    lines = Text()
    lines.append("  Commands:\n\n", style="bold white")
    for cmd, desc in COMMANDS_HELP:
        lines.append(f"    anvl {cmd:<14s}", style=f"bold {CYAN}")
        lines.append(f"{desc}\n", style=DIM)
    lines.append("\n  Options:\n\n", style="bold white")
    lines.append("    --cwd DIR         ", style=f"bold {CYAN}")
    lines.append("Override working directory\n", style=DIM)
    lines.append("    --version         ", style=f"bold {CYAN}")
    lines.append("Show version and exit\n", style=DIM)
    lines.append("\n  Run ", style=DIM)
    lines.append("anvl <command> --help", style=f"bold {CYAN}")
    lines.append(" for command-specific options.\n", style=DIM)
    console.print(lines)


def main() -> None:
    parser = argparse.ArgumentParser(
        prog="anvl",
        description="Session monitor and handoff tool for Claude Code",
        add_help=False,
    )
    parser.add_argument("--cwd", help="Override working directory")
    parser.add_argument("--version", action="store_true", help="Show version")
    parser.add_argument("-h", "--help", action="store_true", help="Show help")
    subparsers = parser.add_subparsers(dest="command")

    sp = subparsers.add_parser("status", help="Show current session metrics")
    sp.add_argument("--session", help="Session ID (default: auto-detect)")
    sp.add_argument("--json", action="store_true", help="Output as JSON")

    sp = subparsers.add_parser("handoff", help="Save handoff for current session")

    sp = subparsers.add_parser("handoffs", help="List / show / archive handoffs")
    sp.add_argument(
        "handoffs_action",
        nargs="?",
        default="list",
        choices=["list", "show", "archive"],
        help="Action to perform",
    )
    sp.add_argument("session_short", nargs="?", help="Session short id (for show/archive)")
    sp.add_argument("--all", action="store_true", help="Include archived handoffs")

    sp = subparsers.add_parser("monitor", help="Live terminal monitor")
    sp.add_argument("--interval", type=float, default=2.0, help="Refresh interval in seconds")

    sp = subparsers.add_parser("sessions", help="Show all sessions with usage stats")
    sp.add_argument("--active", action="store_true", help="Show only active sessions")
    sp.add_argument("--today", action="store_true", help="Show only today's sessions")
    sp.add_argument("--all", action="store_true", dest="show_all", help="Show all sessions (default: last 20)")

    subparsers.add_parser("report", help="Report on all project sessions")
    subparsers.add_parser("init", help="First-time setup (config + hook)")
    subparsers.add_parser("install", help="Install ANVL hook in Claude Code")
    subparsers.add_parser("uninstall", help="Remove ANVL hook from Claude Code")

    sp = subparsers.add_parser("hook")
    sp.add_argument("event", choices=["post-tool-use", "user-prompt-submit", "session-start"])

    args = parser.parse_args()

    if args.version:
        console.print(version_text())
        sys.exit(0)

    if args.command is None or args.help:
        _print_styled_help()
        sys.exit(0)

    commands = {
        "init": cmd_init,
        "status": cmd_status,
        "handoff": cmd_handoff,
        "handoffs": cmd_handoffs,
        "monitor": cmd_monitor,
        "sessions": cmd_sessions,
        "report": cmd_report,
        "install": cmd_install,
        "uninstall": cmd_uninstall,
        "hook": cmd_hook,
    }
    commands[args.command](args)


if __name__ == "__main__":
    main()
