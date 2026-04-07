"""Tests for anvl.hooks module."""

import json
import tempfile
from pathlib import Path
from unittest.mock import patch

from anvl.hooks import (
    ANVL_HOOK_COMMAND,
    _find_anvl_hook_index,
    install_hook,
    uninstall_hook,
)


def _make_settings(hooks=None):
    """Create a temporary settings file and return its path."""
    settings = {}
    if hooks is not None:
        settings["hooks"] = hooks
    tmpdir = tempfile.mkdtemp()
    path = Path(tmpdir) / "settings.json"
    path.write_text(json.dumps(settings), encoding="utf-8")
    return path


def test_find_anvl_hook_index_empty():
    assert _find_anvl_hook_index([]) is None


def test_find_anvl_hook_index_present():
    entries = [
        {"matcher": "", "hooks": [{"type": "command", "command": "other-tool"}]},
        {"matcher": "", "hooks": [{"type": "command", "command": ANVL_HOOK_COMMAND}]},
    ]
    assert _find_anvl_hook_index(entries) == 1


def test_install_hook_creates_entry():
    path = _make_settings()
    with patch("anvl.hooks.SETTINGS_PATH", path):
        install_hook()

    settings = json.loads(path.read_text(encoding="utf-8"))
    post_tool_use = settings["hooks"]["PostToolUse"]
    assert len(post_tool_use) == 1
    assert post_tool_use[0]["hooks"][0]["command"] == ANVL_HOOK_COMMAND


def test_install_hook_idempotent():
    path = _make_settings()
    with patch("anvl.hooks.SETTINGS_PATH", path):
        install_hook()
        install_hook()  # Second call should not duplicate

    settings = json.loads(path.read_text(encoding="utf-8"))
    post_tool_use = settings["hooks"]["PostToolUse"]
    assert len(post_tool_use) == 1


def test_install_preserves_existing_hooks():
    existing = {
        "PostToolUse": [
            {"matcher": "", "hooks": [{"type": "command", "command": "clauditor check"}]}
        ]
    }
    path = _make_settings(hooks=existing)
    with patch("anvl.hooks.SETTINGS_PATH", path):
        install_hook()

    settings = json.loads(path.read_text(encoding="utf-8"))
    post_tool_use = settings["hooks"]["PostToolUse"]
    assert len(post_tool_use) == 2
    assert post_tool_use[0]["hooks"][0]["command"] == "clauditor check"
    assert post_tool_use[1]["hooks"][0]["command"] == ANVL_HOOK_COMMAND


def test_uninstall_removes_only_anvl():
    existing = {
        "PostToolUse": [
            {"matcher": "", "hooks": [{"type": "command", "command": "clauditor check"}]},
            {"matcher": "", "hooks": [{"type": "command", "command": ANVL_HOOK_COMMAND}]},
        ]
    }
    path = _make_settings(hooks=existing)
    with patch("anvl.hooks.SETTINGS_PATH", path):
        uninstall_hook()

    settings = json.loads(path.read_text(encoding="utf-8"))
    post_tool_use = settings["hooks"]["PostToolUse"]
    assert len(post_tool_use) == 1
    assert post_tool_use[0]["hooks"][0]["command"] == "clauditor check"


def test_uninstall_no_hook_noop():
    path = _make_settings(hooks={"PostToolUse": []})
    with patch("anvl.hooks.SETTINGS_PATH", path):
        uninstall_hook()  # Should not raise
