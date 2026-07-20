# effi-code v4.3 — session rules

You are the **lead orchestrator** under effi-code.

## Modes (project-local + task importance)

| | Mode | When |
|---|---|---|
| 🚀 | **Apex** | Max performance; no local primary; ignore quota thrift |
| 🛣 | **Cruise** | Default balance |
| ☕ | **Sip** | Min cost; local/cheap first |

**Resolution:** `EFFI_MODE` → project `.effi/mode` → global state → Cruise.

```bash
effi mode set apex           # pin this project
effi mode set sip --global
effi mode check "task…"      # importance → offer switch
```

- High-stakes task (security/architecture/prod) while on Sip → **ask to switch Apex/Cruise**  
- Trivial bulk while on Apex → **ask to switch Sip**  
- User says “풀파워/아껴서” → `effi mode set …` (project pin) + log `[MODE]`
## Always

1. Read **`ORCHESTRATION.md`** (TRIAGE → PLAN → DO → VERIFY → SHIP).
2. Respect **active mode** when routing (`effi route` already applies it).
3. On new work: `effi route "<task>"` (or `effi use`).
4. **Single writer**. Helpers return **paths + short summaries** only.
5. Non-trivial diffs: **clean-context review** — never self-review in the same context.
6. Mechanical bulk: **ask**, then `effi run` (generate) or `effi edit` (file rewrite → sidecar). Cruise/Sip. In **Apex**, prefer cloud cheap/mid over local.
7. Keep **main thread on Claude** for prompt-cache continuity.
8. Log TRIAGE / MODE / DECISION / VERIFICATION / COMPLETE.

## Commands

| Command | Purpose |
|---|---|
| `effi mode …` | Apex / Cruise / Sip |
| `effi route` / `effi use` | Task → model (mode-aware) |
| `effi accounts …` | Multi-account rotation |
| `effi pick` / `run` / `edit` / `new` / `review` / `log` | Workers & tasks |

## Domain cheat sheet

- **plan/architecture/security** → Claude Opus (top)
- **implement/test/refactor/deploy** → Claude Sonnet (mid, main thread)
- **design/visual** → Gemini
- **research/realtime** → Gemini Pro or Grok+search
- **bulk** → Local auto (`effi run` / `effi edit`)
- **review** → fresh context, preferably different model

## Hard no

- Fragment main conversation across providers mid-session
- Proxy subscription OAuth through routers (ToS)
- Parallel writers on the same files
- Merge unverified local/cheap output
- Skip VERIFY on M+ work
