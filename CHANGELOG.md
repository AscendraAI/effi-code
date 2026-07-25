# Changelog

## 4.6.2 — 2026-07-25

### Added — local-driver guardrail
- `effi local` no longer silently launches a model too small to drive Claude
  Code's agent loop. Such models (no `agent_local` catalog role — micro/fast/mid
  tiers) can't follow the large system prompt + tool schema and **emit raw
  tool-call JSON as plain text** (`{"name":"Write",…}`). The launcher now warns,
  explains the symptom, and on a TTY prompts `[c=cloud / y=local / N=cancel]`
  (`c` re-launches the cloud session). Non-interactive runs warn and proceed.
- `effi local` stops swallowing `effi-pick`'s stderr, so the RAM-pressure /
  micro-fallback warnings ("no model fits budget; using micro fallback",
  "RAM tight") are actually visible.
- Core: `local_driver_check(pick)` → `{ok, model, tier, reason}`, keyed on the
  catalog's `agent_local` role. +4 tests (107 total).

## 4.6.1 — 2026-07-25

### Fixed — honest Gemini connect hint
- `connect_hint("gemini")` no longer points to a non-existent `agy` login when
  the CLI is absent. Antigravity ships as an **IDE**, not a gemini-style pipe
  CLI, so the hint now adapts: with `agy` on PATH it offers `effi connect
  gemini`; without it, it leads with what actually works — `GEMINI_API_KEY` for
  effi routing, and the Antigravity IDE app for interactive use — and says
  plainly that the `agy` CLI does not exist.
- `format_connect` shows the "↳ 바로 실행" line only when the login CLI is
  actually present (`login_available`); otherwise it would just re-print
  install guidance as noise.
- `effi connect <p>`'s CLI-absent message no longer promises "install then
  re-run" (misleading for phantom CLIs) — it points at the real connect path.
- +1 test (103 total).

## 4.6.0 — 2026-07-25

### Added — Onboarding & Guided Connect (Layer 0)
- **`effi connect`** — session-start onboarding: explains what effi-code is
  (`--intro`), shows live provider connection status, and guides connecting any
  missing provider. `effi connect <provider>` runs that provider's **own**
  first-party login in a TTY (`codex login`, `agy`, `claude`, `grok`,
  `ollama serve`); if the login CLI isn't installed it prints install guidance.
  `--json` lets the assistant re-check after the user logs in.
- **SessionStart hook** now drives the full welcome when a project has **no
  pinned mode** (not yet onboarded): intro + how-to-connect + an `[effi:action]`
  block asking the assistant to introduce effi-code, help connect missing
  providers, then ask the task and set the mode. Once pinned, it collapses to
  the compact preflight table — no re-onboarding every session.
- Core: `onboarding_intro`, `connect_hint`, `connect_command`,
  `connect_report`, `format_connect`. 13 new hermetic tests (102 total).

### Notes
- ToS: connect only points to / runs each provider's native auth — it never
  proxies subscription OAuth through a router (hard-no). Gemini interactive CLI
  is `agy` (the legacy `gemini` CLI reached EOL 2026-06-18).
- Design: `docs/02-design/onboarding-connect.md`.

## 4.5.0 — 2026-07-25

### Added — Model Transparency & Credit Advisor (3-layer)
- **`effi preflight`** — checks codex/gemini/claude/grok connection live
  (existence + optional `--probe` API call) and recommends a mode from
  connection health, estimated USD headroom, and task importance.
- **`effi providers`** — per-provider usage detail + `budget <id> <usd>` /
  `record` / `reset`. Credit is a **local USD estimate**; subscription/free
  providers never show a fake balance.
- **`effi statusline`** + **`effi hooks [install]`** — always-on statusLine
  (mode · active model · real session `$cost` · Claude 5h headroom) and a
  SessionStart preflight banner + UserPromptSubmit mode nudge. Real Claude
  spend is captured via the stable `cost.total_cost_usd` statusLine field.
- `catalog/providers.example.json` — zero-config provider registry.
- 48 new tests (preflight / ledger / advisor).

### Changed
- Gemini path: `gemini` CLI reached EOL 2026-06-18 → **Antigravity CLI `agy`**
  (Google-account OAuth). The Gemini **API** (`GEMINI_API_KEY`) is unaffected
  and kept for routing. `doctor` now hints `agy` when `gemini` is absent.

### Notes
- Transcript parsing is intentionally avoided (format is version-internal);
  cloud cost is captured from the stable statusLine cost field instead.

## 4.4.5 — 2026-07-21

### Added
- Social / README hero card (`docs/assets/og-card.png`) for launches and share previews

## 4.4.4 — 2026-07-21

### Fixed
- CI smoke: `effi-edit --help` now exits 0 (was 1, which failed the badge after v4.4.0)

## 4.4.3 — 2026-07-21

### Added
- Soft CTA for optional practical document kit (OSS stays free)
- GitHub Issue template: **Package interest** (`package-interest` label)

## 4.4.2 — 2026-07-21

### Changed
- **Catalog re-verification (2026.07.21)** against official docs:
  - Claude · OpenAI · Gemini · Grok API IDs confirmed current
  - Local ladder: `qwen3-coder:30b`, `qwen3-coder-next`, `devstral:24b`, `gpt-oss:20b`,
    `qwen2.5-coder:14b` (+ measured 7b/3b/1.5b)
  - Added `last_verified_at`, per-model `api_id` / `status` / notes
- `effi catalog research` / `show` surface verification status
- Routing integrity test: domain models must exist in `models.json`

## 4.4.1 — 2026-07-21

### Fixed
- **`effi` / `effi cloud` mode prompt** — global `~/.config/effi` mode no longer skips the ask.
  Prompt runs when the **project** has no `.effi/mode` (and no `EFFI_MODE`); global is Enter-default only.
- `effi mode clear [--global|--both]` to drop pins
- `effi mode` status explains whether the next cloud launch will ask

## 4.4.0 — 2026-07-21

### Added
- **`effi-edit`** — local-tier **file-edit** delegation (twin of `effi-run` generate)
  - Full-file rewrite → `<file>.effi-new` sidecar (non-destructive by default)
  - Unified diff on stdout; `--apply` / `--apply-only` to accept
  - Size guard (default 8000 chars) refuses large files — no quiet truncation
  - Fence stripping for messy model output; `effi pick` for model choice
- Core helpers: `strip_code_fences`, `check_edit_size`, `sidecar_path`, `unified_diff`, `ollama_chat`
- ORCHESTRATION cascade note: bulk file edits → local `effi-edit` first

## 4.3.1 — 2026-07-21

### Added
- **Project-local mode pin** — `.effi/mode` (default scope for `effi mode set`)
- **Task importance check** — high/medium/low → suggest Apex/Cruise/Sip
- Interactive switch prompt on `effi route` / `use` / `new` when mode mismatches task
- `effi mode check "task"` · `effi mode set … --global|--both`
- Resolution order: `EFFI_MODE` → project → global → Cruise

## 4.3.0 — 2026-07-21

### Added
- **3 orchestration modes** (user-selectable anytime):
  - 🚀 **Apex** — max performance, no local primary, ignore quota threshold
  - 🛣 **Cruise** — performance + cost balance (classic effi)
  - ☕ **Sip** — minimum cost, local/cheap first
- `effi mode` / `effi mode set` / `effi mode ask`
- `catalog/modes.json` · mode-aware `effi route`
- Session start asks for mode if unset (`effi cloud`)

## 4.2.1 — 2026-07-21

### Added
- `docs/accounts.md` — multi-account API key / threshold setup guide
- Doctor hints when accounts exist but `api_key_env` is unresolved

### Fixed
- CI workflow on `master` (pushed via SSH; Actions green)

## 4.2.0 — 2026-07-20

Project name remains **effi-code**.

### Added
- `effi use "task"` — route + practical launch steps (`--exec` starts Claude cloud when primary is Claude)
- `effi log <task> <TAG> <msg>` — append to project `tasks/<task>/log.md`
- `CONTRIBUTING.md` — catalog update + design constraints
- Linux `/proc` memory stats for doctor/pick outside macOS
- `.github/workflows/ci.yml` — unit tests + CLI smoke

## 4.1.0 — 2026-07-20

Project name remains **effi-code**.

### Added
- `effi init` — wire any project (`tasks/`, `CLAUDE.md` link, `.effi-root`)
- `effi doctor` — health check (catalog, CLIs, accounts, local pick)
- `tests/test_route.py` — routing unit tests
- Tasks now scaffold under **project root** (cwd / git root / `EFFI_PROJECT`), not the toolkit install path

### Fixed
- Using effi as a global toolkit no longer drops task folders inside the clone by default

## 4.0.0 — 2026-07-20

Project name remains **effi-code**.

### Added
- Multi-provider task routing: Claude · OpenAI/Codex · Gemini · Grok · Local (`effi route`)
- Catalog-driven model matrix (`catalog/models.json`, `catalog/task-routing.json`)
- Claude multi-account rotation with user-defined usage threshold (`effi accounts`)
- Task + RAM aware local model pick (`effi pick --task`)
- Biweekly catalog review workflow (`effi catalog research|bump`)
- Domain pipeline docs: plan → deploy (`docs/domains.md`)
- Session drop-in rules (`CLAUDE.md`)
- Clean-context review pack (`effi review`), task scaffold (`effi new`)

### Changed
- Orchestration loop v4: TRIAGE → PLAN → DO → VERIFY → SHIP
- Main-thread cache lock on Claude; cross-provider only for isolated subtasks
- Local models are no longer a single fixed default

### Evidence base
- Official model pages (Anthropic, OpenAI, Google, xAI, Ollama) checked 2026-07-20
- Anthropic multi-agent research + Cognition multi-agent update (single-writer, clean review)

## 3.x — earlier
- Capacity-aware local pick, local delegation (`effi-run`), subscription↔local toggle, ToS-safe fallback
