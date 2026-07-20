# effi-code

**v4.3 (`VERSION` 4.3.0) — Multi-provider orchestration with 3 user modes.**  
🚀 **Apex** · 🛣 **Cruise** · ☕ **Sip** — pick max performance, balanced thrift, or minimum cost anytime.

Project name: **effi-code** (CLI: `effi`).

**English** | [한국어](README_ko.md)

[![License: Apache 2.0](https://img.shields.io/badge/License-Apache_2.0-blue.svg)](LICENSE) [![CI](https://github.com/AscendraAI/effi-code/actions/workflows/ci.yml/badge.svg)](https://github.com/AscendraAI/effi-code/actions/workflows/ci.yml) ![Orchestration](https://img.shields.io/badge/orchestration-v4.3-green) ![Harness](https://img.shields.io/badge/harness-Claude%20Code-6c47ff)

> Not a heavyweight framework. Files + small CLIs + a way of working.  
> **Right model per task. Single writer. Clean-context verify. Cache-safe main thread.**

---

## What v4 adds

| # | Capability |
|---|---|
| 1 | **Task → optimal model** across Claude / OpenAI-Codex / Gemini / Grok / Ollama (`effi route`) |
| 2 | **Multi Claude accounts** with configurable usage threshold (`effi accounts threshold 80`) |
| 3 | **Task + RAM local pick** (not one fixed model) + **14-day catalog review** (`effi catalog`) |
| 4 | **Full delivery loop** — plan → architecture → design → implement → test → review → deploy |

## Quick start

```bash
git clone https://github.com/AscendraAI/effi-code && cd effi-code
./setup.sh
export PATH="$PWD/bin:$PATH"

# Session rules in any project
ln -s /path/to/effi-code/CLAUDE.md ./CLAUDE.md

# Multi-account (optional)
effi accounts init
effi accounts threshold 80
effi accounts meter work-primary 10

# In any app repo
cd /path/to/your-app
effi init
effi mode ask        # 🚀 Apex · 🛣 Cruise · ☕ Sip
effi doctor
effi use "add rate-limit middleware + tests"
effi new auth-rate "add rate-limit middleware + tests"
effi                 # asks mode if unset; then Claude cloud
```

### Modes

| | | |
|---|---|---|
| 🚀 **Apex** | Max performance | Top models, no local primary, ignore usage threshold |
| 🛣 **Cruise** | Balanced (default) | Domain matrix + escalate only when needed |
| ☕ **Sip** | Min cost | Local/cheap first; Sonnet ceiling for hard bits |

```bash
effi mode set apex    # 1 / max / 풀파워
effi mode set cruise  # 2 / balance
effi mode set sip     # 3 / thrift / 알뜰
```


## Routing examples

```text
$ effi route "분산 트랜잭션 아키텍처 재설계"
primary: claude/claude-opus-4-8  cost≈high

$ effi route "일반 기능 구현과 단위 테스트"
primary: claude/claude-sonnet-5  cost≈mid

$ effi route "UI 목업 디자인"
primary: gemini/gemini-3.5-flash

$ effi route "40개 문자열 한국어 번역"
primary: local/<ram-picked-model>  cost≈free
```

Matrix: [`catalog/task-routing.json`](catalog/task-routing.json) · Models: [`catalog/models.json`](catalog/models.json)

## Core loop

```
TRIAGE → PLAN → DO → VERIFY → SHIP
```

- **TRIAGE:** `effi route` → domain + model + review level  
- **DO:** single writer; cross-provider only on **isolated** subtasks  
- **VERIFY:** clean-context adversarial review (`effi review`)  
- **Main thread stays on Claude** so prompt cache is not shattered (~10× on misses)

## Accounts

```bash
effi accounts init
effi accounts threshold 75          # your choice
effi accounts meter work-primary 72
effi accounts list
# effi (cloud) auto-selects an account under the threshold
```

API keys via env vars (see `config/accounts.example.json`). OAuth profiles via isolated `config_dir`.  
No subscription OAuth proxying (Anthropic ToS).

## Local models

```bash
effi pick --task "docstring bulk"
effi run -t "번역" "…"
effi local          # full Claude Code on Ollama when quota is gone
```

## Catalog (every ~2 weeks)

```bash
effi catalog research   # official docs checklist
# edit catalog/models.json + task-routing.json
effi catalog bump
```

## Docs

| File | |
|---|---|
| [`ORCHESTRATION.md`](ORCHESTRATION.md) | v4 operating rules |
| [`ROUTING.md`](ROUTING.md) | cost + cache discipline |
| [`docs/domains.md`](docs/domains.md) | plan→deploy pipeline |
| [`docs/why.md`](docs/why.md) | citations (2026-07) |
| [`CLAUDE.md`](CLAUDE.md) | drop-in session rules |
| [`CHANGELOG.md`](CHANGELOG.md) | version history (`VERSION` 4.2.0) |
| [`CONTRIBUTING.md`](CONTRIBUTING.md) | catalog updates + PR rules |
| [`docs/accounts.md`](docs/accounts.md) | multi-account keys + threshold rotation |

## Philosophy (evidence-backed)

1. Strong lead + cheap workers beats all-Opus on cost; multi-agent coding is often a **15× token trap** (Anthropic).  
2. Writes single-threaded; review in a **fresh context** (Cognition).  
3. Save money by **routing + cache continuity**, not by silently weakening the lead.  
4. Local is backstop + bulk — not the architect.

## License

Apache-2.0 — [`LICENSE`](LICENSE) / [`NOTICE`](NOTICE). © 2026 AscendraAI.
