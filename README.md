# ANVL

**Session monitor for Claude Code — saves your quota by detecting inflated sessions.**

Developed by **IronDevz**

---

## What it does

Claude Code sends the entire conversation history on every turn. By turn 50, you're sending ~170K tokens just to get 500 back. This burns your quota fast.

**ANVL** detects this automatically and:
1. Alerts you inside your Claude Code session when it's getting expensive
2. Saves a handoff summary so you don't lose context
3. Blocks the session when it's critically inflated (saves 40-60% of quota)

## Quick start

```bash
pip install anvl
anvl init
```

That's it. ANVL will now monitor all your Claude Code sessions automatically.

When a session inflates, you'll see messages like:

```
[ANVL] This session is getting expensive. Consider starting a new conversation soon.
```

And when it's critical:

```
[ANVL] Session blocked -- too inflated to continue efficiently.
       Handoff saved: handoff.md
       Start a new conversation and say: "Read handoff.md and continue where I left off"
```

---

## How it works

```
Session inflates (170K tokens/turn)
       |
  ANVL detects it automatically
       |
  Saves handoff.md with full context
       |
  Blocks the session (exit code 2)
       |
  You open a new conversation
       |
  "Read handoff.md and continue where I left off"
       |
  Fresh session (5K tokens/turn)
```

ANVL uses Claude Code hooks to monitor every turn. It calculates a cost-weighted health metric that accounts for cache pricing (cache reads cost 90% less).

| Health | Status | What happens |
|:---:|:---:|:---|
| Green | Healthy | Nothing — keep working |
| Yellow | Inflating | Warning message appears |
| Red | Critical | Session blocked, handoff saved |

---

## Installation

### From source

```bash
git clone https://github.com/jumontes/anvl.git
cd anvl
pip install -e .
```

### Requirements

- Python 3.11+
- Only dependency: [rich](https://github.com/Textualize/rich) (installed automatically)

---

## Setup

```bash
anvl init
```

This does three things:
1. Creates config at `~/.anvl/config.json`
2. Installs hooks in Claude Code (`UserPromptSubmit` + `PostToolUse`)
3. Writes CLAUDE.md with instructions for Claude to handle handoffs

---

## Commands

| Command | Description |
|---------|-------------|
| `anvl init` | First-time setup (config + hooks + CLAUDE.md) |
| `anvl status` | Current session health |
| `anvl sessions` | All sessions with health status |
| `anvl monitor` | Live terminal monitor |
| `anvl dashboard` | Web dashboard at localhost:3000 |
| `anvl handoff` | Generate handoff manually |
| `anvl report` | Multi-session report |

### Live monitor

```bash
anvl monitor
```

Shows session health with a simple semaphore (Healthy / Inflating / Critical) and a table of all active sessions. Auto-refreshes every 2 seconds.

### Web dashboard

```bash
anvl dashboard
```

Dark-themed dashboard with session list, health indicators, and per-turn token charts.

---

## Configuration

File: `~/.anvl/config.json`

| Field | Description | Default |
|-------|-------------|---------|
| `waste_threshold` | Yellow alert threshold | 2 |
| `handoff_waste_threshold` | Auto-handoff + block threshold | 10 |
| `dashboard_port` | Web dashboard port | 3000 |

---

## How alerts work

ANVL hooks into Claude Code and checks session health on every turn:

1. **Yellow (waste 2-5x):** Informational message
   ```
   [ANVL] This session is getting expensive. Consider starting a new conversation soon.
   ```

2. **Red (waste 5-10x):** Generates handoff automatically
   ```
   [ANVL] This session is inflated. Your work has been saved to handoff.md
          Start a new conversation and say: "Read handoff.md and continue where I left off"
   ```

3. **Critical (waste >10x):** Blocks the session (exit code 2)
   ```
   [ANVL] Session blocked -- too inflated to continue efficiently.
          Handoff saved to handoff.md
   ```

---

## License

MIT — see [LICENSE](LICENSE)

---

Developed by **IronDevz**
