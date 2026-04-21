"""Hook management for Claude Code integration — churn-based alerts."""

import json
import sys
from pathlib import Path

from .config import CLAUDE_HOME

SETTINGS_PATH = CLAUDE_HOME / "settings.json"

HOOK_COMMANDS = {
    "UserPromptSubmit": "anvl hook user-prompt-submit",
    "PostToolUse": "anvl hook post-tool-use",
    "SessionStart": "anvl hook session-start",
}


def _read_settings() -> dict:
    if not SETTINGS_PATH.exists():
        return {}
    with open(SETTINGS_PATH, "r", encoding="utf-8") as f:
        return json.load(f)


def _write_settings(settings: dict) -> None:
    with open(SETTINGS_PATH, "w", encoding="utf-8") as f:
        json.dump(settings, f, indent=2, ensure_ascii=False)
        f.write("\n")


def _find_anvl_hook_index(hooks_list: list, command: str) -> int | None:
    for i, entry in enumerate(hooks_list):
        for hook in entry.get("hooks", []):
            if hook.get("command", "") == command:
                return i
    return None


def install_hook() -> None:
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
        event_hooks.append({"matcher": "", "hooks": [{"type": "command", "command": command}]})
        changed = True

    if changed:
        _write_settings(settings)
    else:
        print("ANVL hooks are already installed.", file=sys.stderr)


def uninstall_hook() -> None:
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
    """On SessionStart: point Claude to the handoff index if one exists."""
    try:
        input_data = json.loads(sys.stdin.read())
        cwd = Path(input_data.get("cwd", "."))
    except (json.JSONDecodeError, ValueError):
        cwd = Path.cwd()

    from .handoff import list_handoffs, migrate_legacy_handoff, update_claude_md_index

    migrate_legacy_handoff(cwd)
    update_claude_md_index(cwd)

    handoffs = list_handoffs(cwd)
    if handoffs:
        msg = (
            f"{len(handoffs)} previous ANVL handoff(s) in this project "
            f"(see CLAUDE.md for the index). If the user asks to continue, "
            f"read the most relevant one from .anvl/handoffs/ first."
        )
        print(msg, file=sys.stdout)


def _auto_save_handoff(jsonl_path: Path, cwd: Path) -> Path | None:
    """Parse the session and write/refresh its handoff file. Fails silently."""
    try:
        from .analyzer import analyze_session
        from .handoff import generate_handoff
        from .parser import parse_session_file

        session = parse_session_file(jsonl_path)
        if not session.session_id:
            session.session_id = jsonl_path.stem
        if not session.cwd:
            session.cwd = str(cwd)
        metrics = analyze_session(session)
        return generate_handoff(session, metrics, project_dir=cwd)
    except Exception:
        return None


def post_tool_use_entrypoint() -> None:
    """PostToolUse: intentional no-op. All work happens on UserPromptSubmit."""
    return


def hook_entrypoint(can_block: bool = True) -> None:
    """UserPromptSubmit hook.

    1. Parse the current session and compute churn.
    2. Always refresh `.anvl/handoffs/<current>.md` (free, local).
    3. If churn ≥ yellow, print an alert and refresh CLAUDE.md index.
    4. The 'anvl bypass' escape hatch skips everything.
    """
    hook_input = {}
    try:
        raw = sys.stdin.read()
        if raw.strip():
            hook_input = json.loads(raw)
    except (json.JSONDecodeError, OSError):
        pass

    prompt = hook_input.get("prompt", "")
    if "anvl bypass" in prompt.lower():
        print("[ANVL] Bypass activated — session checks skipped.", file=sys.stdout)
        return

    cwd = Path(hook_input.get("cwd", "")) if hook_input.get("cwd") else Path.cwd()

    from .config import find_project_dir
    from .handoff import migrate_legacy_handoff
    from .parser import find_active_session

    # One-shot legacy migration (safe to call every turn)
    migrate_legacy_handoff(cwd)

    hook_session_id = hook_input.get("session_id", "")
    result = None
    if hook_session_id:
        project_dir = find_project_dir(cwd)
        if project_dir:
            jsonl_path = project_dir / f"{hook_session_id}.jsonl"
            if jsonl_path.exists():
                result = (jsonl_path, hook_session_id)
    if result is None:
        result = find_active_session(cwd)
    if result is None:
        return

    jsonl_path, session_id = result

    # Always auto-save the handoff for this session (free, local)
    handoff_path = _auto_save_handoff(jsonl_path, cwd)

    # Compute churn + context pressure to decide if we should alert
    from .parser import compute_churn_from_tools, compute_context_tier, worst_tier
    from .sessions import _quick_session_stats, _resolve_context_limit

    stats = _quick_session_stats(jsonl_path)
    turns = stats["turns"]
    if turns < 3:
        return

    churn = compute_churn_from_tools(stats.get("tools_per_turn", []))

    per_turn_context = stats.get("per_turn_context", [])
    last_context = per_turn_context[-1] if per_turn_context else 0
    limit = _resolve_context_limit(stats.get("model", ""), per_turn_context)
    ctx_tier, ctx_pct, ctx_reason = compute_context_tier(last_context, limit=limit)

    combined_tier = worst_tier(churn.health_tier, ctx_tier)
    if combined_tier == "green":
        return

    # Figure out which signal is driving the alert (or both)
    drivers = []
    if churn.health_tier != "green":
        drivers.append(f"churn {churn.churn_score} ({churn.health_reason})")
    if ctx_tier != "green":
        drivers.append(ctx_reason)

    icon = {"yellow": "🟡", "red": "🔴", "critical": "⛔"}.get(combined_tier, "•")
    tier_label = combined_tier.upper()

    rel_path = ""
    if handoff_path:
        try:
            rel_path = str(handoff_path.relative_to(cwd))
        except ValueError:
            rel_path = str(handoff_path)

    msg_lines = [
        "",
        "=" * 60,
        f"[ANVL] {icon} Session getting heavy ({tier_label})",
    ]
    for d in drivers:
        msg_lines.append(f"       • {d}")
    if rel_path:
        msg_lines.append(f"       Handoff auto-saved: {rel_path}")
    msg_lines.append("       Start a new conversation at a natural break — CLAUDE.md has the handoff index.")
    msg_lines.append('       To suppress this turn: prefix your message with "anvl bypass".')
    msg_lines.append("=" * 60)
    msg_lines.append("")

    print("\n".join(msg_lines), file=sys.stdout)
    sys.stdout.flush()
