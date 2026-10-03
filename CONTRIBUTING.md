# Contributing to effi-code

## Dev setup

```bash
git clone https://github.com/AscendraAI/effi-code && cd effi-code
export PATH="$PWD/bin:$PATH"
export PYTHONPATH="$PWD/lib"
git config core.hooksPath .githooks   # pre-push runs verify --fast
bash scripts/verify.sh                 # 0 pass · 1 found (❌) · 2 cannot judge (⛔ — not a pass)
effi doctor
```

`scripts/verify.sh` is the single completion check — CI runs the same script.
Tests run in a clean copy with an empty `HOME`, so your own `.effi/mode` or
`~/.config/effi` can't make local results differ from CI. Fixed a bug? Run
`/lesson` in Claude Code to turn it into a check instead of a prose rule.

## What to change where

| Area | Files |
|---|---|
| Model IDs / prices / local RAM | `catalog/models.json` |
| Task domain → primary model | `catalog/task-routing.json` |
| Routing / accounts / doctor logic | `lib/effi_core.py` |
| CLIs | `bin/effi*` |
| Operating rules | `ORCHESTRATION.md`, `ROUTING.md`, `CLAUDE.md` |
| Evidence | `docs/why.md` |

## Biweekly catalog update

1. `effi catalog research` — open official model pages  
2. Edit `catalog/models.json` and `catalog/task-routing.json`  
3. `effi catalog bump` — set `updated_at` / `next_review_due` (+14d)  
4. `python3 -m unittest discover -s tests -v`  
5. PR with source links in the description  

Do **not** invent model IDs. Prefer primary docs:

- https://platform.claude.com/docs/en/about-claude/models  
- https://developers.openai.com/api/docs/models  
- https://ai.google.dev/gemini-api/docs/models  
- https://docs.x.ai/docs/models  
- https://ollama.com/search?q=coding  

## Design constraints (do not regress)

1. **Single writer** for code edits  
2. **Main thread cache lock** on Claude (no mid-session provider hop)  
3. **Clean-context review** for M+ changes  
4. **Local = task + RAM pick**, not one fixed model  
5. **No subscription OAuth proxy** (ToS)  
6. **Tasks live in the user project**, not the toolkit install path  

## PR checklist

- [ ] `bash scripts/verify.sh` passes  
- [ ] `effi route` still maps architecture→opus, bulk→local, design→gemini  
- [ ] Docs / CHANGELOG / VERSION updated if user-facing  
- [ ] No secrets (`accounts.json` stays local under `~/.config/effi/`)  

## License

Apache-2.0. By contributing, you agree your contributions are under the same license.
