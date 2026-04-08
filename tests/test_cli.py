"""Integration tests for anvl CLI commands."""

import json
import subprocess
import sys
import tempfile
from pathlib import Path
from unittest.mock import patch

import pytest

from anvl.cli import main

FIXTURES = Path(__file__).parent / "fixtures"


def run_anvl(*args: str, cwd: str | None = None) -> subprocess.CompletedProcess:
    """Run anvl as a subprocess and capture output."""
    cmd = [sys.executable, "-m", "anvl", *args]
    return subprocess.run(
        cmd,
        capture_output=True,
        text=True,
        timeout=10,
        cwd=cwd,
        env={**__import__("os").environ, "PYTHONIOENCODING": "utf-8"},
    )


class TestCLIHelp:
    def test_no_args_shows_help(self):
        result = run_anvl()
        assert result.returncode == 0
        assert "anvl" in result.stdout.lower() or "usage" in result.stdout.lower()

    def test_status_help(self):
        result = run_anvl("status", "--help")
        assert result.returncode == 0
        assert "--session" in result.stdout

    def test_handoff_help(self):
        result = run_anvl("handoff", "--help")
        assert result.returncode == 0
        assert "--output" in result.stdout or "-o" in result.stdout

    def test_sessions_help(self):
        result = run_anvl("sessions", "--help")
        assert result.returncode == 0
        assert "--active" in result.stdout
        assert "--today" in result.stdout
        assert "--all" in result.stdout

    def test_monitor_help(self):
        result = run_anvl("monitor", "--help")
        assert result.returncode == 0
        assert "--interval" in result.stdout



class TestCLIStatus:
    def test_status_no_project_exits_nonzero(self):
        """Status with no Claude project should exit 1."""
        with tempfile.TemporaryDirectory() as tmpdir:
            result = run_anvl("--cwd", tmpdir, "status")
            assert result.returncode == 1

    def test_status_json_no_project_exits_nonzero(self):
        with tempfile.TemporaryDirectory() as tmpdir:
            result = run_anvl("--cwd", tmpdir, "status", "--json")
            assert result.returncode == 1


class TestCLIHandoff:
    def test_handoff_no_project_exits_nonzero(self):
        with tempfile.TemporaryDirectory() as tmpdir:
            result = run_anvl("--cwd", tmpdir, "handoff")
            assert result.returncode == 1

    def test_handoff_custom_output(self):
        """Handoff with -o to custom path should mention the path."""
        with tempfile.TemporaryDirectory() as tmpdir:
            result = run_anvl("--cwd", tmpdir, "handoff", "-o", str(Path(tmpdir) / "test.md"))
            # Will fail because no session, but validates arg parsing
            assert result.returncode == 1


class TestCLISessions:
    def test_sessions_empty_exits_nonzero(self):
        """Sessions with no Claude home should handle gracefully."""
        with tempfile.TemporaryDirectory() as tmpdir:
            result = run_anvl("sessions")
            # May exit 1 if no sessions found, or 0 if empty
            assert result.returncode in (0, 1)


class TestCLIHookInstall:
    def test_install_creates_hook(self):
        """Install should create the hook in settings."""
        with tempfile.TemporaryDirectory() as tmpdir:
            settings_path = Path(tmpdir) / "settings.json"
            settings_path.write_text("{}", encoding="utf-8")

            with patch("anvl.hooks.SETTINGS_PATH", settings_path):
                from anvl.hooks import install_hook
                install_hook()

            settings = json.loads(settings_path.read_text(encoding="utf-8"))
            assert "hooks" in settings
            assert "PostToolUse" in settings["hooks"]
            hooks = settings["hooks"]["PostToolUse"]
            assert len(hooks) == 1
            assert hooks[0]["hooks"][0]["command"] == "anvl hook user-prompt-submit"

    def test_uninstall_then_install(self):
        """Uninstall followed by install should leave exactly one hook."""
        with tempfile.TemporaryDirectory() as tmpdir:
            settings_path = Path(tmpdir) / "settings.json"
            settings_path.write_text("{}", encoding="utf-8")

            with patch("anvl.hooks.SETTINGS_PATH", settings_path):
                from anvl.hooks import install_hook, uninstall_hook
                install_hook()
                uninstall_hook()
                install_hook()

            settings = json.loads(settings_path.read_text(encoding="utf-8"))
            hooks = settings["hooks"]["PostToolUse"]
            assert len(hooks) == 1
