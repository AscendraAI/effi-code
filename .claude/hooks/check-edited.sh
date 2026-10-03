#!/usr/bin/env bash
# PostToolUse(Edit|Write): 방금 고친 파일 하나만 문법 검사한다.
#   (picknow-ops .claude/hooks/lint-edited.sh에서 가져왔다 — eslint 대신 py_compile · bash -n)
#
# 턴 끝(Stop)이나 푸시보다 한 겹 앞이다. 고친 직후에 말해야 방금 무엇을 했는지
# 아는 상태에서 고친다.
#
# ⛔ 오류로만 막는다. 느려지면 이 훅은 죽는다 — 파일 하나만, 1초 안에.
#
# 부수는 법: lib/effi_core.py 아무 줄에 `def (:`를 넣고 Edit로 저장한다 → exit 2.

input="$(cat)"
file="$(printf '%s' "$input" | python3 -c 'import sys,json
try: print((json.load(sys.stdin).get("tool_input") or {}).get("file_path") or "")
except Exception: print("")' 2>/dev/null)"
[ -n "$file" ] && [ -f "$file" ] || exit 0

case "$file" in
  *.py)
    err="$(PYTHONPYCACHEPREFIX="${TMPDIR:-/tmp}/effi-hook-pycache" python3 -m py_compile "$file" 2>&1)" || {
      echo "문법 오류 — $file" >&2; echo "$err" | tail -5 >&2; exit 2; }
    ;;
  *)
    if head -1 "$file" 2>/dev/null | grep -qE '^#!.*[/ ](ba)?sh([[:space:]]|$)'; then
      err="$(bash -n "$file" 2>&1)" || { echo "문법 오류 — $file" >&2; echo "$err" >&2; exit 2; }
    fi
    ;;
esac
exit 0
