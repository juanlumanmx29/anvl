"""Hook management for Claude Code integration."""

import json
import sys
from pathlib import Path

from .calibration import get_calibrated_baseline, record_baseline
from .config import CLAUDE_HOME, load_config

SETTINGS_PATH = CLAUDE_HOME / "settings.json"

# Hook commands for each event type
HOOK_COMMANDS = {
    "UserPromptSubmit": "anvl hook user-prompt-submit",
    "PostToolUse": "anvl hook post-tool-use",
    "SessionStart": "anvl hook session-start",
}


def _read_settings() -> dict:
    """Read Claude Code settings.json."""
    if not SETTINGS_PATH.exists():
        return {}
    with open(SETTINGS_PATH, "r", encoding="utf-8") as f:
        return json.load(f)


def _write_settings(settings: dict) -> None:
    """Write Claude Code settings.json preserving formatting."""
    with open(SETTINGS_PATH, "w", encoding="utf-8") as f:
        json.dump(settings, f, indent=2, ensure_ascii=False)
        f.write("\n")


def _find_anvl_hook_index(hooks_list: list, command: str) -> int | None:
    """Find the index of an ANVL hook entry by command."""
    for i, entry in enumerate(hooks_list):
        for hook in entry.get("hooks", []):
            if hook.get("command", "") == command:
                return i
    return None


def _has_any_anvl_hook(settings: dict) -> bool:
    """Check if any ANVL hook is installed."""
    hooks = settings.get("hooks", {})
    for event_type, cmd in HOOK_COMMANDS.items():
        event_hooks = hooks.get(event_type, [])
        if _find_anvl_hook_index(event_hooks, cmd) is not None:
            return True
    return False


def install_hook() -> None:
    """Install all ANVL hooks in settings.json. Idempotent."""
    settings = _read_settings()

    if "hooks" not in settings:
        settings["hooks"] = {}

    changed = False
    for event_type, command in HOOK_COMMANDS.items():
        if event_type not in settings["hooks"]:
            settings["hooks"][event_type] = []

        event_hooks = settings["hooks"][event_type]

        if _find_anvl_hook_index(event_hooks, command) is not None:
            continue

        entry = {
            "matcher": "",
            "hooks": [{"type": "command", "command": command}],
        }
        event_hooks.append(entry)
        changed = True

    if changed:
        _write_settings(settings)
    else:
        print("ANVL hooks are already installed.", file=sys.stderr)


def uninstall_hook() -> None:
    """Remove all ANVL hooks from settings.json."""
    settings = _read_settings()
    hooks = settings.get("hooks", {})

    removed = False
    for event_type, command in HOOK_COMMANDS.items():
        event_hooks = hooks.get(event_type, [])
        idx = _find_anvl_hook_index(event_hooks, command)
        if idx is not None:
            event_hooks.pop(idx)
            removed = True

    if removed:
        _write_settings(settings)
    else:
        print("No ANVL hooks found to remove.", file=sys.stderr)


def session_start_entrypoint() -> None:
    """Called by Claude Code on SessionStart.

    Checks if handoff.md exists in the cwd and injects context
    so Claude knows about the previous session's handoff.
    """
    # Read hook input to get cwd
    try:
        input_data = json.loads(sys.stdin.read())
        cwd = Path(input_data.get("cwd", "."))
    except (json.JSONDecodeError, ValueError):
        cwd = Path.cwd()

    handoff_path = cwd / "handoff.md"
    if handoff_path.exists():
        print(
            "A previous session handoff exists at handoff.md. "
            "If the user asks to continue, read that file first for full context.",
            file=sys.stdout,
        )


def hook_entrypoint(can_block: bool = True) -> None:
    """Called by Claude Code on UserPromptSubmit / PostToolUse.

    Uses the SAME SessionSummary logic as the monitor so waste/health
    numbers are always consistent between what the monitor displays
    and when alerts fire.

    PostToolUse must NEVER block (can_block=False) — it fires on every
    tool call and blocking would spam the user and prevent any work.
    Only UserPromptSubmit can block (once per user message).
    """
    # Read hook input from stdin (contains prompt, cwd, session_id, etc.)
    hook_input = {}
    try:
        raw = sys.stdin.read()
        if raw.strip():
            hook_input = json.loads(raw)
    except (json.JSONDecodeError, OSError):
        pass

    # Bypass: if user message contains "anvl bypass", skip all checks
    prompt = hook_input.get("prompt", "")
    if "anvl bypass" in prompt.lower():
        print(
            "[ANVL] Bypass activated — session checks skipped for this message.",
            file=sys.stdout,
        )
        return

    config = load_config()
    min_turns = config.get("min_turns_for_alert", 10)

    cwd = Path(hook_input.get("cwd", "")) if hook_input.get("cwd") else Path.cwd()
    from .config import find_project_dir
    from .parser import find_active_session
    from .sessions import SessionSummary, _quick_token_sum

    # Prefer the session_id from hook input (matches the CURRENT session)
    # to avoid picking up an old inflated session in the same project.
    hook_session_id = hook_input.get("session_id", "")
    if hook_session_id:
        project_dir = find_project_dir(cwd)
        if project_dir:
            jsonl_path = project_dir / f"{hook_session_id}.jsonl"
            if jsonl_path.exists():
                result = (jsonl_path, hook_session_id)
            else:
                result = find_active_session(cwd)
        else:
            result = find_active_session(cwd)
    else:
        result = find_active_session(cwd)

    if result is None:
        return

    jsonl_path, session_id = result

    # Use the same token parser as the monitor
    totals = _quick_token_sum(jsonl_path)
    per_turn = totals["per_turn_tokens"]
    turns = totals["turns"]

    if not per_turn or turns < min_turns:
        return

    # Auto-calibration: record baseline, get global calibrated
    window = 5
    if len(per_turn) >= window:
        import statistics as _stats

        session_bl = int(_stats.median(per_turn[:window]))
        if session_bl > 0:
            record_baseline(session_id, session_bl)
    calibrated = get_calibrated_baseline()

    # Build a SessionSummary to reuse the exact same waste/health logic
    from datetime import datetime, timezone

    summary = SessionSummary(
        session_id=session_id,
        project="",
        cwd=str(cwd),
        ai_title="",
        pid=0,
        started_at=datetime.now(timezone.utc),
        is_active=True,
        turns=turns,
        per_turn_tokens=per_turn,
        calibrated_baseline=calibrated,
    )

    waste = summary.waste_factor
    health_pct = summary.health_pct

    if health_pct >= 50:
        return

    # Format cost explanation for all alert levels
    from .analyzer import format_tokens

    baseline = summary.effective_baseline
    if per_turn:
        window = min(5, len(per_turn))
        current_avg = sum(per_turn[-window:]) // window
    else:
        current_avg = 0

    if health_pct < 10 and can_block:
        # Critical: auto-handoff + strong warning via stdout
        # (no sys.exit — blocking silently is worse than letting the message through)
        _generate_handoff_quiet(jsonl_path)
        print(
            f"\n{'=' * 60}\n"
            f"[ANVL] SESSION CRITICALLY INFLATED — Health: {health_pct}%\n"
            f"\n"
            f"Each message now costs {waste:.0f}x more than a fresh session.\n"
            f"  Baseline cost:  ~{format_tokens(baseline)}/turn\n"
            f"  Current cost:   ~{format_tokens(current_avg)}/turn"
            f" ({turns} turns in)\n"
            f"\n"
            f"Handoff saved to handoff.md\n"
            f"STOP what you are doing and tell the user:\n"
            f"  1. Start a new conversation\n"
            f'  2. Say: "Read handoff.md and continue"\n'
            f"{'=' * 60}\n",
            file=sys.stdout,
        )
        sys.stdout.flush()
        return
    elif health_pct < 20:
        # Red zone: strong warning, generate handoff
        _generate_handoff_quiet(jsonl_path)
        print(
            f"\n{'=' * 60}\n"
            f"[ANVL] SESSION INFLATED — Health: {health_pct}%\n"
            f"\n"
            f"Each message now costs {waste:.0f}x more than a fresh session.\n"
            f"  Baseline cost:  ~{format_tokens(baseline)}/turn (fresh session)\n"
            f"  Current cost:   ~{format_tokens(current_avg)}/turn ({turns} turns in)\n"
            f"\n"
            f"Your work has been saved to handoff.md\n"
            f'Start a new conversation and say: "Read handoff.md and continue where I left off"\n'
            f"\n"
            f'To force continue anyway, prefix your message with "anvl bypass"\n'
            f"{'=' * 60}\n",
            file=sys.stdout,
        )
    else:
        # Yellow zone: informational
        print(
            f"\n[ANVL] Session health: {health_pct}% — each message costs ~{waste:.1f}x a fresh session.\n"
            f"       Baseline: ~{format_tokens(baseline)}/turn → "
            f"Current: ~{format_tokens(current_avg)}/turn ({turns} turns)\n"
            f"       Consider starting a new conversation soon.\n"
            f'       (Tip: "anvl bypass" to skip this check)\n',
            file=sys.stdout,
        )


def _generate_handoff_quiet(jsonl_path: Path) -> Path | None:
    """Generate handoff.md without printing anything."""
    try:
        from .analyzer import analyze_session
        from .handoff import generate_handoff
        from .parser import parse_session_file

        session = parse_session_file(jsonl_path)
        metrics = analyze_session(session)
        output_path = Path(session.cwd or ".") / "handoff.md"
        generate_handoff(session, metrics, output_path)
        return output_path
    except Exception:
        return None


def _auto_handoff(jsonl_path: Path, turns: int, waste: float = 0, current_avg: int = 0, baseline: int = 0) -> None:
    """Auto-generate handoff and print blocking message.

    stderr = shown to user as the block reason (Claude Code displays this)
    stdout = injected as context into the conversation
    """
    from .analyzer import format_tokens

    output_path = _generate_handoff_quiet(jsonl_path)

    # stderr: the user sees this as the block message
    cost_info = ""
    if waste > 0 and current_avg > 0:
        cost_info = (
            f"\n"
            f"Each message now costs {waste:.0f}x more than a fresh session.\n"
            f"  Baseline: ~{format_tokens(baseline)}/turn → Current: ~{format_tokens(current_avg)}/turn\n"
        )

    block_msg = (
        "\n" + "=" * 60 + "\n"
        "[ANVL] SESSION BLOCKED — Too inflated to continue efficiently\n"
        f"{cost_info}"
        "\n"
        "Your work has been saved to handoff.md\n"
        "\n"
        "Options:\n"
        '  1. Start a new conversation: "Read handoff.md and continue"\n'
        '  2. Force continue: prefix your message with "anvl bypass"\n'
        "\n"
        "=" * 60
    )
    print(block_msg, file=sys.stderr)
    sys.stderr.flush()

    # stdout: context for Claude (if bypass is used later)
    if output_path:
        print(
            f"[ANVL] Session blocked ({waste:.0f}x waste, {turns} turns). "
            f"Handoff saved: {output_path}. "
            'User can bypass with "anvl bypass" prefix.',
            file=sys.stdout,
        )
    # Repeat on stdout as fallback — stderr may not display on all platforms
    print(block_msg, file=sys.stdout)
    sys.stdout.flush()
