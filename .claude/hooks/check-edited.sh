#!/usr/bin/env bash
# After an edit: syntax-check the file(s) just edited.   (effi harness)
# One script for every agent that runs hooks:
#   Claude Code  PostToolUse Edit|Write   → tool_input.file_path
#   Codex        PostToolUse apply_patch  → paths inside tool_input.command
#                (measured 2026-10-04: Codex never reports Edit|Write)
# Errors block (exit 2); nothing else does — a noisy hook gets switched off.

input="$(cat)"
files="$(printf '%s' "$input" | python3 -c '
import sys, json, re
try:
    d = json.load(sys.stdin)
except Exception:
    sys.exit(0)
ti = d.get("tool_input") or {}
if ti.get("file_path"):
    print(ti["file_path"])
elif d.get("tool_name") == "apply_patch":
    for m in re.finditer(r"^\*\*\* (?:Update|Add) File: (.+)$|^\*\*\* Move to: (.+)$",
                         str(ti.get("command") or ""), re.M):
        print((m.group(1) or m.group(2)).strip())
' 2>/dev/null)"
[ -n "$files" ] || exit 0

rc=0
while IFS= read -r file; do
  [ -f "$file" ] || continue
  case "$file" in
    *.py)
      err="$(PYTHONPYCACHEPREFIX="${TMPDIR:-/tmp}/effi-hook-pycache" python3 -m py_compile "$file" 2>&1)" || {
        echo "syntax error — $file" >&2; echo "$err" | tail -5 >&2; rc=2; }
      ;;
    *.json)
      err="$(python3 -m json.tool "$file" 2>&1 >/dev/null)" || {
        echo "invalid JSON — $file" >&2; echo "$err" >&2; rc=2; }
      ;;
    *)
      if head -1 "$file" 2>/dev/null | grep -qE '^#!.*[/ ](ba)?sh([[:space:]]|$)'; then
        err="$(bash -n "$file" 2>&1)" || { echo "syntax error — $file" >&2; echo "$err" >&2; rc=2; }
      fi
      ;;
  esac
done <<< "$files"
exit $rc
