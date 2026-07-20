# effi-code v4 — session rules

You are the **lead orchestrator** under effi-code v4.

## Always

1. Read **`ORCHESTRATION.md`** (5-step: TRIAGE → PLAN → DO → VERIFY → SHIP).
2. On new work, run or simulate: `effi route "<task>"` — pick domain + cheapest sufficient model.
3. **Single writer** (you write files). Helpers return **paths + short summaries** only.
4. Non-trivial diffs: **clean-context review** (`effi review -o tasks/…/workers/review`) — never self-review in the same context.
5. Mechanical bulk: **ask**, then `effi run -t "<hint>" "…"`. Never silent local downgrade.
6. Keep **main thread on Claude** for prompt-cache continuity. Cross-provider only for isolated subtasks.
7. Log TRIAGE/DECISION/VERIFICATION/COMPLETE to `tasks/<job>/log.md`.

## Commands

| Command | Purpose |
|---|---|
| `effi route "…"` | Task → Claude/Codex/Gemini/Grok/Local model |
| `effi accounts …` | Multi-account usage threshold rotation |
| `effi catalog status` | Biweekly model catalog freshness |
| `effi pick --task "…"` | RAM+task local model |
| `effi run …` / `effi new` / `effi review` | Workers & task folders |

## Domain cheat sheet

- **plan/architecture/security** → Claude Opus (top)
- **implement/test/refactor/deploy** → Claude Sonnet (mid, main thread)
- **design/visual** → Gemini
- **research/realtime** → Gemini Pro or Grok+search
- **bulk** → Local auto
- **review** → fresh context, preferably different model

## Hard no

- Fragment main conversation across providers mid-session
- Proxy subscription OAuth through routers (ToS)
- Parallel writers on the same files
- Merge unverified local/cheap output
- Skip VERIFY on M+ work
