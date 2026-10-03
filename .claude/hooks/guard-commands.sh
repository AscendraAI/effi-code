#!/usr/bin/env bash
# Before a shell command: refuse the git moves that sweep up or rewrite other
# people's work.   (effi harness)
# Claude Code enforces the same list through permissions.deny in
# .claude/settings.json; agents without a deny list (Codex) get it here —
# measured 2026-10-04: a Codex PreToolUse hook exiting 2 blocks the command.

input="$(cat)"
cmd="$(printf '%s' "$input" | python3 -c 'import sys,json
try: print((json.load(sys.stdin).get("tool_input") or {}).get("command") or "")
except Exception: print("")' 2>/dev/null)"
[ -n "$cmd" ] || exit 0

if printf '%s' "$cmd" | grep -qE '(^|[;&|[:space:]])git[[:space:]]+(add[[:space:]]+(-A|--all)([[:space:]]|$)|commit[[:space:]]+(-a|-am|--all)([[:space:]]|$)|push([[:space:]].*)?[[:space:]](--force[^[:space:]]*|-f)([[:space:]]|$))'; then
  echo "blocked by project policy (effi harness): stage explicit paths (git add <paths>), never git add -A / commit -a, and never force-push." >&2
  exit 2
fi
exit 0
