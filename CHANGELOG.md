# Changelog

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
