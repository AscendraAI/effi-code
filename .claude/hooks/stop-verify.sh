#!/usr/bin/env bash
# Stop: 무인 실행(VERIFY_ON_STOP=1)일 때만, 턴이 끝나기 전에 verify --fast를 돌린다.
#   (picknow-ops .claude/hooks/stop-verify.sh에서 가져왔다)
# 빨간불이면 exit 2로 턴을 안 끝내고 결과를 에이전트에게 돌려준다.
#
# 대화형 세션에서는 아무것도 하지 않는다 — 매 턴 기다리게 하면 사람이 훅을 끄는 법을 배운다.
# 끝없이 도는 것을 막는다: 연속 5번 막으면 통과시키고 멈추라고 말한다.
#
# 부수는 법: VERIFY_ON_STOP=1로 두고 tests/ 기대값 하나를 틀리게 바꾼다 → exit 2.

[ "${VERIFY_ON_STOP:-0}" = "1" ] || exit 0

input="$(cat)"
cwd="$(printf '%s' "$input" | python3 -c 'import sys,json
try: print(json.load(sys.stdin).get("cwd") or "")
except Exception: print("")' 2>/dev/null)"
root="$(git -C "${cwd:-$PWD}" rev-parse --show-toplevel 2>/dev/null)" || exit 0
cd "$root" || exit 0
[ -f scripts/verify.sh ] || exit 0

count_file="$(git rev-parse --git-dir)/stop-verify.count"
n="$(cat "$count_file" 2>/dev/null || echo 0)"

out="$(bash scripts/verify.sh --fast 2>&1)"
rc=$?

if [ $rc -eq 0 ]; then
  echo 0 > "$count_file"
  exit 0
fi

n=$((n + 1))
echo "$n" > "$count_file"

if [ "$n" -ge 5 ]; then
  echo 0 > "$count_file"
  echo "verify가 연속 5번 빨간불이다. 더 막지 않는다. 지금 상태와 막힌 이유를 적고 멈춰라." >&2
  exit 0
fi

{
  echo "verify --fast가 통과하지 못했다 (exit $rc, 연속 ${n}번째). 고친 뒤에 끝내라."
  echo "$out" | tail -25
} >&2
exit 2
