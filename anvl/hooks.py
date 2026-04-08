"""Hook management for Claude Code integration."""

import json
import sys
from pathlib import Path

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


def hook_entrypoint() -> None:
    """Called by Claude Code on UserPromptSubmit / PostToolUse.

    Uses Clauditor-style waste: current_tokens_per_turn / baseline_tokens_per_turn.
    Alerts when the session is inflating. Blocks at critical levels.
    """
    config = load_config()
    block_threshold = config.get("handoff_waste_threshold", 10)
    min_turns = config.get("min_turns_for_alert", 5)

    cwd = Path.cwd()
    from .parser import find_active_session

    result = find_active_session(cwd)
    if result is None:
        return

    jsonl_path, _ = result
    turns_data = _collect_turn_tokens(jsonl_path)
    if not turns_data or len(turns_data) < min_turns:
        return

    # Compute waste: avg last 5 / min first 5
    window = 5
    baseline_min = min(turns_data[:window])
    current = turns_data[-window:]
    current_avg = sum(current) / len(current)

    if baseline_min == 0:
        return

    waste = current_avg / baseline_min
    turns = len(turns_data)
    health_pct = min(100, max(0, int(100 * (block_threshold - waste) / (block_threshold - 1)))) if waste > 1 else 100

    if waste < 2:
        return

    if waste >= block_threshold and turns >= 20:
        # Critical: auto-handoff + block session
        _auto_handoff(jsonl_path, turns)
        sys.exit(2)
    elif health_pct < 30:
        # Red zone: strong warning, generate handoff
        _generate_handoff_quiet(jsonl_path)
        print(
            f"\n[ANVL] This session is inflated ({waste:.0f}x). Your work has been saved to handoff.md\n"
            '       Start a new conversation and say: "Read handoff.md and continue where I left off"\n',
            file=sys.stdout,
        )
    elif health_pct < 60:
        # Yellow zone: informational
        print(
            f"\n[ANVL] Session health: {health_pct}% ({waste:.1f}x waste). "
            "Consider starting a new conversation soon.\n",
            file=sys.stdout,
        )


def _generate_handoff_quiet(jsonl_path: Path) -> Path | None:
    """Generate handoff.md without printing anything."""
    try:
        from .parser import parse_session_file
        from .analyzer import analyze_session
        from .handoff import generate_handoff

        session = parse_session_file(jsonl_path)
        metrics = analyze_session(session)
        output_path = Path(session.cwd or ".") / "handoff.md"
        generate_handoff(session, metrics, output_path)
        return output_path
    except Exception:
        return None


def _auto_handoff(jsonl_path: Path, turns: int) -> None:
    """Auto-generate handoff and print blocking message."""
    output_path = _generate_handoff_quiet(jsonl_path)

    if output_path:
        print(
            "\n" + "=" * 60 + "\n"
            "[ANVL] Session blocked -- too inflated to continue efficiently.\n"
            "\n"
            f"Handoff saved: {output_path}\n"
            "\n"
            "Start a new conversation and say:\n"
            '  "Read handoff.md and continue where I left off"\n'
            "\n"
            "=" * 60 + "\n",
            file=sys.stdout,
        )
    else:
        print(
            "\n[ANVL] Session blocked -- too inflated to continue efficiently.\n"
            "       Start a new conversation to save quota.\n",
            file=sys.stdout,
        )


def _collect_turn_tokens(jsonl_path: Path) -> list[int]:
    """Collect total tokens per user turn from JSONL (fast scan).

    Returns a list where each entry is the total tokens for that turn.
    Deduplicates by requestId to avoid double-counting streaming chunks.
    Groups assistant usage records by the preceding user turn.
    """
    turn_totals: list[int] = []
    current_turn_tokens = 0
    request_usage: dict[str, int] = {}
    in_turn = False

    try:
        with open(jsonl_path, "r", encoding="utf-8") as f:
            for line in f:
                line = line.strip()
                if not line:
                    continue

                # Detect user turns (not tool results)
                if '"type":"user"' in line or '"type": "user"' in line:
                    if '"tool_use_id"' not in line:
                        # Save previous turn
                        if in_turn:
                            total = current_turn_tokens + sum(request_usage.values())
                            if total > 0:
                                turn_totals.append(total)
                        current_turn_tokens = 0
                        request_usage = {}
                        in_turn = True
                    continue

                if not ('"type":"assistant"' in line or '"type": "assistant"' in line):
                    continue

                try:
                    record = json.loads(line)
                except json.JSONDecodeError:
                    continue

                usage = record.get("message", {}).get("usage")
                if not usage:
                    continue

                inp = usage.get("input_tokens", 0)
                cr = usage.get("cache_read_input_tokens", 0)
                cc = usage.get("cache_creation_input_tokens", 0)
                out = usage.get("output_tokens", 0)
                total = inp + cr + cc + out

                request_id = record.get("requestId", "")
                if request_id:
                    request_usage[request_id] = total
                else:
                    current_turn_tokens += total

    except OSError:
        return []

    # Don't forget the last turn
    if in_turn:
        total = current_turn_tokens + sum(request_usage.values())
        if total > 0:
            turn_totals.append(total)

    return turn_totals
