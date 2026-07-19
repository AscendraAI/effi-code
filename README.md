# effi-code

**A cost-optimized, multi-model coding-agent orchestration.**
Run Claude as the lead, spend the least, and keep working on a free local model when your paid quota runs out.

**English** | [한국어](README_ko.md)

[![License: Apache 2.0](https://img.shields.io/badge/License-Apache_2.0-blue.svg)](LICENSE) ![Platform](https://img.shields.io/badge/platform-macOS%20·%20Apple%20Silicon-lightgrey) ![Harness](https://img.shields.io/badge/harness-Claude%20Code-6c47ff)

> Not a framework. No server to install, nothing running in the background.
> Just a few files and a **way of working** — every decision, approval, and record lives on disk.

**Top model where it counts. Cheap models everywhere else. Local when the meter runs out.** — that's the whole philosophy.

---

## What is effi-code?

effi-code turns a single coding session (Claude Code) into a small, cost-aware **team**: a strong **Claude** lead, cheaper **Codex / Gemini** helpers, and a free **local model** running on your own machine. An orchestrator routes each task to the cheapest tier that can do it, and escalates only when it must.

It solves two everyday pains:

- **Cost** — most people send every task to the priciest model. In practice ~85% of coding tokens don't need the top tier. Send the easy work down; keep Claude for the hard judgment.
- **Quota** — paid plans have usage limits. When you hit the wall mid-flow, a local model takes over so you don't stop and wait.

## Core idea: cheapest tier first

```
local (free)  →  Codex  →  Gemini  →  Claude (top)
        try cheap first, escalate only when the result isn't good enough
```

| Tier | Role | Cost |
|---|---|---|
| **Claude** | hard design · debugging · architecture · orchestration | high → used sparingly |
| **Codex / Gemini** | implementation help · long documents · third-party review | low |
| **Local** (Ollama: Qwen / Devstral) | narrow mechanical coding · quota backstop | free (slower) |

**Memory is the filesystem.** There is no runtime state — every task, approval, and verdict is a file on disk. A dead session resumes by reading one folder.

## Get Started

### Requirements
- macOS (Apple Silicon)
- [Claude Code](https://claude.com/claude-code) — the orchestration harness
- [Ollama](https://ollama.com) — runs the local model (installed for you by `setup.sh`)

### Installation
```bash
git clone https://github.com/AscendraAI/effi-code && cd effi-code

./setup.sh                                    # installs Ollama, picks & pulls a local model that fits your Mac
ln -s "$PWD/bin/effi"      /opt/homebrew/bin/effi
ln -s "$PWD/bin/effi-run"  /opt/homebrew/bin/effi-run
ln -s "$PWD/bin/effi-pick" /opt/homebrew/bin/effi-pick
```

### Configuration
- Load `ORCHESTRATION.md` as your session's rules (e.g. as the project's `CLAUDE.md`). The orchestrator reads it on every task.
- The local model is chosen **automatically** by available memory — no config needed. Pin one with `EFFI_LOCAL_MODEL=devstral effi local`.

## Usage

```bash
effi            # normal — subscription Claude (smartest)
effi local      # quota exhausted → free local model (auto-sized to your free RAM)
effi status     # which local model fits right now
```

Delegate a boring, high-volume job straight to the local model:

```bash
effi-run "Translate these 5 UI strings to natural Korean: Save, Cancel, Delete, Loading, Done"
```

And in a normal Claude session, the orchestrator will **offer** to delegate tedious bulk work for you:

> "This is a mechanical bulk task — I can run it on the free local model to save quota. Go ahead?"

## Why it's built this way

Every choice is grounded in 2026 research and real measurement. In short:

1. **Claude leads, but don't over-spawn agents.** A strong lead + cheap workers beats a lone strong agent (Anthropic measured +90%) — but that was *research* work. For *coding*, multi-agent burns ~15× the tokens and can get *worse* from coordination overhead. So: one strong Claude thread; subagents only for genuinely parallel work.
2. **Cut cost by routing, not by weakening.** ~85% of tokens don't need the top model. Prompt caching saves up to ~90% — but fragmenting one conversation across providers *breaks* the cache and costs ~10× more, so the main thread stays in one place.
3. **Local is a backstop, not a replacement.** It covers ~80% of routine work, slower and less sharp. Auto-proxying a subscription would violate Anthropic's ToS, so switching is an honest toggle.
4. **Tedious bulk work is offered to local — with your consent.** The orchestrator asks first; it never silently downgrades.
5. **The local model is auto-sized to free memory** so your computer doesn't bog down.

Full reasoning with citations: [`docs/why.md`](docs/why.md).

## Honest limits

- Local can't match the cloud on complex, multi-file work — it's a backstop.
- Subscription users switch to local **manually** (`effi local`) — that's the ToS-clean path.
- On a busy 16GB Mac, only small local models stay smooth. Memory is the ceiling, not the tool.
- Router/local tools change monthly — check each project's latest docs before configuring.

## Docs

| File | What |
|---|---|
| [`ORCHESTRATION.md`](ORCHESTRATION.md) | operating rules the orchestrator follows |
| [`ROUTING.md`](ROUTING.md) | cost discipline + the caching trap |
| [`FALLBACK.md`](FALLBACK.md) | quota exhaustion → local |
| [`LOCAL-MODELS.md`](LOCAL-MODELS.md) | models per Mac + Ollama setup |
| [`docs/why.md`](docs/why.md) | design rationale (research + measurements) |

## License

Apache-2.0 — see [`LICENSE`](LICENSE) / [`NOTICE`](NOTICE). © 2026 AscendraAI.
