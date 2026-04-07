"""Generator for handoff.md files from session data."""

from collections import defaultdict
from datetime import datetime
from pathlib import Path

from .analyzer import SessionMetrics, format_tokens
from .parser import SessionData, Turn


def extract_files_touched(session: SessionData) -> dict[str, list[str]]:
    """Extract {file_path: [actions]} from tool use records across all turns."""
    files: dict[str, list[str]] = defaultdict(list)

    tool_to_action = {
        "Write": "Written",
        "Edit": "Edited",
        "Read": "Read",
        "Glob": "Searched",
        "Grep": "Searched",
    }

    for turn in session.turns:
        for tool in turn.tool_uses:
            if tool.file_path:
                action = tool_to_action.get(tool.name, tool.name)
                if action not in files[tool.file_path]:
                    files[tool.file_path].append(action)
            elif tool.name == "Bash" and tool.command:
                cmd_short = tool.command[:80]
                key = f"[Bash] {cmd_short}"
                if "Executed" not in files[key]:
                    files[key].append("Executed")

    return dict(files)


def extract_session_summary(session: SessionData) -> str:
    """Build summary from ai_title and first user message."""
    parts = []
    if session.ai_title:
        parts.append(session.ai_title)
    if session.turns:
        first_text = session.turns[0].user_text.strip()
        if first_text and first_text != session.ai_title:
            # Truncate long first messages
            if len(first_text) > 300:
                first_text = first_text[:300] + "..."
            parts.append(first_text)
    return "\n\n".join(parts) if parts else "No summary available."


def extract_last_state(session: SessionData, n: int = 3) -> str:
    """Format last n turns as markdown."""
    if not session.turns:
        return "No turns recorded."

    recent = session.turns[-n:]
    lines = []
    for turn in recent:
        user_text = _truncate(turn.user_text, 500)
        assistant_text = _truncate(turn.assistant_text, 500)

        if user_text:
            lines.append(f"**User (turn {turn.index}):** {user_text}")
        if assistant_text:
            lines.append(f"**Assistant:** {assistant_text}")
        if turn.tool_uses:
            tools_str = ", ".join(
                t.name + (f" ({_short_path(t.file_path)})" if t.file_path else "")
                for t in turn.tool_uses[:5]
            )
            if len(turn.tool_uses) > 5:
                tools_str += f" (+{len(turn.tool_uses) - 5} more)"
            lines.append(f"*Tools used:* {tools_str}")
        lines.append("")

    return "\n".join(lines).strip()


def extract_pending_work(session: SessionData) -> str:
    """Heuristic extraction of pending work from last messages."""
    if not session.turns:
        return "No clear pending work detected."

    # Scan last 3 turns for TODO-like patterns
    patterns_user = ["todo", "pending", "next", "still need", "remaining", "left to do"]
    patterns_assistant = ["todo", "next step", "remaining", "still need", "left to do", "not yet"]

    findings = []
    for turn in session.turns[-3:]:
        text_lower = turn.user_text.lower()
        for p in patterns_user:
            if p in text_lower:
                findings.append(f"- User mentioned: {_truncate(turn.user_text, 200)}")
                break

        text_lower = turn.assistant_text.lower()
        for p in patterns_assistant:
            if p in text_lower:
                # Extract the sentence containing the pattern
                for sentence in turn.assistant_text.split("."):
                    if p in sentence.lower():
                        findings.append(f"- Assistant noted: {sentence.strip()}")
                        break
                break

    if findings:
        return "\n".join(findings[:5])

    # If no patterns found, describe what was happening in the last turn
    last = session.turns[-1]
    if last.user_text:
        return f"Last user request: {_truncate(last.user_text, 300)}"
    return "No clear pending work detected."


def generate_handoff(
    session: SessionData,
    metrics: SessionMetrics,
    output_path: Path,
) -> Path:
    """Generate the handoff.md file."""
    now = datetime.now().strftime("%Y-%m-%d %H:%M")
    session_short = session.session_id[:8] if session.session_id else "unknown"

    lines = []
    lines.append(f"# ANVL Handoff \u2014 {session.ai_title or 'Untitled Session'}")
    lines.append(
        f"> Generated: {now} | Session: {session_short} | "
        f"Turns: {metrics.turn_count} | Waste: {metrics.current_waste_factor:.1f}x"
    )
    lines.append("")

    # Summary
    lines.append("## Session summary")
    lines.append(extract_session_summary(session))
    lines.append("")

    # Work completed (files touched)
    files_touched = extract_files_touched(session)
    lines.append("## Work completed")
    if files_touched:
        write_edit_files = {
            fp: actions
            for fp, actions in files_touched.items()
            if any(a in ("Written", "Edited") for a in actions)
        }
        if write_edit_files:
            lines.append("Files created or modified:")
            for fp, actions in write_edit_files.items():
                lines.append(f"- `{_short_path(fp)}` ({', '.join(actions)})")
        bash_entries = {
            fp: actions
            for fp, actions in files_touched.items()
            if fp.startswith("[Bash]")
        }
        if bash_entries:
            lines.append("\nCommands executed:")
            for fp, _ in list(bash_entries.items())[:10]:
                lines.append(f"- `{fp[7:]}`")  # Remove "[Bash] " prefix
    else:
        lines.append("No file modifications detected.")
    lines.append("")

    # Files touched table
    lines.append("## Files touched")
    if files_touched:
        lines.append("| File | Actions |")
        lines.append("|------|---------|")
        # Count occurrences per file across turns
        file_counts: dict[str, int] = {}
        for turn in session.turns:
            for tool in turn.tool_uses:
                if tool.file_path:
                    file_counts[tool.file_path] = file_counts.get(tool.file_path, 0) + 1

        for fp in sorted(files_touched.keys()):
            if fp.startswith("[Bash]"):
                continue
            actions = ", ".join(files_touched[fp])
            count = file_counts.get(fp, 1)
            count_str = f" ({count}x)" if count > 1 else ""
            lines.append(f"| `{_short_path(fp)}` | {actions}{count_str} |")
    else:
        lines.append("No files touched.")
    lines.append("")

    # Last state
    lines.append("## Last state")
    lines.append(extract_last_state(session))
    lines.append("")

    # Pending
    lines.append("## Pending / Next steps")
    lines.append(extract_pending_work(session))
    lines.append("")

    # Technical context
    lines.append("## Technical context")
    lines.append(f"- Branch: {session.git_branch or 'unknown'}")
    lines.append(f"- CWD: {session.cwd or 'unknown'}")
    if session.turns:
        lines.append(f"- Session started: {session.turns[0].timestamp}")
    lines.append(f"- Total input tokens: {format_tokens(metrics.total_input_tokens)}")
    lines.append(f"- Total output tokens: {format_tokens(metrics.total_output_tokens)}")
    lines.append("")

    content = "\n".join(lines)
    output_path.parent.mkdir(parents=True, exist_ok=True)
    output_path.write_text(content, encoding="utf-8")
    return output_path


def _truncate(text: str, max_len: int) -> str:
    """Truncate text to max_len chars."""
    text = text.strip()
    if len(text) <= max_len:
        return text
    return text[:max_len] + "..."


def _short_path(path: str | None) -> str:
    """Shorten a file path for display."""
    if path is None:
        return ""
    # Show last 3 parts of the path
    parts = path.replace("\\", "/").split("/")
    if len(parts) <= 3:
        return "/".join(parts)
    return ".../" + "/".join(parts[-3:])
