#!/usr/bin/env bash
#
# verify.sh — effi-code의 완료 판정. 「됐다」는 이 스크립트가 말한다.
#   (picknow-ops scripts/verify.sh에서 가져와 Python CLI에 맞게 줄였다)
#
#   bash scripts/verify.sh          전체 (문법 · 단위 · 정합 · CLI 스모크)
#   bash scripts/verify.sh --fast   스모크를 뺀다 (pre-push · Stop 훅)
#
# exit: 0 통과 · 1 새 발견(❌) · 2 점검 불가(⛔ — 통과가 아니다)
#
# ★ 테스트는 **깨끗한 사본**에서 돈다. 이 저장소의 테스트는 cwd의 git 루트와
#   ~/.config/effi를 읽는다 — 작업 트리의 `.effi/mode`(apex 고정)가 새어 들어가
#   로컬에서는 CI와 다른 실패 4건이 났다(2026-10-03 실측). 그래서 추적 파일 +
#   추적 안 된 새 파일(무시 목록 · .effi/ 제외)을 임시 폴더로 옮기고, HOME도
#   비운다. **CI와 같은 조건이어야 로컬 초록이 의미가 있다.**
#
# 부수는 법: tests/test_route.py의 기대값 하나를 틀리게 바꾼다 → exit 1.
#           PATH에서 python3를 빼고 돌린다 → exit 2.

set -u
FAST=0
[ "${1:-}" = "--fast" ] && FAST=1

ROOT="$(cd "$(dirname "$0")/.." && pwd)"
cd "$ROOT" || exit 2

FOUND=0
fail() { echo "❌ $*"; FOUND=1; }
cannot() { echo "⛔ $*"; exit 2; }
ok() { echo "✅ $*"; }

command -v python3 >/dev/null 2>&1 || cannot "python3가 없다 — 판정 불가"
command -v git >/dev/null 2>&1 || cannot "git이 없다 — 판정 불가"

WORK="$(mktemp -d "${TMPDIR:-/tmp}/effi-verify.XXXXXX")" || cannot "임시 폴더를 못 만들었다"
trap 'rm -rf "$WORK"' EXIT
mkdir -p "$WORK/home" "$WORK/src"
export PYTHONPYCACHEPREFIX="$WORK/pycache"

# ── 1. 문법 ───────────────────────────────────────────────
n=0; bad=""
for f in bin/* setup.sh scripts/*.sh .githooks/* .claude/hooks/*.sh; do
  [ -f "$f" ] || continue
  head -1 "$f" | grep -qE '^#!.*[/ ](ba)?sh([[:space:]]|$)' || continue
  n=$((n + 1))
  bash -n "$f" 2>"$WORK/err" || bad="$bad\n   $f: $(head -1 "$WORK/err")"
done
for f in lib/*.py tests/*.py; do
  [ -f "$f" ] || continue
  n=$((n + 1))
  python3 -m py_compile "$f" 2>"$WORK/err" || bad="$bad\n   $f: $(tail -1 "$WORK/err")"
done
[ "$n" -gt 0 ] || cannot "문법 검사: 센 파일이 0개 — 경로가 틀렸다"
if [ -n "$bad" ]; then fail "문법 오류$(printf '%b' "$bad")"; else ok "문법 ${n}파일"; fi

# ── 2. 깨끗한 사본 ────────────────────────────────────────
git ls-files -z -co --exclude-standard \
  | tr '\0' '\n' | grep -v '^\.effi/' | while IFS= read -r f; do [ -e "$f" ] && printf '%s\0' "$f"; done \
  | tar --null -T - -cf - 2>/dev/null | tar -xf - -C "$WORK/src"
# 파이프 끝(tar -x)만 보면 앞쪽 실패가 빈 사본으로 통과한다 — 단계마다 본다.
for st in "${PIPESTATUS[@]}"; do [ "$st" -eq 0 ] || cannot "사본을 못 만들었다 (파이프 상태: ${PIPESTATUS[*]})"; done
[ -f "$WORK/src/lib/effi_core.py" ] || cannot "사본에 lib/effi_core.py가 없다"
git -C "$WORK/src" init -q 2>/dev/null || cannot "사본에 git init 실패"

hermetic() { (cd "$WORK/src" && env -u EFFI_MODE -u EFFI_PROJECT -u EFFI_ACCOUNT_ID \
  HOME="$WORK/home" PYTHONPATH="$WORK/src/lib" PATH="$WORK/src/bin:$PATH" "$@"); }

# ── 3. 단위 테스트 ────────────────────────────────────────
hermetic python3 -m unittest discover -s tests >"$WORK/unit" 2>&1
urc=$?
ran="$(grep -Eo '^Ran [0-9]+' "$WORK/unit" | grep -Eo '[0-9]+' || echo 0)"
if [ "$ran" = "0" ]; then
  tail -15 "$WORK/unit"
  cannot "단위 테스트: 0건을 돌렸다 — 「없다」가 아니라 「못 쟀다」"
elif [ $urc -ne 0 ]; then
  fail "단위 테스트 ${ran}건 중 실패:"
  grep -E '^(FAIL|ERROR):|Error:' "$WORK/unit" | sed 's/^/   /' | head -20
else
  ok "단위 테스트 ${ran}건"
fi

# ── 4. 정합 ───────────────────────────────────────────────
ver="$(tr -d '[:space:]' < VERSION 2>/dev/null)"
top="$(grep -m1 -Eo '^## [0-9]+\.[0-9]+\.[0-9]+' CHANGELOG.md 2>/dev/null | cut -c4-)"
if [ -z "$ver" ] || [ -z "$top" ]; then
  fail "VERSION 또는 CHANGELOG 머리를 못 읽었다"
elif [ "$ver" != "$top" ]; then
  fail "VERSION($ver) ≠ CHANGELOG 맨 위($top) — 둘 다 같이 올린다"
else
  ok "VERSION = CHANGELOG ($ver)"
fi

# CLAUDE.md는 매 세션 읽힌다. 길어지면 아무도 안 읽는다(picknow: 5,009줄 → 150줄).
lines="$(wc -l < CLAUDE.md | tr -d ' ')"
if [ "$lines" -gt 150 ]; then
  fail "CLAUDE.md ${lines}줄 > 150 — 교훈은 검사로 만들거나 docs/로 (/lesson)"
else
  ok "CLAUDE.md ${lines}줄 ≤ 150"
fi

# ── 5. CLI 스모크 (CI와 같은 것) ──────────────────────────
if [ $FAST -eq 0 ]; then
  smoke() {
    local name="$1"; shift
    if hermetic bash -c "$*" >"$WORK/smoke" 2>&1; then
      ok "스모크: $name"
    else
      fail "스모크: $name"; tail -5 "$WORK/smoke" | sed 's/^/   /'
    fi
  }
  smoke "help" 'effi help >/dev/null'
  smoke "route" 'effi route --compact "add rate limit middleware and unit tests" >/dev/null'
  smoke "route (bulk)" 'effi route --compact "40 UI strings translate" >/dev/null'
  smoke "catalog" 'effi catalog status >/dev/null'
  smoke "splash" 'effi splash --width 100 --no-color | grep -q "effi-code v"'
  smoke "splash --json" 'effi splash --json | python3 -c "import sys,json; assert json.load(sys.stdin)[\"providers\"]"'
  smoke "session-start hook" "echo '{\"source\":\"startup\"}' | bin/effi-hook-session-start | python3 -c \"import sys,json; d=json.load(sys.stdin); assert 'effi-code v' in d['systemMessage']; assert 'effi preflight' in d['hookSpecificOutput']['additionalContext']\""
  smoke "edit --help" 'effi-edit --help >/dev/null'
  smoke "doctor --json" 'effi doctor --json | python3 -c "import sys,json; assert \"version\" in json.load(sys.stdin)"'
fi

echo ""
if [ $FOUND -ne 0 ]; then echo "❌ verify: 새 발견이 있다"; exit 1; fi
echo "✅ verify 통과$([ $FAST -eq 1 ] && echo ' (--fast)')"
exit 0
