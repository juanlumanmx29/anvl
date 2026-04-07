"""HTTP server for ANVL web dashboard."""

import json
import webbrowser
from functools import partial
from http.server import HTTPServer, BaseHTTPRequestHandler
from pathlib import Path

from ..analyzer import analyze_session, format_tokens
from ..parser import (
    find_active_session,
    find_latest_session,
    find_project_sessions,
    parse_session_file,
)


DASHBOARD_HTML = Path(__file__).parent / "dashboard.html"


class ANVLHandler(BaseHTTPRequestHandler):
    """HTTP request handler for the ANVL dashboard."""

    def __init__(self, *args, cwd: Path | None = None, **kwargs):
        self.project_cwd = cwd
        super().__init__(*args, **kwargs)

    def log_message(self, format, *args):
        """Suppress default request logging."""
        pass

    def do_GET(self):
        path = self.path.split("?")[0]  # Strip query params

        routes = {
            "/": self._serve_dashboard,
            "/api/session/current": self._serve_current_session,
            "/api/sessions": self._serve_session_list,
            "/api/global": self._serve_global_overview,
        }

        if path in routes:
            routes[path]()
        elif path.startswith("/api/session/"):
            session_id = path.split("/")[-1]
            self._serve_session(session_id)
        elif path.startswith("/api/history/"):
            session_id = path.split("/")[-1]
            self._serve_history(session_id)
        elif path == "/api/handoff":
            self._trigger_handoff()
        else:
            self._send_error(404, "Not found")

    def _serve_dashboard(self):
        """Serve the dashboard HTML file."""
        if DASHBOARD_HTML.exists():
            content = DASHBOARD_HTML.read_text(encoding="utf-8")
        else:
            content = "<html><body><h1>Dashboard HTML not found</h1></body></html>"
        self._send_response(content, "text/html")

    def _serve_current_session(self):
        """Serve metrics for the active session."""
        result = find_active_session(self.project_cwd)
        if result is None:
            result = find_latest_session(self.project_cwd)
        if result is None:
            self._send_json({"error": "No active session found"})
            return

        jsonl_path, session_id = result
        session = parse_session_file(jsonl_path)
        metrics = analyze_session(session)

        self._send_json({
            "session_id": metrics.session_id,
            "ai_title": metrics.ai_title,
            "turns": metrics.turn_count,
            "waste_factor": round(metrics.current_waste_factor, 1),
            "average_waste": round(metrics.average_waste_factor, 1),
            "semaphore": metrics.semaphore,
            "total_input": metrics.total_input_tokens,
            "total_output": metrics.total_output_tokens,
            "total_cache_read": metrics.total_cache_read,
            "trend": metrics.trend,
            "git_branch": session.git_branch,
            "cwd": session.cwd,
        })

    def _serve_session(self, session_id: str):
        """Serve metrics for a specific session."""
        from ..config import find_project_dir

        project_dir = find_project_dir(self.project_cwd)
        if project_dir is None:
            self._send_json({"error": "Project not found"})
            return

        jsonl_path = project_dir / f"{session_id}.jsonl"
        if not jsonl_path.exists():
            # Try partial match
            for f in project_dir.glob("*.jsonl"):
                if f.stem.startswith(session_id):
                    jsonl_path = f
                    break
            else:
                self._send_json({"error": "Session not found"})
                return

        session = parse_session_file(jsonl_path)
        metrics = analyze_session(session)

        self._send_json({
            "session_id": metrics.session_id,
            "ai_title": metrics.ai_title,
            "turns": metrics.turn_count,
            "waste_factor": round(metrics.current_waste_factor, 1),
            "average_waste": round(metrics.average_waste_factor, 1),
            "semaphore": metrics.semaphore,
            "total_input": metrics.total_input_tokens,
            "total_output": metrics.total_output_tokens,
            "trend": metrics.trend,
        })

    def _serve_global_overview(self):
        """Serve global overview: all sessions across all projects with usage stats."""
        from ..config import load_config
        from ..sessions import collect_all_sessions, compute_window_usage, compute_savings, get_reset_info

        config = load_config()
        window_hours = config.get("window_hours", 5)
        limit = config.get("weighted_quota_limit", 105_000_000)

        summaries = collect_all_sessions()
        weighted_total, _, win_start = compute_window_usage(summaries, window_hours)
        usage_pct = min(100.0, (weighted_total / max(limit, 1)) * 100)
        time_remaining, reset_time = get_reset_info(config, win_start)
        savings = compute_savings(summaries)

        sessions = []
        for s in summaries:
            sessions.append({
                "session_id": s.session_id,
                "project": s.project,
                "ai_title": s.ai_title,
                "is_active": s.is_active,
                "turns": s.turns,
                "total_input": s.total_input,
                "total_output": s.total_output,
                "waste_factor": round(s.waste_factor, 1),
                "efficiency": s.efficiency,
                "started_at": s.started_at.isoformat(),
            })

        self._send_json({
            "window_hours": window_hours,
            "weighted_total": round(weighted_total),
            "weighted_limit": limit,
            "usage_pct": round(usage_pct, 1),
            "time_remaining": time_remaining,
            "reset_time": reset_time,
            "savings_pct": round(savings["pct_saved"]),
            "sessions": sessions,
        })

    def _serve_session_list(self):
        """Serve list of all project sessions."""
        session_paths = find_project_sessions(self.project_cwd)
        sessions = []

        for path in reversed(session_paths):  # Most recent first
            session = parse_session_file(path)
            metrics = analyze_session(session)
            sessions.append({
                "session_id": metrics.session_id,
                "ai_title": metrics.ai_title,
                "turns": metrics.turn_count,
                "waste_factor": round(metrics.current_waste_factor, 1),
                "total_input": metrics.total_input_tokens,
                "total_output": metrics.total_output_tokens,
                "semaphore": metrics.semaphore,
            })

        self._send_json(sessions)

    def _serve_history(self, session_id: str):
        """Serve per-turn token data for charts."""
        from ..config import find_project_dir

        project_dir = find_project_dir(self.project_cwd)
        if project_dir is None:
            self._send_json({"error": "Project not found"})
            return

        jsonl_path = project_dir / f"{session_id}.jsonl"
        if not jsonl_path.exists():
            for f in project_dir.glob("*.jsonl"):
                if f.stem.startswith(session_id):
                    jsonl_path = f
                    break
            else:
                self._send_json({"error": "Session not found"})
                return

        session = parse_session_file(jsonl_path)
        metrics = analyze_session(session)

        turns = []
        for tm in metrics.per_turn:
            turns.append({
                "index": tm.turn_index,
                "input_tokens": tm.input_tokens,
                "cache_creation": tm.cache_creation,
                "cache_read": tm.cache_read,
                "output_tokens": tm.output_tokens,
                "waste": round(tm.waste_factor, 1),
                "is_tool_only": tm.is_tool_only,
            })

        self._send_json({"turns": turns})

    def _trigger_handoff(self):
        """Generate handoff.md via API."""
        result = find_active_session(self.project_cwd)
        if result is None:
            result = find_latest_session(self.project_cwd)
        if result is None:
            self._send_json({"error": "No session found"})
            return

        from ..handoff import generate_handoff

        jsonl_path, _ = result
        session = parse_session_file(jsonl_path)
        metrics = analyze_session(session)
        output_path = Path(session.cwd or ".") / "handoff.md"
        generate_handoff(session, metrics, output_path)

        self._send_json({"status": "ok", "path": str(output_path)})

    def _send_json(self, data):
        self._send_response(json.dumps(data, ensure_ascii=False), "application/json")

    def _send_response(self, content: str, content_type: str):
        self.send_response(200)
        self.send_header("Content-Type", f"{content_type}; charset=utf-8")
        self.send_header("Access-Control-Allow-Origin", "*")
        encoded = content.encode("utf-8")
        self.send_header("Content-Length", str(len(encoded)))
        self.end_headers()
        self.wfile.write(encoded)

    def _send_error(self, code: int, message: str):
        self.send_response(code)
        self.send_header("Content-Type", "application/json")
        self.end_headers()
        self.wfile.write(json.dumps({"error": message}).encode("utf-8"))


def start_server(port: int = 3000, cwd: Path | None = None) -> None:
    """Start the ANVL dashboard HTTP server."""
    handler = partial(ANVLHandler, cwd=cwd)
    server = HTTPServer(("127.0.0.1", port), handler)

    print(f"ANVL Dashboard running at http://localhost:{port}")
    print("Press Ctrl+C to stop\n")

    try:
        webbrowser.open(f"http://localhost:{port}")
    except Exception:
        pass

    try:
        server.serve_forever()
    except KeyboardInterrupt:
        print("\nDashboard stopped.")
        server.server_close()
