"""Parser for Claude Code session JSONL files."""

import json
from dataclasses import dataclass, field
from pathlib import Path
from typing import Iterator

from .config import find_project_dir, get_sessions_dir


@dataclass
class TokenUsage:
    input_tokens: int = 0
    cache_creation_input_tokens: int = 0
    cache_read_input_tokens: int = 0
    output_tokens: int = 0

    @property
    def total_input(self) -> int:
        return (
            self.input_tokens
            + self.cache_creation_input_tokens
            + self.cache_read_input_tokens
        )


@dataclass
class ToolUseRecord:
    name: str
    file_path: str | None = None
    command: str | None = None


@dataclass
class Turn:
    index: int
    user_text: str = ""
    assistant_text: str = ""
    tool_uses: list[ToolUseRecord] = field(default_factory=list)
    usage: TokenUsage | None = None
    timestamp: str = ""
    is_tool_only: bool = False


@dataclass
class SessionData:
    session_id: str = ""
    ai_title: str = ""
    cwd: str = ""
    git_branch: str = ""
    turns: list[Turn] = field(default_factory=list)
    raw_line_count: int = 0


def iter_jsonl(path: Path) -> Iterator[dict]:
    """Yield parsed JSON objects from a JSONL file, skipping malformed lines."""
    with open(path, "r", encoding="utf-8") as f:
        for line in f:
            line = line.strip()
            if not line:
                continue
            try:
                yield json.loads(line)
            except json.JSONDecodeError:
                continue


def _extract_text_from_content(content: list) -> str:
    """Extract concatenated text from a message content array."""
    parts = []
    for block in content:
        if isinstance(block, dict) and block.get("type") == "text":
            text = block.get("text", "")
            # Skip IDE metadata
            if text.startswith("<ide_opened_file>"):
                continue
            parts.append(text)
    return "\n".join(parts)


def _extract_tool_uses(content: list) -> list[ToolUseRecord]:
    """Extract tool use records from assistant message content."""
    records = []
    for block in content:
        if isinstance(block, dict) and block.get("type") == "tool_use":
            name = block.get("name", "")
            inp = block.get("input", {})
            file_path = inp.get("file_path")
            command = inp.get("command")
            records.append(ToolUseRecord(name=name, file_path=file_path, command=command))
    return records


def _extract_usage(message: dict) -> TokenUsage | None:
    """Extract token usage from an assistant message."""
    usage = message.get("usage")
    if not usage:
        return None
    return TokenUsage(
        input_tokens=usage.get("input_tokens", 0),
        cache_creation_input_tokens=usage.get("cache_creation_input_tokens", 0),
        cache_read_input_tokens=usage.get("cache_read_input_tokens", 0),
        output_tokens=usage.get("output_tokens", 0),
    )


def _is_tool_result(record: dict) -> bool:
    """Check if a user-type record is a tool result (not a real user turn)."""
    msg = record.get("message", {})
    content = msg.get("content", [])
    if not content:
        return False
    # Tool results have content items with 'tool_use_id'
    return any(
        isinstance(c, dict) and "tool_use_id" in c
        for c in content
    )


def parse_session_file(path: Path) -> SessionData:
    """Parse a .jsonl session file into structured SessionData."""
    session = SessionData()
    line_count = 0

    # Collect all records first
    records = []
    for record in iter_jsonl(path):
        line_count += 1
        records.append(record)

    session.raw_line_count = line_count

    # First pass: extract metadata
    for record in records:
        rtype = record.get("type")
        if rtype == "ai-title":
            session.session_id = record.get("sessionId", "")
            session.ai_title = record.get("aiTitle", "")
        elif rtype == "user" and not session.cwd:
            session.cwd = record.get("cwd", "")
            session.git_branch = record.get("gitBranch", "")
            if not session.session_id:
                session.session_id = record.get("sessionId", "")

    # Second pass: assemble turns
    # A turn starts with a real user message (not a tool result)
    # and includes all subsequent assistant messages and tool result exchanges
    # until the next real user message.
    current_turn: Turn | None = None
    turn_index = 0
    # Track seen request IDs to deduplicate streaming chunks
    seen_request_ids: dict[str, dict] = {}  # requestId -> last assistant record

    for record in records:
        rtype = record.get("type")

        if rtype == "user" and not _is_tool_result(record):
            # Real user turn - finalize previous turn if any
            if current_turn is not None:
                _finalize_turn(current_turn, seen_request_ids)
                session.turns.append(current_turn)

            current_turn = Turn(index=turn_index)
            turn_index += 1
            msg = record.get("message", {})
            content = msg.get("content", [])
            current_turn.user_text = _extract_text_from_content(content)
            current_turn.timestamp = record.get("timestamp", "")
            seen_request_ids = {}

        elif rtype == "assistant" and current_turn is not None:
            msg = record.get("message", {})
            request_id = record.get("requestId", "")

            # Collect tool uses from all chunks
            content = msg.get("content", [])
            tool_uses = _extract_tool_uses(content)
            current_turn.tool_uses.extend(tool_uses)

            # Collect text from all chunks
            text = _extract_text_from_content(content)
            if text:
                if current_turn.assistant_text:
                    current_turn.assistant_text += "\n" + text
                else:
                    current_turn.assistant_text = text

            # Track by requestId - keep the last one (has final usage/stop_reason)
            if request_id:
                seen_request_ids[request_id] = record

    # Finalize last turn
    if current_turn is not None:
        _finalize_turn(current_turn, seen_request_ids)
        session.turns.append(current_turn)

    return session


def _finalize_turn(turn: Turn, request_records: dict[str, dict]) -> None:
    """Set usage on a turn from the final assistant records in the turn."""
    # Sum usage across all distinct API requests in this turn
    total_usage = TokenUsage()
    for request_id, record in request_records.items():
        msg = record.get("message", {})
        usage = _extract_usage(msg)
        if usage:
            total_usage.input_tokens += usage.input_tokens
            total_usage.cache_creation_input_tokens += usage.cache_creation_input_tokens
            total_usage.cache_read_input_tokens += usage.cache_read_input_tokens
            total_usage.output_tokens += usage.output_tokens

    if total_usage.output_tokens > 0 or total_usage.total_input > 0:
        turn.usage = total_usage
        turn.is_tool_only = total_usage.output_tokens <= 1


def find_active_session(cwd: Path | None = None) -> tuple[Path, str] | None:
    """Find the active session JSONL for the current project.

    Strategy:
    1. Scan ~/.claude/sessions/*.json for PID files matching cwd
    2. Fallback: most recently modified JSONL in project directory
    """
    if cwd is None:
        cwd = Path.cwd()

    project_dir = find_project_dir(cwd)
    if project_dir is None:
        return None

    sessions_dir = get_sessions_dir()
    cwd_str = str(cwd).lower().replace("/", "\\")

    # Try PID files first
    best_session = None
    best_mtime = 0

    if sessions_dir.exists():
        for pid_file in sessions_dir.glob("*.json"):
            try:
                with open(pid_file, "r", encoding="utf-8") as f:
                    data = json.load(f)
                session_cwd = data.get("cwd", "").lower().replace("/", "\\")
                if session_cwd == cwd_str:
                    session_id = data.get("sessionId", "")
                    jsonl_path = project_dir / f"{session_id}.jsonl"
                    if jsonl_path.exists():
                        mtime = jsonl_path.stat().st_mtime
                        if mtime > best_mtime:
                            best_mtime = mtime
                            best_session = (jsonl_path, session_id)
            except (json.JSONDecodeError, OSError):
                continue

    if best_session:
        return best_session

    # Fallback: most recently modified JSONL
    return find_latest_session(cwd)


def find_latest_session(cwd: Path | None = None) -> tuple[Path, str] | None:
    """Find the most recently modified session JSONL for the project."""
    if cwd is None:
        cwd = Path.cwd()

    project_dir = find_project_dir(cwd)
    if project_dir is None:
        return None

    best_path = None
    best_mtime = 0

    for jsonl_file in project_dir.glob("*.jsonl"):
        mtime = jsonl_file.stat().st_mtime
        if mtime > best_mtime:
            best_mtime = mtime
            best_path = jsonl_file

    if best_path:
        session_id = best_path.stem
        return (best_path, session_id)
    return None


def find_project_sessions(cwd: Path | None = None) -> list[Path]:
    """Find all JSONL session files for the project."""
    if cwd is None:
        cwd = Path.cwd()

    project_dir = find_project_dir(cwd)
    if project_dir is None:
        return []

    return sorted(project_dir.glob("*.jsonl"), key=lambda p: p.stat().st_mtime)
