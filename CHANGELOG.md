# Changelog

## 4.8.0 — 2026-07-26

### Added — `effi trust`: what third-party code your Claude Code runs, and what changed (2026-10-03)
`scan` (read-only) · `list` · `accept`. Inventories MCP servers (user, local and
project scope), marketplaces, plugins and the hook events they register,
user skills (content-hashed, scanned for risky shapes), and your own hooks.
- 🔴 unpinned MCP (`pkg@latest` / no version via npx·uvx·bunx·pnpx) · enabled
  third-party plugins hooking PreToolUse/UserPromptSubmit/PermissionRequest… ·
  skills that pipe downloads into a shell, decode base64, eval output, carry
  hidden/bidi unicode or prompt-injection phrasing.
- 🟡 third-party marketplaces (Anthropic doesn't review them) · skills with no
  recorded source — grouped into one line, not one per skill.
- **The baseline is the point**: `accept` records a fingerprint per item; every
  later scan flags anything **CHANGED since you approved it** (🔴) or new (🟡) —
  the update-after-approval attack (postmark-mcp, CVE-2025-54136).
- Secrets never reach the output or the lock: MCP env/header *values* are
  dropped before fingerprinting (key names stay), so rotating a key isn't a change.
  The lock is `~/.config/effi/trust-lock.json`, mode 600.
- Fingerprints are keyed (HMAC, per-machine key in `~/.config/effi/trust-key`),
  plugin fingerprints include their full content, skill hashes stream every
  file and follow symlinked dirs once; hook commands, URL credentials/queries
  and option values after flags are never printed or stored.
- A clean-context review found 13 issues in the first cut — hook-command and
  URL secrets reaching output, edited hooks reading as "new" instead of
  CHANGED, plugin code changes going unseen, crashes on real config shapes,
  `accept` dropping other projects' approvals, floating versions read as
  pinned, a corrupt lock passing silently. Each has a regression test
  (tests/test_trust.py, 22 tests).
- First run on the author's machine: 1 🔴 (an unpinned `@latest` MCP), 4
  third-party marketplaces, 13 skills with no recorded source.

### Added — `effi harness`: a verification floor for any repo (2026-10-03)
`scan` → `plan`/`show` → `apply [--arm]` → `prove`. Generates one
`scripts/verify.sh` (0 pass · 1 found · 2 cannot judge — a step that counted
nothing is never a pass), a pre-push gate, an edit-time syntax hook, an opt-in
Stop verify and deny rules. Detects python (syntax · unittest per test dir ·
pytest · `--selftest` modules), shell, node (lint · typecheck · test · tsc) and
caps CLAUDE.md/AGENTS.md at 150 lines; a stack it can't check yields ⛔, not ✅.
- **Writes only on `apply`, never overwrites** — a differing file gets `*.effi-new`.
  `--arm` refuses when `core.hooksPath` points elsewhere or `.git/hooks` has live hooks.
- **`prove` breaks a temp copy on purpose** (syntax error, failing test or
  selftest, missing interpreter) and passes only when verify names *that* file
  — a fault in an already-red tree is not a proof. The real tree is never touched.
- First real install: picknow-homepage (Next.js) — verify 3.5 s, `prove` 4/4
  including an injected TypeScript type error (the temp copy links the real
  `node_modules`, since it is gitignored).
- `scan` also flags unpinned MCP servers (`@latest`), oversized context files
  and a gitignored `.claude/`.
- Why a floor and not recommendations: Anthropic's `claude-code-setup` already
  recommends project-specific skills well; measured against picknow-ops it
  missed the judging floor (docs/01-plan/v5-direction.md §6.2).
- A clean-context review found 8 false-pass paths before release (empty floor,
  non-ASCII paths, one test dir only, subdirectory arming, substring proofs,
  disarmed `.git/hooks`, string-mention selftests, zsh as bash); each has a
  regression test in `tests/test_harness.py` (25 tests).

### Changed — subscription OAuth profiles are no longer rotated (2026-10-03)
Automatic threshold rotation (`effi accounts select` without an id, `meter`,
`env`, `apply`, `effi`) now moves between **API-key accounts only**. An
`oauth_profile` is used only when you name it: `effi accounts select --id <id>`.
Cycling several Claude subscriptions to get past usage limits is the pattern
Anthropic's terms bar for third-party tools, and effi is heading for a public,
commercial release. If every enabled account is a profile, automatic selection
returns `no_rotatable_accounts` instead of picking one. Tests: `tests/test_accounts.py`.

### Fixed — CI red since v4.7.1: launch line and resume screen (2026-10-03)
- **Resume/compact showed the whole onboarding intro** in any project without
  `.effi/mode` — the one-line promise only held on machines with a mode pin,
  which is why it passed locally and failed in CI.
- **The one-line status had no width cap on its warning**, so it outgrew 110
  columns the day the catalog review date lapsed. It is now trimmed to 100.
- New dev harness (ported from picknow-ops): `scripts/verify.sh` (0 pass ·
  1 found · 2 cannot judge) runs tests in a clean copy with an empty `HOME`,
  so local results match CI; `.githooks/pre-push`; project Claude Code hooks
  (syntax check on edit, opt-in verify on Stop); `/lesson`. CI runs the same script.

### Fixed — Gemini, after two wrong premises in a row
Both earlier stories about Gemini were wrong, in opposite directions:

- v4.6.1: *"the `gemini` CLI died 2026-06-18, use Antigravity's `agy`"* — false.
  `@google/gemini-cli` is actively shipping (stable **v0.52.0**), and no `agy`
  binary exists; Antigravity is an IDE. That premise pointed the registry at a
  login that does not exist.
- This release's own draft: *"so `oauth-personal` / Login with Google works"* —
  also false. Google **stopped serving Gemini Code Assist for individuals on
  2026-06-18 — free, Google AI Pro and AI Ultra alike**. Paying does not buy a
  way through. The login still succeeds, still writes a full
  `~/.gemini/oauth_creds.json`, and then the first call is refused — measured,
  not inferred:

  ```
  $ gemini -p "say OK"     # rc=55
  IneligibleTierError: This client is no longer supported for Gemini Code Assist
  for individuals … reasonCode: UNSUPPORTED_CLIENT, tierId: free-tier
  ```

  This is the worst failure shape available: authenticate, then get refused.
  `UNSUPPORTED_CLIENT` is not even in the CLI's own `IneligibleTierReasonCode`
  enum — the server issues it, so upgrading the CLI cannot help.

**Code Assist Standard/Enterprise was not retired** and still works, so the
retirement is scoped rather than blanket: `oauth_retired_unless_env:
"GOOGLE_CLOUD_PROJECT"` means a licensed GCP project keeps its 🟢. Marking a
live login dead would be the same bug as the false 🟢, only mirrored.

So on a personal account Gemini has exactly one routable credential —
`GEMINI_API_KEY`:
- Registry: `cli: gemini` kept, `agy` and `oauth_auth_type` **removed**, new
  `oauth_retired: "2026-07"` + `oauth_retired_note` + `api_key_url`.
  `subscription: true` dropped — there is no subscription path through the CLI.
- `effi connect gemini` **no longer execs anything**. It prints how to get a key
  (aistudio.google.com/apikey), what to export, and that AI Pro/Ultra means the
  Antigravity IDE — which has no pipe CLI and so is not a routing target.
- `effi doctor` reports `개인용 OAuth 폐기 — export GEMINI_API_KEY=… 필요`
  instead of checking for a credential file the server rejects.
- Routing steps hand over `gemini -m <model> -p "…"` (`-o json`) and say the key
  is required, replacing the old "subscription login means you need no key".

### Added — `antigravity` provider (the surviving subscription path), measured
Google's migration target for personal Gemini subscriptions is the Antigravity
CLI, and unlike v4.6.1's `agy` guess, this one was installed and run before being
written down. What it actually does (agy 1.1.7, darwin_arm64):

- **Subscription auth works with no API key.** `agy models` answered instantly
  off the OS keyring — it reuses the Antigravity IDE login. It also exposes
  `claude-sonnet-4-6`, `claude-opus-4-6-thinking` and `gpt-oss-120b-medium`
  alongside the Gemini models.
- **`-p/--print` is a real, documented flag** ("Run a single prompt
  non-interactively and print the response"), with `--model`, `--effort`, and
  `--mode`. The answer does land on stdout.
- **It exits only when stdout is a file.** `agy -p "…" > out.txt` returns rc=0
  in ~39s (twice). Hand it a **pipe** — `$(agy …)`, `agy … | jq`, or anything
  capturing stdout — and the answer still arrives but the process waits forever.
  `--print-timeout` does not rescue the pipe case, and at `20s` it suppresses
  the output entirely.
- **And it is slow to start.** First output at **38.1s** for a trivial prompt
  (twice, identical), 53.5s for a 200-word answer. ~38s is fixed overhead.

Registered as a provider so preflight shows the subscription is live, with the
latency in the table itself (`design/research(구독·기동~40s)`). **Routing defaults
are unchanged** — effi only ever prints provider commands, never execs them, so
nothing silently hands you a process that will not terminate.

### Fixed — provider-id column ate its own gap
`format_preflight` / `format_providers` padded the id with a hard-coded `:<9`,
so `antigravity` (11 chars) rendered as `antigravitycli:agy`. Both now size the
column from the data (`_id_col_width`, floor of 9 so short lists look the same).
`best_for` also takes a provider-specific label before falling back to the model
family it bills as.

### Changed — two steps of connection honesty
1. **A login is a credential file, not a binary on PATH.** New
   `oauth_creds_path()`: a provider that declares `oauth_creds` (str or list of
   candidates) is only 🟢 when a **non-empty credential file actually exists** —
   `~/.gemini/oauth_creds.json`, `~/.codex/auth.json`, `~/.grok/auth.json`.
   Installed-but-never-logged-in is now 🟡 `미로그인`, which the old
   CLI-presence heuristic reported as connected. Providers that keep credentials
   off the filesystem (Claude Code → macOS Keychain) omit the field and keep the
   old heuristic, so nothing regresses.
2. **A credential file is not proof the backend still honours it.** Gemini is
   the worked example: a complete, freshly-written creds file bought a false 🟢
   in preflight while every call was refused. A provider that declares
   `oauth_retired` now has its creds ignored for connection purposes and reads
   🟡 `oauth 폐기 → 키 필요` — never `미로그인`, because the user *did* log in.
   The status column truncates, so that phrase is placed ahead of `cli:…`: the
   one thing to act on has to survive the cut.
- Both are registry-driven: a future retirement needs `oauth_retired` (+ optional
  `oauth_retired_note`, `api_key_url`) and no code change.
- `probe_provider` returns `has_oauth` / `oauth_creds`; `connect_command`
  returns `env`, `install`, `logged_in`, `oauth_retired`, `guide`, and falls back
  to `cli_legacy` when the primary login binary is missing.
- `bin/effi-connect` gains a `GUIDE` state for providers whose login is retired.
- The gemini-only "key present but no CLI ⇒ 🟡 api-only" special case is gone.
  One rule for everyone: **an API key alone is a routable credential (🟢)**, and
  `api-only` survives as a label, not a downgrade.
- `effi connect <p>` guides install-then-login (`npm i -g @google/gemini-cli`)
  when the login CLI is absent and the registry knows how to get it.
- +7 tests (162 total) covering the login-file semantics, the empty-file case,
  the legacy-CLI fallback, and the retirement path: stale creds never reaching
  🟢, the `미로그인` label being suppressed, a key restoring the connection, and
  `effi connect gemini` guiding rather than exec'ing.

### Fixed — table alignment
- `format_preflight` / `format_providers` padded status columns with `len()`,
  which drifts one cell per CJK char. Fine while every status was ASCII; the new
  `미로그인` label sheared the table. Both now pad by `_dwidth` (the helper the
  splash panel already used) via `_dpad`.

### Note
- `bin/effi-connect` keeps its heredoc apostrophe-free: bash 3.2 (macOS default)
  mis-parses `'` inside a heredoc nested in `$(...)` and fails the whole script.

## 4.7.1 — 2026-07-26

### Fixed — the launch screen was invisible
- 4.7.0 emitted the screen on the SessionStart hook's **plain stdout**, on the
  strength of the documented behaviour *"stdout is added as context that Claude
  can see and act on"*. That's true and beside the point: Claude Code turns
  hook stdout into a `hook_success` attachment flagged `isMeta`, which the
  transcript **hides**. The model saw the screen; the user never did.
- The hook now replies with JSON and splits the two audiences:
  **`systemMessage`** (rendered to the user as `<hook> says: …`) carries the
  panel plus the intro/connect guidance, and
  **`hookSpecificOutput.additionalContext`** (context-only) carries the compact
  preflight table plus the `[effi:action]` onboarding instructions — which are
  addressed to the assistant and shouldn't have been on the user's screen
  either. Net effect: the art now costs **zero** context tokens.
- `systemMessage` is prefixed inline with `<hook> says: `, so the payload
  starts with a newline; hook stdout is now pure JSON (output not starting with
  `{` is silently treated as plain text and both fields are dropped).
- `resume` / `compact` collapse to a one-line status (`splash_line`) instead of
  the preflight table — the model still gets the full table. Any *other*
  source, including ones a future Claude Code may add, renders the screen:
  a new source is likelier a fresh start than a re-entry, and this feature
  should fail toward visible.
- Core: `hook_session_start_output`, `splash_line`, `onboarding_action`,
  `QUIET_SOURCES`; `splash_data(pf=…)` reuses a preflight the caller already
  ran. +7 tests (155 total) pinning the channel split, the leading newline,
  pure-JSON stdout, and that `[effi:action]` never reaches the user.

## 4.7.0 — 2026-07-25

### Added — launch screen (`effi splash`)
- Every session now opens with a single screen: ANSI-block wordmark + a panel
  carrying **runtime & headline model** (mode-aware — Apex's pinned top model,
  Sip's ceiling, Cruise's routing primary), **live provider connection +
  credit estimate**, the **command surface grouped by job**, the three modes
  with the active one marked, project path, and a session id. Warnings
  (unconnected provider, stale catalog, unpinned project mode) render inline
  with the command that fixes them.
- It renders from the **SessionStart hook**, not the launcher. Claude Code
  clears the terminal when it starts, so a screen printed before `exec claude`
  flashes past unread. `effi` / `effi local` keep their one-line banners, and
  say so once when the hook isn't wired (`effi hooks install`).
  (The channel this shipped with was wrong — see 4.7.1.)
- `effi splash` prints it on demand (`--local --model M`, `--probe`, `--width`,
  `--no-color`, `--json`). `EFFI_SPLASH_WIDTH` / `EFFI_SPLASH_ART=0` tune the
  hook's rendering, and `NO_COLOR` / non-TTY stdout drop styling.
- Layout is width-aware: two columns ≥92 cols, stacked below, clamped to
  72–118 so it reads the same in a split pane and a maximised terminal.
  Alignment goes through a display-width helper (Hangul/emoji = 2 cells,
  variation selectors = 0) instead of `len()`, and long lines wrap with a
  hanging indent rather than truncating.
- Core: `splash_data`, `format_splash`, `mode_headline_model`,
  `new_session_id`, `hooks_installed`, `COMMAND_GROUPS`, `SPLASH_TIPS`,
  `_dwidth`/`_dtrim`/`_dpad`/`_wrap_cell`; `format_connect(table=…)` so the
  onboarding path doesn't print provider status twice. +40 tests (148 total),
  including a panel-geometry invariant at five widths, per-`source` hook
  behaviour, and a check that every command the panel advertises actually
  dispatches in `bin/effi`.

### Notes
- Design: `docs/02-design/launch-splash.md`.

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
