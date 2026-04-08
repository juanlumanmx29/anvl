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


def hook_entrypoint() -> None:
    """Called by Claude Code on UserPromptSubmit / PostToolUse.

    Computes session health and alerts the user with clear messages.
    Blocks the session (exit code 2) when critically inflated.
    """
    config = load_config()
    threshold = config.get("waste_threshold", 2)
    handoff_threshold = config.get("handoff_waste_threshold", 10)

    cwd = Path.cwd()
    from .parser import find_active_session

    result = find_active_session(cwd)
    if result is None:
        return

    jsonl_path, _ = result
    stats = _compute_session_stats(jsonl_path)
    if stats is None:
        return

    turns = stats["turns"]
    waste = _compute_waste(stats)

    if turns < 2 or waste <= threshold:
        return

    if waste > handoff_threshold:
        # Critical: auto-handoff + block session
        _auto_handoff(jsonl_path, turns)
        sys.exit(2)
    elif waste > 5:
        # Red: strong warning, generate handoff
        _generate_handoff_quiet(jsonl_path)
        print(
            "\n[ANVL] This session is inflated. Your work has been saved to handoff.md\n"
            '       Start a new conversation and say: "Read handoff.md and continue where I left off"\n',
            file=sys.stdout,
        )
    elif waste > threshold:
        # Yellow: informational
        print(
            "\n[ANVL] This session is getting expensive. Consider starting a new conversation soon.\n",
            file=sys.stdout,
        )


def _compute_waste(stats: dict) -> float:
    """Compute cost-weighted waste from session stats."""
    weighted_input = (
        stats["total_raw_input"] * 1.0
        + stats["total_cache_read"] * 0.1
        + stats["total_cache_creation"] * 1.25
    )
    weighted_output = stats["total_output"] * 5.0
    return weighted_input / max(weighted_output, 1)


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


def _compute_session_stats(jsonl_path: Path) -> dict | None:
    """Compute cumulative session stats by scanning all usage records.

    Deduplicates by requestId to avoid double-counting streaming chunks.
    """
    total_input = 0
    total_output = 0
    total_raw_input = 0
    total_cache_read = 0
    total_cache_creation = 0
    turns = 0
    request_usage: dict[str, dict] = {}

    try:
        with open(jsonl_path, "r", encoding="utf-8") as f:
            for line in f:
                line = line.strip()
                if not line:
                    continue
                if '"type":"assistant"' not in line and '"type": "assistant"' not in line:
                    if '"type":"user"' in line or '"type": "user"' in line:
                        if '"tool_use_id"' not in line:
                            turns += 1
                    continue

                try:
                    record = json.loads(line)
                except json.JSONDecodeError:
                    continue

                msg = record.get("message", {})
                usage = msg.get("usage")
                if not usage:
                    continue

                request_id = record.get("requestId", "")
                if request_id:
                    request_usage[request_id] = usage
                else:
                    inp = usage.get("input_tokens", 0)
                    cr = usage.get("cache_read_input_tokens", 0)
                    cc = usage.get("cache_creation_input_tokens", 0)
                    out = usage.get("output_tokens", 0)
                    total_input += inp + cr + cc
                    total_output += out
                    total_raw_input += inp
                    total_cache_read += cr
                    total_cache_creation += cc

    except OSError:
        return None

    # Sum deduplicated usage
    for usage in request_usage.values():
        inp = usage.get("input_tokens", 0)
        cr = usage.get("cache_read_input_tokens", 0)
        cc = usage.get("cache_creation_input_tokens", 0)
        out = usage.get("output_tokens", 0)
        total_input += inp + cr + cc
        total_output += out
        total_raw_input += inp
        total_cache_read += cr
        total_cache_creation += cc

    if total_output == 0:
        return None

    return {
        "total_input": total_input,
        "total_output": total_output,
        "total_raw_input": total_raw_input,
        "total_cache_read": total_cache_read,
        "total_cache_creation": total_cache_creation,
        "turns": turns,
    }
