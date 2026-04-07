"""Hook management for Claude Code integration."""

import json
import sys
from pathlib import Path

from .config import CLAUDE_HOME, load_config

SETTINGS_PATH = CLAUDE_HOME / "settings.json"
ANVL_HOOK_COMMAND = "anvl hook user-prompt-submit"


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


def _find_anvl_hook_index(post_tool_use: list) -> int | None:
    """Find the index of the ANVL hook entry in PostToolUse array."""
    for i, entry in enumerate(post_tool_use):
        hooks_list = entry.get("hooks", [])
        for hook in hooks_list:
            if hook.get("command", "") == ANVL_HOOK_COMMAND:
                return i
    return None


def install_hook() -> None:
    """Add ANVL PostToolUse hook to settings.json. Idempotent."""
    settings = _read_settings()

    if "hooks" not in settings:
        settings["hooks"] = {}
    if "PostToolUse" not in settings["hooks"]:
        settings["hooks"]["PostToolUse"] = []

    post_tool_use = settings["hooks"]["PostToolUse"]

    if _find_anvl_hook_index(post_tool_use) is not None:
        print("ANVL hook is already installed.", file=sys.stderr)
        return

    anvl_entry = {
        "matcher": "",
        "hooks": [
            {
                "type": "command",
                "command": ANVL_HOOK_COMMAND,
            }
        ],
    }
    post_tool_use.append(anvl_entry)
    _write_settings(settings)


def uninstall_hook() -> None:
    """Remove ANVL hook from settings.json."""
    settings = _read_settings()

    post_tool_use = settings.get("hooks", {}).get("PostToolUse", [])
    if not post_tool_use:
        print("No ANVL hook found to remove.", file=sys.stderr)
        return

    idx = _find_anvl_hook_index(post_tool_use)
    if idx is None:
        print("No ANVL hook found to remove.", file=sys.stderr)
        return

    post_tool_use.pop(idx)
    _write_settings(settings)


def hook_entrypoint() -> None:
    """Called by Claude Code after each tool use.

    Shows conversation health and auto-generates handoff when critically inflated.
    """
    config = load_config()
    threshold = config.get("waste_threshold", 7)

    cwd = Path.cwd()
    from .parser import find_active_session

    result = find_active_session(cwd)
    if result is None:
        return

    jsonl_path, _ = result

    # Fast path: read only the tail
    usage = _read_last_usage(jsonl_path)
    if usage is None:
        return

    input_tokens = usage.get("input_tokens", 0)
    cache_read = usage.get("cache_read_input_tokens", 0)
    cache_creation = usage.get("cache_creation_input_tokens", 0)
    output = max(usage.get("output_tokens", 0), 1)

    total_input = input_tokens + cache_read + cache_creation
    waste = total_input / output

    if output <= 1:  # Skip tool-only turns
        return

    if waste <= threshold:
        return

    # Calculate conversation health based on context growth
    # Cache read growing = context is getting bigger
    cache_ratio = cache_read / max(total_input, 1)
    context_health = _estimate_context_health(cache_read, cache_creation, output)

    handoff_threshold = config.get("handoff_waste_threshold", 100)

    if waste > handoff_threshold:
        _auto_handoff(jsonl_path, waste, context_health)
    elif context_health == "critical":
        print(
            f"\n\U0001f534 ANVL: Conversation critically inflated ({waste:.0f}x waste)\n"
            f"   Context is {cache_read:,} tokens and growing.\n"
            f"   Run: anvl handoff\n"
            f"   Then start a new conversation with the handoff file.\n",
            file=sys.stdout,
        )
    elif context_health == "warning":
        print(
            f"\U0001f7e1 ANVL: Session inflating ({waste:.1f}x waste, cache: {cache_read:,} tokens)\n"
            f"   Consider running `anvl handoff` soon.\n",
            file=sys.stdout,
        )


def _estimate_context_health(cache_read: int, cache_creation: int, output: int) -> str:
    """Estimate how close the conversation is to needing a restart.

    Based on cache read size — bigger context = more tokens per turn.
    """
    if cache_read > 150_000:
        return "critical"  # Context is huge, definitely restart
    elif cache_read > 80_000:
        return "warning"   # Getting large
    elif cache_read > 40_000:
        return "elevated"  # Starting to grow
    return "healthy"


def _auto_handoff(jsonl_path: Path, waste: float, health: str) -> None:
    """Auto-generate handoff.md and print clear restart instructions."""
    try:
        from .parser import parse_session_file
        from .analyzer import analyze_session
        from .handoff import generate_handoff

        session = parse_session_file(jsonl_path)
        metrics = analyze_session(session)
        output_path = Path(session.cwd or ".") / "handoff.md"
        generate_handoff(session, metrics, output_path)

        print(
            f"\n{'='*60}\n"
            f"\U0001f6a8 ANVL: Session critically inflated ({waste:.0f}x waste)\n"
            f"\n"
            f"\U0001f4be Handoff saved: {output_path}\n"
            f"\n"
            f"\U0001f449 To continue:\n"
            f"   1. Open a new Claude Code conversation\n"
            f"   2. Say: Read handoff.md and continue where I left off\n"
            f"      or drag handoff.md into the chat\n"
            f"\n"
            f"   This saves ~40-60% of your quota per session.\n"
            f"{'='*60}\n",
            file=sys.stdout,
        )
    except Exception as e:
        print(f"\u26a0\ufe0f ANVL: Auto-handoff failed: {e}", file=sys.stderr)


def _read_last_usage(jsonl_path: Path) -> dict | None:
    """Read the last assistant message with usage data from the JSONL file."""
    try:
        file_size = jsonl_path.stat().st_size
        read_size = min(file_size, 10240)

        with open(jsonl_path, "rb") as f:
            f.seek(max(0, file_size - read_size))
            tail = f.read().decode("utf-8", errors="replace")

        lines = tail.strip().split("\n")
        for line in reversed(lines):
            try:
                record = json.loads(line)
            except json.JSONDecodeError:
                continue

            if record.get("type") != "assistant":
                continue

            msg = record.get("message", {})
            usage = msg.get("usage")
            if usage and usage.get("output_tokens", 0) > 0:
                return usage

    except OSError:
        pass

    return None
