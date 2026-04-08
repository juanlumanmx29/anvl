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
[ANVL] Session health: 45% (2.2x waste). Consider starting a new conversation soon.
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
Session inflates (tokens/turn growing)
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
  Fresh session — back to baseline
```

ANVL measures **waste** as the ratio of current tokens/turn vs baseline tokens/turn. A fresh session starts at 1.0x. As the conversation grows, each turn sends more tokens — waste goes up, health goes down.

| Health | Waste | What happens |
|:---:|:---:|:---|
| 100% | 1x | Fresh session — keep working |
| 60-100% | 1-5x | Healthy — no alerts |
| 30-60% | 5-8x | Warning message appears |
| 0-30% | 8-10x | Session blocked, handoff saved |

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
2. Installs hooks in Claude Code (`UserPromptSubmit`, `PostToolUse`, `SessionStart`)
3. Writes CLAUDE.md with instructions for Claude to handle handoffs

---

## Commands

| Command | Description |
|---------|-------------|
| `anvl init` | First-time setup (config + hooks + CLAUDE.md) |
| `anvl status` | Current session health |
| `anvl sessions` | All sessions with health status |
| `anvl monitor` | Live terminal monitor |
| `anvl handoff` | Generate handoff manually |
| `anvl report` | Multi-session report |

### Live monitor

```bash
anvl monitor
```

Shows session health with a progress bar, tokens/turn comparison, and a table of all active sessions. Auto-refreshes every 2 seconds.

---

## Configuration

File: `~/.anvl/config.json`

| Field | Description | Default |
|-------|-------------|---------|
| `waste_threshold` | Yellow alert threshold (waste factor) | 2 |
| `handoff_waste_threshold` | Auto-handoff + block threshold | 10 |
| `min_turns_for_alert` | Minimum turns before alerting | 5 |

---

## How alerts work

ANVL hooks into Claude Code and checks session health on every turn:

1. **Health 60-100%:** No alerts — session is healthy
2. **Health 30-60%:** Warning message
   ```
   [ANVL] Session health: 45% (2.2x waste). Consider starting a new conversation soon.
   ```
3. **Health <30%:** Generates handoff automatically
   ```
   [ANVL] This session is inflated (5x). Your work has been saved to handoff.md
          Start a new conversation and say: "Read handoff.md and continue where I left off"
   ```
4. **Waste ≥10x + 20 turns:** Blocks the session (exit code 2)
   ```
   [ANVL] Session blocked -- too inflated to continue efficiently.
          Handoff saved to handoff.md
   ```

---

## License

MIT — see [LICENSE](LICENSE)

---

Developed by **IronDevz**
