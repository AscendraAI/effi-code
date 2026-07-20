# Changelog

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
