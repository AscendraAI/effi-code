# Launch screen (`effi splash`) — v4.7

## Why

Before v4.7 the launcher printed three loose lines (mode banner, account
rotation, `▶ CLOUD`), and everything a user actually needed to *decide* —
which providers are connected, what the active mode routes coding work to,
what commands exist — lived behind separate subcommands (`effi preflight`,
`effi providers`, `effi help`). Most sessions never ran them.

The splash puts one screen between `effi` and Claude Code that answers four
questions in the order a user asks them:

1. **What am I running?** — version, catalog stamp, runtime (cloud / local).
2. **On what?** — the active mode and the model that mode leads with.
3. **What's connected?** — live provider status + credit estimate.
4. **What do I type next?** — commands grouped by job, plus the exact command
   that fixes each warning.

## Where it renders — and why not in the launcher

The obvious place is `bin/effi`, right before `exec claude`. That doesn't work:
**Claude Code clears the terminal when it starts**, so a screen printed by the
launcher is drawn and wiped in the same instant. It has to come from *inside*
the session.

Claude Code's hook contract offers three channels:

| Channel | Seen by user | Seen by model | Verdict |
|---|---|---|---|
| SessionStart plain stdout | ✅ rendered in transcript | ✅ added to context | **used** |
| `systemMessage` | ✅ but documented only as "warning message" — no multi-line/box-art guarantee | ❌ | rejected: layout not contractual |
| `hookSpecificOutput.additionalContext` | ❌ | ✅ | not user-visible |

So the splash goes to the SessionStart hook's stdout. The model seeing it too
is a feature, not a leak — mode, connected providers, and warnings are exactly
the state it should start with.

`effi` / `effi local` keep their one-line banners and print a single hint when
the hook isn't wired (`hooks_installed()`), because otherwise the screen simply
never appears and there's nothing to explain why.

### Cost control

The panel costs ~500 context tokens a session, so it isn't emitted blindly:

| `source` | Output |
|---|---|
| `startup`, `clear`, `fork` | full launch screen |
| `resume`, `compact` | compact preflight table only |

`EFFI_SPLASH_ART=0` drops the wordmark (~150 tokens), `EFFI_NO_SPLASH=1` falls
back to the table everywhere.

## Layers it reuses

Nothing here probes or estimates on its own — it composes what already exists:

| Layer | Source | Used for |
|---|---|---|
| 0 — connect | `preflight()` | provider connection + detail |
| 1 — advisor | `estimate_headroom()` | credit / budget column |
| modes | `catalog/modes.json` | active mode, mode strip |
| routing | `catalog/task-routing.json` | Cruise's headline model |
| catalog | `catalog_status()` | freshness warning |

`splash_data()` gathers, `format_splash()` lays out. Both are pure enough to
unit test without a terminal — `probe=False` by default, so no network.

## Headline model

What a mode "leads with" for coding work is not one field in the catalog:

- **Apex** pins `policy.default_coding_model` → shown as-is.
- **Sip** pins `policy.coding_ceiling_model` → shown as `≤ model` (local runs
  below it; `cascade: local_first` is reported separately).
- **Cruise** pins nothing → falls back to the routing table's `implement`
  primary, which is what `effi route` would pick.

`effi local` passes the picked Ollama model explicitly, so the headline shows
the model that will actually answer.

## Layout rules

- Width clamped to **72–118** columns: the panel reads the same in a split
  pane and a maximised terminal.
- **≥92 cols** → two columns (mark + identity left, status + commands right).
  Below that the panel stacks; the mark is dropped, never squeezed.
- Alignment goes through `_dwidth()`, not `len()` — Hangul and emoji occupy
  two cells, variation selectors zero. A `len()`-based panel drifts by ~10
  columns on the Korean rows.
- Long lines **wrap with a hanging indent** (`_wrap_cell`) rather than
  truncating: a command list that gets cut is worse than one that takes two
  lines. Only the free-form provider `detail` is ellipsised.
- Color is applied **after** padding, so styling never changes geometry.
  `--no-color`, `NO_COLOR`, and non-TTY stdout all drop to plain text.

## Degradation

| Condition | Behaviour |
|---|---|
| `EFFI_NO_SPLASH=1` | compact preflight table instead |
| SessionStart hook not wired | no screen; launcher says `effi hooks install` |
| `NO_COLOR` / non-TTY (`effi splash`) | rendered, uncolored |
| hook payload missing or malformed | treated as `startup` |
| provider registry missing | bundled `catalog/providers.example.json` |
| corrupt ledger | `usage_summary()` skips bad lines; panel still renders |
| anything raises | one honest line, never a failed session start |

The hook always renders `color=False`: Claude Code draws the hook's text in its
transcript, so escape codes would surface as literal noise. Width defaults to
**72** to clear the transcript gutter (`EFFI_SPLASH_WIDTH` overrides), which
puts it in the stacked layout.

## Invariants under test (`tests/test_splash.py`)

- every panel row is exactly the requested width, at 72 / 80 / 92 / 100 / 118
- stripping ANSI from the colored render equals the plain render
- the wordmark is rectangular and fits 72 columns
- wrapping never exceeds the cell and never drops a command
- every command the panel advertises dispatches in `bin/effi`
  (`COMMAND_GROUPS` is the single source for the panel *and* the count)
- `splash_data()` makes no API call unless `probe=True`
- the hook emits the screen for `startup`/`clear`/`fork`, the table for
  `resume`/`compact`, and never crashes on a malformed payload
