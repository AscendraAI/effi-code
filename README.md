<div align="center">

# effi-code

### Cost-aware multi-provider coding orchestration

**CLI:** `effi` · **Version:** [`4.4.2`](VERSION)

Route every task to the right model among **Claude · Codex (OpenAI) · Gemini · Grok · Local**,  
run under one of three modes — **Apex / Cruise / Sip** — and keep working when quota runs out.

[English](README.md) · [한국어](README_ko.md)

[![License: Apache 2.0](https://img.shields.io/badge/License-Apache_2.0-blue.svg)](LICENSE)
[![CI](https://github.com/AscendraAI/effi-code/actions/workflows/ci.yml/badge.svg)](https://github.com/AscendraAI/effi-code/actions/workflows/ci.yml)
![Platform](https://img.shields.io/badge/platform-macOS%20·%20Linux-lightgrey)
![Harness](https://img.shields.io/badge/harness-Claude%20Code-6c47ff)
![Version](https://img.shields.io/badge/version-4.4.2-green)

[Quick start](#quick-start) ·
[Modes](#modes-apex--cruise--sip) ·
[How it works](#how-it-works) ·
[Commands](#commands) ·
[Repository layout](#repository-layout) ·
[Documentation](#documentation) ·
[Development](#development) ·
[Contributing](#contributing) ·
[License](#license)

</div>

---

## What is effi-code?

**effi-code is not a framework and not a background server.**  
It is a small toolkit — files, CLIs, and a way of working — that turns a Claude Code session into a **cost-aware lead orchestrator**:

| Pillar | Idea |
|--------|------|
| **Route** | Task → cheapest *sufficient* model (domain matrix + active mode) |
| **Mode** | 🚀 Apex · 🛣 Cruise · ☕ Sip — per project, switch anytime |
| **Write once** | Single writer; helpers return paths + short summaries only |
| **Verify clean** | Adversarial review in a **fresh context** (not the generator’s chat) |
| **Survive quota** | Multi-account rotation + free local (Ollama) backstop |

> **Top model where it counts. Cheap models everywhere else. Local when the meter runs out.**

Design choices are grounded in 2026 research and production practice — see [`docs/why.md`](docs/why.md).

### Auth: login is enough (API keys optional)

**You do not need Anthropic/OpenAI/Google API keys to use the core loop.**

| Path | What you need | Notes |
|------|----------------|--------|
| **Default lead** | Claude Code **subscription login** (`claude` once in browser/device flow) | `effi` / `effi cloud` uses that session — no `ANTHROPIC_API_KEY` required |
| **Helpers** | Each tool’s own **app/CLI login** (Codex, Gemini CLI, Grok, …) when you choose to spawn them | Still login-based if the product supports it |
| **Local** | Ollama + a pulled model | Zero cloud billing; no vendor API key |
| **Optional** | API keys in `effi accounts` | Only if you want **multi-key rotation**, metering, or pure API (non-subscription) backends |

Routing still *recommends* models by capability; **how you pay** is usually a plan login, not a developer API key.  
API keys are an upgrade path, not a requirement.

---

## Quick start

### Requirements

- macOS or Linux (Apple Silicon well-tested for local models)
- [Claude Code](https://claude.com/claude-code) on `PATH`, **logged in** with your plan (Pro/Max/Team — whatever you already use)
- Optional: [Ollama](https://ollama.com) for local / quota fallback (**no cloud API**)
- Optional: Codex / Gemini / Grok CLIs, each logged in the normal app way, for isolated subtasks
- Optional: API keys — only for multi-account automation or raw API access ([`docs/accounts.md`](docs/accounts.md))

### Install

```sh
git clone https://github.com/AscendraAI/effi-code.git
cd effi-code
./setup.sh                          # Ollama + recommended local model (macOS)
export PATH="$PWD/bin:$PATH"        # or symlink bin/* into /opt/homebrew/bin

# One-time: log into Claude Code (subscription) if you have not already
claude                              # complete browser/device login, then quit
```

### Wire any app repo

```sh
cd /path/to/your-app
effi init                 # tasks/ · CLAUDE.md · .effi/
effi mode ask             # pick Apex / Cruise / Sip (saved to this project)
effi doctor               # health check (API keys not required)
effi use "add rate-limit middleware + tests"
effi new auth-rate "add rate-limit middleware + tests"
effi                      # uses your Claude Code login — no API key needed
```

After edits:

```sh
effi review -o tasks/auth-rate/workers/review
effi log auth-rate COMPLETE "shipped"
```

---

## Modes (Apex · Cruise · Sip)

Modes are **project-local by default** (`.effi/mode`).  
Resolution order: `EFFI_MODE` env → **project** → global `~/.config/effi` → **Cruise**.

| | Mode | Best for | Routing bias |
|---|------|----------|--------------|
| 🚀 | **Apex** | Hard design, security, “don’t care about cost” | Top models (Opus-class); **no local primary**; ignore account usage threshold |
| 🛣 | **Cruise** | Day-to-day features (default) | Domain matrix; escalate only after failed verification |
| ☕ | **Sip** | Simple / bulk work or scarce quota | Local & cheap first; Sonnet ceiling for harder slices |

```sh
effi mode set apex                 # pin this project
effi mode set sip --global         # default for all projects
effi mode set cruise --both
effi mode check "security audit"   # importance → offer switch
```

### Task importance prompts

On `effi route` / `use` / `new`, effi scores the task:

| Importance | Examples | Suggests |
|------------|----------|----------|
| **high** | security, architecture, L/XL, production / urgent | Apex |
| **medium** | normal features, debug | Cruise |
| **low** | translate, docstring, bulk, grade S | Sip |

If the active mode is too weak or too expensive for that band, **TTY sessions ask before switching** and pin the choice to the project.

---

## How it works

### Core loop

```
TRIAGE → PLAN → DO → VERIFY → SHIP
```

1. **TRIAGE** — `effi route` / `use`: domain, grade, model, review level, mode  
2. **PLAN** — short plan in `tasks/<job>/task.md` (L/XL longer)  
3. **DO** — **single writer** on the main Claude thread; isolated helpers only  
4. **VERIFY** — tests + clean-context review (`effi review`)  
5. **SHIP** — log `[COMPLETE]`; prod deploy needs an approval gate  

### Providers (catalog-driven)

| Provider | Role examples |
|----------|----------------|
| **Claude** | Main thread (cache continuity), Opus judgment, Sonnet implement |
| **OpenAI / Codex** | Isolated implement / review subtasks |
| **Gemini** | Design, multimodal, long-context research |
| **Grok** | Realtime research, value coding |
| **Local (Ollama)** | Mechanical bulk + quota backstop (`effi pick` by free RAM) |

Matrices live in:

- [`catalog/task-routing.json`](catalog/task-routing.json) — domain → primary model  
- [`catalog/models.json`](catalog/models.json) — model ladder & costs  
- [`catalog/modes.json`](catalog/modes.json) — Apex / Cruise / Sip policy  

### Routing examples (Cruise)

```text
$ effi route --compact "redesign distributed transaction architecture"
domain=architecture … model=claude/claude-opus-4-8 cost=high

$ effi route --compact "add rate limit middleware and unit tests"
domain=implement … model=claude/claude-sonnet-5 cost=mid

$ effi route --compact "landing page UI mockup"
domain=design … model=gemini/gemini-3.5-flash

$ effi route --compact "translate 40 UI strings"
domain=bulk … model=local/<ram-picked> cost=free
```

### Quota, accounts & local fallback

**Most users:** stay on Claude Code login → when the plan hits a wall, run `effi local` (Ollama). No API key.

```sh
effi                  # subscription / plan login
# … hit usage limit …
effi local            # free local backend (MCP stripped for small models)
effi pick --task "bulk translate"
effi run -t "translate" "…"                 # generate (stdout)
effi edit path.py "add type hints"          # rewrite → path.py.effi-new + diff
effi edit --apply-only path.py              # accept after review
```

**Optional — multi-account / API rotation** (when you *do* have keys or isolated OAuth profiles):

```sh
effi accounts init
effi accounts threshold 80
# either: api_key_env exports  OR  oauth_profile config_dir per account
effi accounts meter work-primary 72
effi                             # pick account under threshold
```

See [`docs/accounts.md`](docs/accounts.md).

> **ToS:** do not proxy subscription OAuth through third-party routers.  
> Prefer the honest toggle (`effi` ↔ `effi local`) or official multi-login / API setups.

### Philosophy (short)

1. Multi-agent coding often costs **~15× tokens** — use one strong lead; parallelize only true read/review work ([Anthropic](https://www.anthropic.com/engineering/multi-agent-research-system)).  
2. **Writes stay single-threaded**; review in a clean context ([Cognition](https://cognition.com/blog/multi-agents-working)).  
3. Save money with **routing + prompt-cache continuity**, not by silently dumbing down the lead.  
4. Local is a **backstop**, not the architect.

---

## Commands

| Command | Purpose |
|---------|---------|
| `effi` / `effi cloud` | Claude Code session (mode banner + account select) |
| `effi local` | Claude Code on Ollama (MCP stripped for small models) |
| `effi mode …` | Show / set / ask / check Apex·Cruise·Sip |
| `effi route "…"` | Task → model (mode-aware; may prompt on importance) |
| `effi use "…"` | Route + how to run (`--exec` for Claude) |
| `effi init` | Wire project: `tasks/`, `CLAUDE.md`, `.effi/` |
| `effi doctor` | Health check |
| `effi new <name> [goal]` | Scaffold task folder under **project root** |
| `effi log <name> <TAG> <msg>` | Append to `tasks/<name>/log.md` |
| `effi review [-o dir]` | Clean-context review pack (diff + brief) |
| `effi pick` / `effi run` | Local model pick / mechanical **generate** worker |
| `effi edit <file> "…"` | Local **file rewrite** → `.effi-new` sidecar + diff |
| `effi accounts …` | Multi-account threshold rotation |
| `effi catalog …` | Biweekly model catalog status / research / bump |
| `effi status` | Snapshot: mode, project, Ollama, accounts |

```sh
effi help
```

---

## Repository layout

| Path | Contents |
|------|----------|
| [`bin/`](bin/) | CLI entrypoints (`effi`, `effi-mode`, `effi-route`, …) |
| [`lib/effi_core.py`](lib/effi_core.py) | Routing, modes, accounts, doctor, project roots |
| [`catalog/`](catalog/) | `models.json`, `task-routing.json`, `modes.json` |
| [`config/`](config/) | Example accounts & user config (no secrets) |
| [`templates/`](templates/) | `task`, `log`, `brief`, `review`, `handoff` |
| [`docs/`](docs/) | Why, domains, accounts guides |
| [`ORCHESTRATION.md`](ORCHESTRATION.md) | Operating rules for the lead agent |
| [`CLAUDE.md`](CLAUDE.md) | Drop-in session rules for any project |
| [`tests/`](tests/) | Unit tests (routing, modes, importance) |
| [`setup.sh`](setup.sh) | macOS-oriented bootstrap |

> Task artifacts belong in **your app’s** `tasks/` (via `effi init` / `effi new`), not inside this toolkit clone.

---

## Documentation

| Doc | Description |
|-----|-------------|
| [`ORCHESTRATION.md`](ORCHESTRATION.md) | 5-step loop, modes, gates |
| [`ROUTING.md`](ROUTING.md) | Cost discipline & cache trap |
| [`CLAUDE.md`](CLAUDE.md) | Session rules pointer |
| [`FALLBACK.md`](FALLBACK.md) | Quota → local toggle |
| [`LOCAL-MODELS.md`](LOCAL-MODELS.md) | Local ladder & RAM rules |
| [`SETUP.md`](SETUP.md) | Setup walkthrough |
| [`docs/why.md`](docs/why.md) | Research citations |
| [`docs/domains.md`](docs/domains.md) | Plan → deploy pipeline |
| [`docs/accounts.md`](docs/accounts.md) | Multi-account setup |
| [`CONTRIBUTING.md`](CONTRIBUTING.md) | Catalog updates & PR rules |
| [`CHANGELOG.md`](CHANGELOG.md) | Release history |

---

## Development

```sh
export PATH="$PWD/bin:$PATH"
export PYTHONPATH="$PWD/lib"

python3 -m unittest discover -s tests -v
effi doctor
effi route --compact "add rate limit middleware and unit tests"
effi mode list
```

CI runs the same tests on every push/PR: [`.github/workflows/ci.yml`](.github/workflows/ci.yml).

---

## Contributing

See [`CONTRIBUTING.md`](CONTRIBUTING.md).

Highlights:

- Update model IDs only from **official provider docs** (`effi catalog research`).  
- Do not regress: single-writer, main-thread cache lock, clean-context review, project-root tasks.  
- Never commit real API keys (`~/.config/effi/accounts.json` stays local).

---

## Honest limits

- Local models do not match cloud on hard multi-file work.  
- `effi-edit` refuses large files (default 8 k chars) so small models never quiet-truncate.  
- Subscription users switch to local **manually** (`effi local`) — ToS-clean.  
- Busy 16 GB machines stay on small local models — memory is the ceiling.  
- Agent Teams (Claude Code experimental) are optional and expensive — XL only.  
- Catalog IDs/prices drift; re-check every ~14 days.

---

## License

First-party code is licensed under the **Apache License, Version 2.0** — see [`LICENSE`](LICENSE) and [`NOTICE`](NOTICE).

Copyright © 2026 AscendraAI.
