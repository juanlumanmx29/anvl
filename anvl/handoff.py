"""Multi-handoff generator for Claude Code sessions.

Handoffs are stored per-session under `<project>/.anvl/handoffs/<stamp>-<short>.md`.
CLAUDE.md gets an index table of active handoffs between anvl markers.
"""

import re
from collections import defaultdict
from dataclasses import dataclass
from datetime import datetime, timezone
from pathlib import Path

from .analyzer import SessionMetrics, format_tokens
from .parser import SessionData

HANDOFFS_DIR_NAME = ".anvl/handoffs"
ARCHIVE_DIR_NAME = "archive"
# Default hours without activity before a handoff is archived. Kept short so
# abandoned sessions fall off the index fast — users running several parallel
# sessions need the active set to stay short and distinguishable.
DEFAULT_INACTIVE_HOURS = 48

HANDOFFS_START = "<!-- anvl:handoffs-start -->"
HANDOFFS_END = "<!-- anvl:handoffs-end -->"
# Legacy v2 markers — upgraded on first write
LEGACY_START = "<!-- anvl:handoff-start -->"
LEGACY_END = "<!-- anvl:handoff-end -->"


@dataclass
class HandoffMeta:
    path: Path
    session_id: str
    session_short: str
    ai_title: str
    generated_at: str
    turns: int
    churn: float
    status: str  # "active" | "archived"
    last_user_prompt: str = ""


def _handoffs_dir(project_dir: Path) -> Path:
    return project_dir / HANDOFFS_DIR_NAME


def _archive_dir(project_dir: Path) -> Path:
    return _handoffs_dir(project_dir) / ARCHIVE_DIR_NAME


def _session_short(session_id: str) -> str:
    return session_id[:8] if session_id else "unknown"


def _handoff_filename(session_id: str, ts: datetime) -> str:
    stamp = ts.strftime("%Y%m%d-%H%M")
    return f"{stamp}-{_session_short(session_id)}.md"


def _extract_text(turn_text: str, max_len: int) -> str:
    text = turn_text.strip()
    if len(text) <= max_len:
        return text
    return text[:max_len] + "..."


def _short_path(path: str | None) -> str:
    if path is None:
        return ""
    parts = path.replace("\\", "/").split("/")
    if len(parts) <= 3:
        return "/".join(parts)
    return ".../" + "/".join(parts[-3:])


def extract_files_touched(session: SessionData) -> dict[str, list[str]]:
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
    parts = []
    if session.ai_title:
        parts.append(session.ai_title)
    if session.turns:
        first_text = session.turns[0].user_text.strip()
        if first_text and first_text != session.ai_title:
            if len(first_text) > 300:
                first_text = first_text[:300] + "..."
            parts.append(first_text)
    return "\n\n".join(parts) if parts else "No summary available."


def extract_last_state(session: SessionData, n: int = 3) -> str:
    if not session.turns:
        return "No turns recorded."
    recent = session.turns[-n:]
    lines = []
    for turn in recent:
        user_text = _extract_text(turn.user_text, 500)
        assistant_text = _extract_text(turn.assistant_text, 500)
        if user_text:
            lines.append(f"**User (turn {turn.index}):** {user_text}")
        if assistant_text:
            lines.append(f"**Assistant:** {assistant_text}")
        if turn.tool_uses:
            tools_str = ", ".join(
                t.name + (f" ({_short_path(t.file_path)})" if t.file_path else "") for t in turn.tool_uses[:5]
            )
            if len(turn.tool_uses) > 5:
                tools_str += f" (+{len(turn.tool_uses) - 5} more)"
            lines.append(f"*Tools used:* {tools_str}")
        lines.append("")
    return "\n".join(lines).strip()


def extract_pending_work(session: SessionData) -> str:
    if not session.turns:
        return "No clear pending work detected."
    # Use the user's last request as the most reliable "next step" signal
    last = session.turns[-1]
    if last.user_text:
        return f"Last user request: {_extract_text(last.user_text, 400)}"
    return "No clear pending work detected."


def _last_user_prompt_snippet(session: SessionData, max_len: int = 120) -> str:
    """Short one-line preview of the last user prompt, safe for YAML frontmatter."""
    if not session.turns:
        return ""
    for turn in reversed(session.turns):
        if turn.user_text:
            text = turn.user_text.strip().replace("\n", " ").replace("\r", " ")
            text = " ".join(text.split())
            if len(text) > max_len:
                text = text[: max_len - 1] + "…"
            return text
    return ""


def _build_front_matter(
    session: SessionData,
    metrics: SessionMetrics,
    generated_at: datetime,
) -> str:
    last_prompt = _last_user_prompt_snippet(session).replace('"', "'")
    lines = [
        "---",
        f"session_id: {session.session_id}",
        f"session_short: {_session_short(session.session_id)}",
        f"ai_title: {(session.ai_title or 'Untitled').replace(chr(10), ' ')}",
        f"generated_at: {generated_at.isoformat()}",
        f"turns: {metrics.turn_count}",
        f"churn: {metrics.churn_score}",
        f"health_tier: {metrics.health_tier}",
        "status: active",
        f'last_user_prompt: "{last_prompt}"',
        "---",
        "",
    ]
    return "\n".join(lines)


def generate_handoff(
    session: SessionData,
    metrics: SessionMetrics,
    project_dir: Path | None = None,
) -> Path:
    """Generate a handoff for this session under .anvl/handoffs/.

    Returns the path to the written file. If a handoff for this session
    already exists (same session_short), it is overwritten — one handoff
    per session.
    """
    if project_dir is None:
        project_dir = Path(session.cwd or ".")

    handoffs_dir = _handoffs_dir(project_dir)
    handoffs_dir.mkdir(parents=True, exist_ok=True)

    generated_at = datetime.now(timezone.utc).astimezone()
    session_short = _session_short(session.session_id)

    # One handoff file per session: reuse existing if present
    existing = sorted(handoffs_dir.glob(f"*-{session_short}.md"))
    if existing:
        output_path = existing[0]
    else:
        output_path = handoffs_dir / _handoff_filename(session.session_id, generated_at)

    lines: list[str] = []
    lines.append(_build_front_matter(session, metrics, generated_at))
    lines.append(f"# ANVL Handoff — {session.ai_title or 'Untitled Session'}")
    lines.append(
        f"> Updated: {generated_at.strftime('%Y-%m-%d %H:%M')} | "
        f"Session: {session_short} | Turns: {metrics.turn_count} | "
        f"Churn: {metrics.churn_score} ({metrics.health_tier})"
    )
    lines.append("")
    lines.append("## Session summary")
    lines.append(extract_session_summary(session))
    lines.append("")

    files_touched = extract_files_touched(session)
    lines.append("## Work completed")
    if files_touched:
        write_edit_files = {
            fp: acts for fp, acts in files_touched.items() if any(a in ("Written", "Edited") for a in acts)
        }
        if write_edit_files:
            lines.append("Files created or modified:")
            for fp, acts in write_edit_files.items():
                lines.append(f"- `{_short_path(fp)}` ({', '.join(acts)})")
        bash_entries = {fp: acts for fp, acts in files_touched.items() if fp.startswith("[Bash]")}
        if bash_entries:
            lines.append("\nCommands executed:")
            for fp, _ in list(bash_entries.items())[:10]:
                lines.append(f"- `{fp[7:]}`")
    else:
        lines.append("No file modifications detected.")
    lines.append("")

    lines.append("## Last state")
    lines.append(extract_last_state(session))
    lines.append("")

    lines.append("## Pending / Next steps")
    lines.append(extract_pending_work(session))
    lines.append("")

    lines.append("## Technical context")
    lines.append(f"- Session ID: {session.session_id}")
    lines.append(f"- CWD: {session.cwd or 'unknown'}")
    lines.append(f"- Git branch: {session.git_branch or 'unknown'}")
    if session.turns:
        lines.append(f"- Session started: {session.turns[0].timestamp}")
    lines.append(f"- Total input tokens: {format_tokens(metrics.total_input_tokens)}")
    lines.append(f"- Total output tokens: {format_tokens(metrics.total_output_tokens)}")
    lines.append(f"- Churn score: {metrics.churn_score} ({metrics.health_reason})")
    if metrics.most_reread_files:
        top = ", ".join(f"{Path(p).name}({n})" for p, n in metrics.most_reread_files[:3])
        lines.append(f"- Most re-read files: {top}")
    lines.append("")

    output_path.write_text("\n".join(lines), encoding="utf-8")

    # Archive stale peer handoffs and refresh CLAUDE.md
    archive_stale_handoffs(project_dir)
    update_claude_md_index(project_dir)

    return output_path


def _parse_front_matter(path: Path) -> dict:
    """Read the YAML-ish front matter from a handoff file."""
    try:
        text = path.read_text(encoding="utf-8")
    except OSError:
        return {}
    if not text.startswith("---"):
        return {}
    end = text.find("\n---", 3)
    if end == -1:
        return {}
    body = text[3:end].strip()
    data: dict = {}
    for line in body.splitlines():
        if ":" in line:
            k, v = line.split(":", 1)
            v = v.strip()
            # Strip optional surrounding quotes for string values
            if len(v) >= 2 and v[0] == v[-1] and v[0] in ('"', "'"):
                v = v[1:-1]
            data[k.strip()] = v
    return data


def list_handoffs(project_dir: Path, include_archived: bool = False) -> list[HandoffMeta]:
    """List handoff files for the project, sorted newest first."""
    handoffs_dir = _handoffs_dir(project_dir)
    if not handoffs_dir.exists():
        return []

    results: list[HandoffMeta] = []
    for path in handoffs_dir.glob("*.md"):
        if not path.is_file():
            continue
        meta = _parse_front_matter(path)
        results.append(
            HandoffMeta(
                path=path,
                session_id=meta.get("session_id", ""),
                session_short=meta.get("session_short", path.stem[-8:]),
                ai_title=meta.get("ai_title", "Untitled"),
                generated_at=meta.get("generated_at", ""),
                turns=int(meta.get("turns", 0) or 0),
                churn=float(meta.get("churn", 0) or 0),
                status="active",
                last_user_prompt=meta.get("last_user_prompt", ""),
            )
        )

    if include_archived:
        archive = _archive_dir(project_dir)
        if archive.exists():
            for path in archive.glob("*.md"):
                meta = _parse_front_matter(path)
                results.append(
                    HandoffMeta(
                        path=path,
                        session_id=meta.get("session_id", ""),
                        session_short=meta.get("session_short", path.stem[-8:]),
                        ai_title=meta.get("ai_title", "Untitled"),
                        generated_at=meta.get("generated_at", ""),
                        turns=int(meta.get("turns", 0) or 0),
                        churn=float(meta.get("churn", 0) or 0),
                        status="archived",
                        last_user_prompt=meta.get("last_user_prompt", ""),
                    )
                )

    results.sort(key=lambda h: h.generated_at, reverse=True)
    return results


def archive_stale_handoffs(project_dir: Path, inactive_hours: int | None = None) -> int:
    """Move handoff files with no recent activity into the archive subdir.

    A handoff's mtime is refreshed every time its session submits a prompt
    (via UserPromptSubmit hook). So mtime older than `inactive_hours` is a
    reliable abandonment signal — users running parallel sessions want the
    active index to stay short.

    Returns number of files archived.
    """
    from datetime import timedelta

    handoffs_dir = _handoffs_dir(project_dir)
    if not handoffs_dir.exists():
        return 0

    if inactive_hours is None:
        try:
            from .config import get_handoff_inactive_hours

            inactive_hours = get_handoff_inactive_hours()
        except Exception:
            inactive_hours = DEFAULT_INACTIVE_HOURS

    archive = _archive_dir(project_dir)
    cutoff = datetime.now(timezone.utc) - timedelta(hours=inactive_hours)
    archived = 0

    for path in handoffs_dir.glob("*.md"):
        if not path.is_file():
            continue
        try:
            mtime = datetime.fromtimestamp(path.stat().st_mtime, tz=timezone.utc)
        except OSError:
            continue
        if mtime < cutoff:
            archive.mkdir(parents=True, exist_ok=True)
            try:
                path.rename(archive / path.name)
                archived += 1
            except OSError:
                pass

    return archived


def archive_handoff(project_dir: Path, session_short: str) -> Path | None:
    """Manually archive a specific handoff by session_short."""
    handoffs_dir = _handoffs_dir(project_dir)
    archive = _archive_dir(project_dir)

    matches = list(handoffs_dir.glob(f"*-{session_short}.md"))
    if not matches:
        return None
    archive.mkdir(parents=True, exist_ok=True)
    target = archive / matches[0].name
    matches[0].rename(target)
    update_claude_md_index(project_dir)
    return target


def _format_index_section(project_dir: Path) -> str:
    """Build the CLAUDE.md table for active handoffs."""
    active = list_handoffs(project_dir, include_archived=False)
    archived = [h for h in list_handoffs(project_dir, include_archived=True) if h.status == "archived"]

    lines = [HANDOFFS_START, "## Active handoffs", ""]

    if not active:
        lines.append("_No active handoffs._")
    else:
        lines.append(
            "Several sessions may be in flight. Pick the handoff whose last "
            "prompt matches the user's request — if unsure, ask which to resume."
        )
        lines.append("")
        lines.append("| When | Session | Title | Last prompt | Turns | Churn |")
        lines.append("|---|---|---|---|---|---|")
        for h in active:
            when = h.generated_at[:16].replace("T", " ") if h.generated_at else "unknown"
            title = (h.ai_title or "Untitled").replace("|", "\\|")[:50]
            last = (h.last_user_prompt or "").replace("|", "\\|")[:60]
            rel = h.path.relative_to(project_dir).as_posix() if h.path.is_absolute() else h.path.as_posix()
            lines.append(
                f"| {when} | [{h.session_short}]({rel}) | {title} | {last} | {h.turns} | {h.churn} |"
            )

    if archived:
        lines.append("")
        lines.append("<details><summary>Archived handoffs</summary>")
        lines.append("")
        for h in archived:
            when = h.generated_at[:16].replace("T", " ") if h.generated_at else "unknown"
            title = (h.ai_title or "Untitled").replace("|", "\\|")[:60]
            lines.append(f"- {when} · {h.session_short} · {title} ({h.turns} turns, churn {h.churn})")
        lines.append("")
        lines.append("</details>")

    lines.append(HANDOFFS_END)
    return "\n".join(lines)


def update_claude_md_index(project_dir: Path) -> None:
    """Insert or replace the handoff index block in CLAUDE.md."""
    claude_md = project_dir / "CLAUDE.md"
    new_section = _format_index_section(project_dir)

    if claude_md.exists():
        text = claude_md.read_text(encoding="utf-8")
        # Always strip legacy marker block if present
        if LEGACY_START in text:
            text = (
                re.sub(
                    re.escape(LEGACY_START) + r".*?" + re.escape(LEGACY_END),
                    "",
                    text,
                    flags=re.DOTALL,
                ).rstrip()
                + "\n"
            )

        pattern = re.compile(
            re.escape(HANDOFFS_START) + r".*?" + re.escape(HANDOFFS_END),
            re.DOTALL,
        )
        if pattern.search(text):
            new_text = pattern.sub(new_section, text)
        else:
            new_text = text.rstrip() + "\n\n" + new_section + "\n"
    else:
        new_text = new_section + "\n"

    claude_md.write_text(new_text, encoding="utf-8")


def migrate_legacy_handoff(project_dir: Path) -> Path | None:
    """Move <project>/handoff.md (v2 layout) into .anvl/handoffs/ if present."""
    legacy = project_dir / "handoff.md"
    if not legacy.exists():
        return None

    handoffs_dir = _handoffs_dir(project_dir)
    handoffs_dir.mkdir(parents=True, exist_ok=True)

    try:
        mtime = datetime.fromtimestamp(legacy.stat().st_mtime, tz=timezone.utc).astimezone()
    except OSError:
        mtime = datetime.now(timezone.utc).astimezone()

    target = handoffs_dir / f"{mtime.strftime('%Y%m%d-%H%M')}-legacy00.md"
    try:
        legacy.rename(target)
        return target
    except OSError:
        return None
