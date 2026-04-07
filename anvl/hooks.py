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
    """Called by Claude Code on each event (UserPromptSubmit / PostToolUse).

    Computes cumulative session waste and alerts the user with
    conversational messages, as if Claude itself were speaking.
    """
    config = load_config()
    threshold = config.get("waste_threshold", 7)

    cwd = Path.cwd()
    from .parser import find_active_session

    result = find_active_session(cwd)
    if result is None:
        return

    jsonl_path, _ = result

    # Compute cumulative session waste (all turns, not just last message)
    stats = _compute_session_stats(jsonl_path)
    if stats is None:
        return

    turns = stats["turns"]
    total_input = stats["total_input"]
    total_output = stats["total_output"]
    cache_read = stats["last_cache_read"]
    waste = total_input / max(total_output, 1)

    if turns < 2:  # Too early to judge
        return

    if waste <= threshold:
        return

    handoff_threshold = config.get("handoff_waste_threshold", 50)

    if waste > handoff_threshold:
        _auto_handoff(jsonl_path, waste, turns)
    elif waste > 20:
        print(
            f"\n[ANVL] This session is getting expensive ({waste:.0f}x waste, {turns} turns).\n"
            f"   I recommend starting a new conversation soon.\n"
            f"   Run `anvl handoff` to save context, then open a fresh session.\n",
            file=sys.stdout,
        )
    elif waste > threshold:
        print(
            f"\n[ANVL] Session waste is {waste:.1f}x after {turns} turns. Keep an eye on it.\n",
            file=sys.stdout,
        )


def _auto_handoff(jsonl_path: Path, waste: float, turns: int) -> None:
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
            f"[ANVL] This session is critically inflated ({waste:.0f}x waste, {turns} turns).\n"
            f"\n"
            f"Handoff saved: {output_path}\n"
            f"\n"
            f"To continue without wasting tokens:\n"
            f"   1. Open a new Claude Code conversation\n"
            f"   2. Say: Read handoff.md and continue where I left off\n"
            f"\n"
            f"This typically saves 40-60% of your quota.\n"
            f"{'='*60}\n",
            file=sys.stdout,
        )
    except Exception as e:
        print(f"[ANVL] Auto-handoff failed: {e}", file=sys.stderr)


def _compute_session_stats(jsonl_path: Path) -> dict | None:
    """Compute cumulative session stats by scanning all usage records.

    Returns dict with total_input, total_output, turns, last_cache_read.
    Optimized: reads the full file but only parses usage fields.
    """
    total_input = 0
    total_output = 0
    turns = 0
    last_cache_read = 0

    try:
        with open(jsonl_path, "r", encoding="utf-8") as f:
            for line in f:
                line = line.strip()
                if not line:
                    continue
                # Quick filter: only parse lines that look like assistant messages
                if '"type":"assistant"' not in line and '"type": "assistant"' not in line:
                    # Count user turns (non-tool-result)
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

                inp = usage.get("input_tokens", 0)
                cr = usage.get("cache_read_input_tokens", 0)
                cc = usage.get("cache_creation_input_tokens", 0)
                out = usage.get("output_tokens", 0)

                total_input += inp + cr + cc
                total_output += out
                last_cache_read = cr

    except OSError:
        return None

    if total_output == 0:
        return None

    return {
        "total_input": total_input,
        "total_output": total_output,
        "turns": turns,
        "last_cache_read": last_cache_read,
    }
