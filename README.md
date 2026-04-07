# ANVL

**Session monitor and handoff tool for Claude Code.**

Developed by **IronDevz**

---

## The problem

Claude Code sends the entire conversation history on every turn. On turn 1 you send ~5K tokens, but by turn 50 you're sending ~170K tokens to get 500 back. This burns your quota exponentially.

**ANVL** detects this inflation in real time and helps you make a clean cut: it generates a summary of what was done and what's pending, so you can continue in a fresh session without losing context. This saves 40-60% of your daily quota.

## How it works

```
Inflated session (170K tokens/turn)
         │
    ANVL detects waste > 7x
         │
    Generates handoff.md automatically
         │
    Open a new Claude Code session
         │
    "Read handoff.md and continue where I left off"
         │
Fresh session (5K tokens/turn) ✓
```

The **waste factor** is the ratio between input tokens and output tokens. A high value means Claude is reading a lot of context to generate little output:

| Waste Factor | Status | Action |
|:---:|:---:|:---|
| < 3x | Green | Session is healthy |
| 3-7x | Yellow | Starting to inflate |
| > 7x | Red | Do a handoff now |

---

## Installation

### Option 1: From PyPI (recommended)

```bash
pip install anvl
```

### Option 2: From source

```bash
git clone https://github.com/jumontes/anvl.git
cd anvl
pip install -e .
```

### Requirements

- Python 3.11 or higher
- Only dependency is [rich](https://github.com/Textualize/rich) (installed automatically)

---

## Initial setup

After installing, run this once:

```bash
anvl init
```

This does two things:
1. Creates the config file at `~/.anvl/config.json`
2. Installs a hook in Claude Code that alerts you when a session inflates

That's it. ANVL will now automatically alert you when you need to rotate the session.

---

## Usage

### Check your current session status

```bash
anvl status
```

Shows waste factor, tokens used, trend, and a green/yellow/red semaphore.

### View all your sessions

```bash
anvl sessions              # Last 20 sessions
anvl sessions --active     # Only running sessions
anvl sessions --today      # Only today's sessions
anvl sessions --all        # All sessions, no limit
```

### Live monitor

```bash
anvl monitor               # Refreshes every 2 seconds
anvl monitor --interval 5  # Every 5 seconds
```

Live terminal panel with waste gauge and per-turn token table.

### Web dashboard

```bash
anvl dashboard             # Opens http://localhost:3000
anvl dashboard --port 8080 # Custom port
```

Dark-themed dashboard with interactive charts and global overview of all sessions. Includes:
- Quota usage bar with reset timer
- Per-turn token chart (cache read, cache creation, output)
- Waste factor trend chart
- One-click handoff generation

### Generate handoff manually

```bash
anvl handoff               # Generates handoff.md in the project directory
anvl handoff -o path.md    # Custom path
```

The generated file contains:
- Session summary and completed work
- Files touched and actions performed
- Last 3 turns summarized
- Automatically detected pending work
- Technical context (branch, tokens, timestamps)

### Multi-session report

```bash
anvl report                # Comparative table of all project sessions
```

---

## Automatic alerts

If you ran `anvl init`, the hook is already installed. When the cumulative waste factor rises, you'll see alerts directly in Claude Code:

```
[ANVL] Session waste is 8.5x after 12 turns. Keep an eye on it.
```

```
[ANVL] This session is getting expensive (25x waste, 18 turns).
   I recommend starting a new conversation soon.
   Run `anvl handoff` to save context, then open a fresh session.
```

When it reaches critical levels (>50x), ANVL generates the handoff automatically and tells you exactly how to continue:

```
============================================================
[ANVL] This session is critically inflated (168x waste, 24 turns).

Handoff saved: handoff.md

To continue without wasting tokens:
   1. Open a new Claude Code conversation
   2. Say: Read handoff.md and continue where I left off

This typically saves 40-60% of your quota.
============================================================
```

---

## Configuration

File: `~/.anvl/config.json`

```json
{
  "waste_threshold": 7,
  "dashboard_port": 3000,
  "window_hours": 5,
  "weighted_quota_limit": 105000000,
  "handoff_waste_threshold": 50
}
```

| Field | Description | Default |
|-------|-------------|---------|
| `waste_threshold` | Threshold for hook alerts | 7 |
| `dashboard_port` | Web dashboard port | 3000 |
| `window_hours` | Rolling window size (hours) | 5 |
| `weighted_quota_limit` | Weighted token budget | 105M |
| `handoff_waste_threshold` | Threshold for auto-handoff | 50 |

---

## Command reference

| Command | Description |
|---------|-------------|
| `anvl init` | Initial setup (config + hook) |
| `anvl status` | Current session status |
| `anvl sessions` | All sessions with stats |
| `anvl monitor` | Live terminal monitor |
| `anvl dashboard` | Web dashboard with charts |
| `anvl report` | Multi-session comparative report |
| `anvl handoff` | Generate summary for rotation |
| `anvl install` | Install hook in Claude Code |
| `anvl uninstall` | Remove hook |

---

## Contributing

1. Fork the repo
2. Create a branch (`git checkout -b my-feature`)
3. Run tests (`python -m pytest tests/ -v`)
4. Commit and push
5. Open a Pull Request

---

## License

MIT — see [LICENSE](LICENSE)

---

Developed by **IronDevz**
