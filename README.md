# ANVL

[![CI](https://github.com/juanlumanmx29/anvl/actions/workflows/ci.yml/badge.svg)](https://github.com/juanlumanmx29/anvl/actions/workflows/ci.yml)
[![PyPI version](https://img.shields.io/pypi/v/anvl-monitor.svg)](https://pypi.org/project/anvl-monitor/)
[![Python 3.11+](https://img.shields.io/pypi/pyversions/anvl-monitor.svg)](https://pypi.org/project/anvl-monitor/)
[![License: MIT](https://img.shields.io/badge/License-MIT-blue.svg)](LICENSE)

```
   █████████   ██████   █████ █████   █████ █████
  ███▒▒▒▒▒███ ▒▒██████ ▒▒███ ▒▒███   ▒▒███ ▒▒███
 ▒███    ▒███  ▒███▒███ ▒███  ▒███    ▒███  ▒███
 ▒███████████  ▒███▒▒███▒███  ▒███    ▒███  ▒███
 ▒███▒▒▒▒▒███  ▒███ ▒▒██████  ▒▒███   ███   ▒███
 ▒███    ▒███  ▒███  ▒▒█████   ▒▒▒█████▒    ▒███      █
 █████   █████ █████  ▒▒█████    ▒▒███      ███████████
▒▒▒▒▒   ▒▒▒▒▒ ▒▒▒▒▒    ▒▒▒▒▒      ▒▒▒      ▒▒▒▒▒▒▒▒▒▒▒
                  ⚒ forged by IronDevz
```

**Session monitor for Claude Code — saves your quota by detecting inflated sessions.**

---

## The problem

Claude Code resends the **entire conversation history** on every turn. This is how the API works — there's no persistent memory, so each request includes everything: system prompt, your messages, Claude's responses, tool results, all of it.

On turn 1, this might be 150K tokens. By turn 20, it's 500K. By turn 50, you're sending 1M+ tokens just to get a 2K response back. Your quota burns exponentially.

**ANVL** detects when this is happening and rotates your session before it wastes quota.

## Quick start

### 1. Install

```bash
pip install anvl-monitor
```

### 2. Verify it works

```bash
anvl --version
```

If you get `anvl: command not found` (or similar), Python's Scripts folder isn't in your PATH. Use this instead:

```bash
python -m anvl --version
```

> **Windows users:** `pip install` puts `anvl.exe` in Python's `Scripts/` folder (e.g. `C:\Users\YOU\AppData\Local\Programs\Python\Python313\Scripts\`). If `anvl` isn't recognized, either:
> - Use `python -m anvl` everywhere (always works), or
> - Add the Scripts folder to your PATH:
>   ```powershell
>   # PowerShell (run once, then restart terminal)
>   $scripts = python -c "import sysconfig; print(sysconfig.get_path('scripts'))"
>   [Environment]::SetEnvironmentVariable("Path", "$env:Path;$scripts", "User")
>   ```

### 3. Initialize

```bash
anvl init
```

That's it. ANVL runs silently in the background via Claude Code hooks. You'll only see it when your session starts inflating:

```
[ANVL] Session health: 45% (5.4x waste). Consider starting a new conversation soon.
```

When it's critical, ANVL blocks the session entirely:

```
[ANVL] Session blocked -- too inflated to continue efficiently.
       Handoff saved: handoff.md
       Start a new conversation and say: "Read handoff.md and continue where I left off"
```

---

## How health is calculated

ANVL measures session health as a **percentage from 0% to 100%**, based on a single metric: **waste factor**.

### Waste factor (dual-signal)

ANVL uses a **growth-aware** waste calculation that combines two signals:

```
Signal A (relative): actual_growth / expected_growth_p75
Signal B (absolute): current_cost / fresh_session_cost
waste = max(signal_a, signal_b)
```

- **Signal A** compares your session's growth rate against the historical p75 growth curve. If your session is growing faster than 75% of past sessions at the same turn count, waste goes up.

- **Signal B** compares the current cost per turn against what a fresh session costs. This catches sessions that started expensive and stayed expensive.

- **Calibrated baseline** = the global median of "typical turn cost" across all your sessions. Each session contributes its median tokens/turn from the first 5 turns. With enough sessions, this is a stable reference for what a normal Claude Code turn costs in your workflow.

A fresh session has waste = 1.0x. As the conversation grows and Claude resends more history, tokens/turn increases, waste goes up.

### Health percentage

Health maps waste linearly from 100% (fresh, 1x) to 0% (critical, 15x):

```
health = 100% × (15 - waste) / 14
```

Sessions with fewer than 5 turns always show 100% — waste isn't reliable with few data points.

| Waste | Health |
|:-----:|:------:|
| 1.0x | 100% |
| 3.0x | 85% |
| 5.0x | 71% |
| 8.0x | 50% |
| 12.0x | 21% |
| 15.0x | 0% |

### Weighted token cost

The monitor shows a **weighted cost** per session that approximates real quota impact using API pricing ratios:

| Token type | Weight | What it is |
|:-----------|:------:|:-----------|
| Input tokens | 1.0x | New content in the prompt |
| Cache read | 0.1x | Previously cached context (90% cheaper) |
| Cache creation | 1.25x | Writing new content to cache |
| Output tokens | 5.0x | Claude's response |

This gives you a single number that reflects actual cost, not just raw token volume. A session burning 500K tokens mostly on cache reads costs far less than one burning 500K on fresh input + output.

---

## How it works

```
You're working in Claude Code
         |
    ANVL monitors every turn via hooks
         |
    Tokens/turn growing? Waste going up?
         |
    ┌────┴────┐
    │  < 60%  │ ──→ Warning: "Consider starting a new conversation"
    │  < 30%  │ ──→ Auto-saves handoff.md
    │   = 0%  │ ──→ Blocks session (exit code 2)
    └─────────┘
         |
    You open a new conversation
         |
    "Read handoff.md and continue where I left off"
         |
    Fresh session — back to 100% health
```

### Hooks

ANVL installs three Claude Code hooks:

| Hook | When it runs | What it does |
|:-----|:------------|:-------------|
| `UserPromptSubmit` | Before Claude processes your message | Checks health, warns or blocks |
| `PostToolUse` | After each tool call | Same check during autonomous work |
| `SessionStart` | When a new session opens | Injects handoff.md context if it exists |

The `SessionStart` hook is what makes handoffs seamless — when you open a new session, Claude automatically knows there's a handoff.md to read.

### Handoff

When ANVL detects a critically inflated session, it generates `handoff.md` containing:

- Session summary and what was being worked on
- Files that were created or modified
- Commands that were run
- The last few conversation turns
- Pending/next steps

This gives the new session full context without carrying the token debt.

---

## Installation

```bash
# From PyPI (recommended)
pip install anvl-monitor

# From source (for development)
git clone https://github.com/juanlumanmx29/anvl.git
cd anvl
pip install -e .
```

**Requirements:** Python 3.11+ | Only dependency: [rich](https://github.com/Textualize/rich) (installed automatically)

> **Note:** On all platforms, `python -m anvl` works as an alternative to the `anvl` command.

---

## Setup

```bash
anvl init
```

Run once. This:
1. Creates config at `~/.anvl/config.json`
2. Installs hooks in Claude Code (`UserPromptSubmit`, `PostToolUse`, `SessionStart`)
3. Writes `CLAUDE.md` with instructions for Claude to handle handoffs

The hooks are **global** — once installed, ANVL monitors all sessions in all projects automatically. You don't need to run `anvl init` per project.

---

## Commands

| Command | Description |
|---------|-------------|
| `anvl init` | First-time setup (config + hooks + CLAUDE.md) |
| `anvl status` | Current session health, waste, tokens breakdown |
| `anvl status --json` | Machine-readable output |
| `anvl sessions` | All sessions with health status |
| `anvl sessions --active` | Only active sessions |
| `anvl monitor` | Live terminal monitor (auto-refreshes) |
| `anvl calibrate` | View global calibration baseline |
| `anvl calibrate --reset` | Reset calibration data |
| `anvl handoff` | Generate handoff manually |
| `anvl report` | Multi-session report |

### Live monitor

```bash
anvl monitor
```

Shows a live dashboard with:
- Health bar with percentage and waste factor per session
- Weighted token cost (approximates real quota impact using API pricing ratios)
- Global calibrated baseline (median turn cost across all sessions)
- Tokens wasted by inflation and tokens saved by rotation
- Update notification when a new version is available on PyPI

Auto-refreshes every 2 seconds. Press `Ctrl+C` to exit.

### Calibration

ANVL learns what a "normal" turn costs by collecting baselines from all your sessions globally. Each session that reaches 5+ turns contributes its median tokens/turn to the global baseline.

```bash
anvl calibrate
```

Shows the current global baseline, session count, and range. With 3+ sessions, calibration activates and health works from turn 1 — no warmup period needed.

---

## Configuration

File: `~/.anvl/config.json`

| Field | Default | Description |
|-------|:-------:|-------------|
| `waste_threshold` | 2 | Waste factor to start showing warnings |
| `handoff_waste_threshold` | 10 | Waste factor to block session + auto-handoff |
| `min_turns_for_alert` | 10 | Minimum turns before any alerts fire |
| `window_hours` | 5 | Rolling window for quota tracking |

---

## Alert levels

| Health | Action |
|:------:|:-------|
| 50-100% | No alerts — session is healthy |
| 20-49% | Warning: "Consider starting a new conversation soon" |
| 10-19% | **Inflated** — handoff auto-generated, strong warning |
| < 10% | **Critical** — handoff saved, session flagged for rotation |

Alerts require at least 10 turns (`min_turns_for_alert` in config) — ANVL won't warn on short sessions even if waste is high, because short sessions are cheap regardless.

**Bypass:** If ANVL blocks your session and you need to send one more message, type `anvl bypass` before your message. ANVL will skip all checks for that message.

---

## FAQ

**Q: Does ANVL slow down Claude Code?**
No. The hook runs a lightweight scan of the session file (~10ms). It doesn't parse the full JSONL — it uses a fast token counter.

**Q: What if I don't want blocking?**
Set `handoff_waste_threshold` to a very high number (e.g., 9999) in your config. You'll still get warnings.

**Q: How much quota does session rotation actually save?**
Depends on session length. A 50-turn session that gets rotated at turn 25 typically saves 40-60% of what it would have consumed. Run `anvl sessions` to see estimated savings.

---

## License

MIT — see [LICENSE](LICENSE)

---

⚒ Forged by **IronDevz**
