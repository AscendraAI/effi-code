# Design — `effi delegate`: the bridge from Claude to other providers

> Status: **SHIPPED v1** (2026-10-03) — reviewed three times by Codex (design + two code passes, the last through `effi delegate` itself); see §7 · plan: docs/01-plan/v5-direction.md §3 · §7 (H6) · §6.3 (H4)

## 1. What it is (and is not)

Claude Code's native orchestration only drives Claude models. `effi delegate`
lets the Claude main thread hand one task to Codex, Gemini, Grok or a local
model **through their own official CLIs**, and get back **paths + a short
summary**. It owns only five things — the parts nobody else does (H6):

| | effi does | effi does not |
|---|---|---|
| ① decide | which provider, from task kind + mode + connection status | invent model ids (CLI default unless `--model`) |
| ② isolate | read jobs: provider's read-only mode. write jobs: a fresh git worktree outside the repo | let a delegate write the user's tree |
| ③ run | `codex exec` · `gemini -p` · `grok -p` · `effi run` (local) · `claude -p` (only if asked) | proxy credentials (each CLI uses its own login/key) |
| ④ gate | write jobs: `scripts/verify.sh --fast` in the worktree; `apply` refuses unless it passed | merge or commit anything |
| ⑤ record | `~/.config/effi/delegate/<id>/` (prompt, output, meta) + one ledger line | keep full transcripts in context |

## 2. Decide

`recommend(task)` already classifies the domain. Delegation adds three rules
that routing alone got wrong (measured 2026-10-03: in Apex every domain —
review, research, bulk, design — routed to Claude, so the session never used
another provider):

1. **review → a different provider than the main thread** (the CLAUDE.md rule
   "review → fresh context, preferably different model"). Order: codex, grok, gemini.
2. **research that is realtime** (latest, news, today, 2026, current, release…)
   → grok first (live search), then gemini.
3. otherwise the recommended primary; if that is `claude` the answer is
   **"stay on the main thread"** (no delegate) unless `--to` forces one.

Availability: skip providers that `connect_report()` says are not usable
(e.g. gemini without `GEMINI_API_KEY`); the reason is recorded. Apex never
picks `local`; Sip prefers `local` for bulk.

## 3. Isolate · run

| provider | read (default) | write (`--write`) |
|---|---|---|
| codex | `codex exec -s read-only -C <dir> -o <out>` | `-s workspace-write` in the worktree |
| gemini | `gemini -p … --approval-mode plan` (cwd) | `--approval-mode auto_edit` in the worktree |
| grok | `grok -p … --cwd <dir> --permission-mode plan` | `--permission-mode acceptEdits` in the worktree |
| local | `effi run` (text only) | not supported |
| claude | `claude -p --permission-mode plan` | `--permission-mode acceptEdits` in the worktree |

Write worktrees live at `~/.config/effi/worktrees/<repo>-<id>` on branch
`effi/delegate-<id>` from HEAD — outside the repo, so the main tree's
`git status` stays clean. stdin is `/dev/null` (codex otherwise waits).
Default timeout 900 s; on timeout the job is recorded as `timeout`, never as done.

## 4. Gate · apply

After a write job: diff stat + `bash scripts/verify.sh --fast` in the worktree
(if present) → `verify: 0|1|2|absent`. `effi delegate apply <id>` applies the
worktree's diff to the main tree **without committing** (the main thread stays
the single writer and reviews it) and refuses when verify ≠ 0 (or absent)
unless `--force`, or when `git apply --check` fails.

## 5. Record

`~/.config/effi/delegate/<id>/{prompt.md, output.md, meta.json}`; ledger
`~/.config/effi/delegate/ledger.ndjson` (id, at, repo, task, provider, decision
reason, mode, kind, exit, duration, verify, worktree). The CLI prints ≤ 15
lines: decision, status, summary tail, paths.

## 6. Out of scope (v1)

Per-agent policy rendering (H4 finding 2: Codex gets no effi policy) — the
verify gate is the agent-agnostic guard for now. Antigravity (`agy`: 38 s
start, needs a file redirect). Parallel fan-out (Orca / Workflows do that).

## 7. Reviews and what changed (2026-10-03)

The design was reviewed by **Codex** (read-only), not by another Claude, and
the code twice more — the second time *through `effi delegate --review`*.

| Finding | Resolution |
|---|---|
| Worktree is not a security boundary | Every job runs in an OS sandbox: Codex's own seatbelt, or effi's `sandbox-exec` fence (writes: job temp, CLI state, worktree; reads of other providers' credentials and ~/.ssh etc. denied). No sandbox → refused, read jobs too |
| Delegate controls its verifier | Gate runs the main tree's `verify.sh` on a **fresh checkout of base + the frozen patch**, written by effi with symlink refusal + `O_NOFOLLOW`, fenced, no network |
| Output re-enters Claude as instructions | Printed under an "untrusted data" banner; ANSI/control/bidi stripped; never suggests `--force` |
| Inherited secrets | Allowlisted env per provider |
| Stale verify / dirty main tree | `apply` refuses when HEAD moved or tracked files are dirty (untracked → warning) |
| Incomplete diff | `git add -A` + `diff --cached --binary` as bytes (untracked, binaries, its own commits); caches excluded |
| Timeouts leave children | Process group killed after every run. **Known limit:** a child that calls `setsid()` escapes |
| `openai` vs `codex` ids | Normalized |
| Delegate rewrites `.git` / gitattributes filters | git runs with the gitdir recorded at creation, hooks/fsmonitor off, and `git add` inside a no-network fence |
| Case variants (`.CLAUDE/`) on macOS | Guard paths matched case-insensitively |
| Delegate plants config in its own CLI dir | Config/hooks/skills files inside the state dir are write-denied; sessions/logs allowed |

Measured in real runs: `codex exec` cannot run inside effi's fence (nested
`sandbox_apply` is refused) → native sandbox. `grok -p` without `--max-turns`
is a single model turn (no edits). Denying all of `~/.claude` silently stopped
grok's tool loop (it reads Claude-compatible skills) → only Claude's private
parts are denied.
