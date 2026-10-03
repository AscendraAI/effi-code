"""effi harness — the verification floor, generated for any repo.

Scan a project, propose a floor (one `scripts/verify.sh` that answers
0 pass · 1 found · 2 cannot judge, a pre-push gate, edit-time syntax hook,
deny rules), write it only after the user approves, and *prove* it works by
breaking a copy of the project on purpose.

Why a floor and not recommendations: Anthropic's claude-code-setup already
recommends project-specific skills well. Measured against a real project's
hand-built harness, what it missed was the floor that lets a machine judge
the result — the part with the strongest evidence. That is what this module
builds. Domain hooks stay with claude-code-setup.

Rules carried over from picknow-ops (each cost an incident there):
  - "cannot judge" is not a pass: a step that counted 0 things exits 2.
  - a check that was never seen going red is not a check → `prove`.
  - never overwrite the user's file → sidecar `<file>.effi-new`.
  - gate at push, not commit: commit hooks teach `--no-verify`.
"""
from __future__ import annotations

import json
import os
import re
import shutil
import stat
import subprocess
import tempfile
from pathlib import Path
from typing import Optional

CONTEXT_MAX_LINES = 150
SHELL_DIRS = ("bin", "scripts", ".githooks", ".claude/hooks")
NODE_FAST_SCRIPTS = ("lint", "typecheck", "test")
NPM_PLACEHOLDER_TEST = "no test specified"


# ── scan ──────────────────────────────────────────────────────────────

def _git(root: Path, *args: str) -> subprocess.CompletedProcess:
    return subprocess.run(["git", "-C", str(root), *args],
                          capture_output=True, text=True)


def _tracked(root: Path) -> list[str]:
    # -z: git quotes non-ASCII paths otherwise ("\355\225\234.py"), and a
    # quoted path silently fails every existence check downstream
    r = _git(root, "-c", "core.quotePath=false", "ls-files", "-z", "-co", "--exclude-standard")
    return [l for l in r.stdout.split("\0") if l] if r.returncode == 0 else []


def _toplevel(root: Path) -> Path:
    """Always the repo top: core.hooksPath and the generated scripts resolve
    from there, so a floor written into a subdirectory would never fire."""
    r = _git(Path(root).resolve(), "rev-parse", "--show-toplevel")
    if r.returncode != 0:
        raise ValueError(f"{root} is not a git repository")
    return Path(r.stdout.strip()).resolve()


# same rule in Python and in the generated bash: a real argv check, not any
# mention of the string (a no-op exit 0 must not count as a passing selftest)
SELFTEST_RE = r"argv.*--selftest|--selftest.*argv|add_argument\(.*--selftest|==\s*.--selftest|.--selftest.\s*(==|in\s)"
SHEBANG_RE = rb"^#!.*[/ ](ba)?sh(\s|$)"


def _is_shell(path: Path) -> bool:
    try:
        with open(path, "rb") as fh:
            first = fh.readline(120)
    except OSError:
        return False
    return bool(re.match(SHEBANG_RE, first))


def _read_json(path: Path) -> dict:
    try:
        return json.loads(path.read_text())
    except (OSError, ValueError):
        return {}


def _pytest_configured(root: Path) -> bool:
    if (root / "pytest.ini").exists() or (root / "conftest.py").exists():
        return True
    for name in ("pyproject.toml", "setup.cfg", "tox.ini"):
        p = root / name
        if p.exists() and re.search(r"\[(tool:)?pytest|\[tool\.pytest", p.read_text(errors="ignore")):
            return True
    return False


def _node_packages(root: Path, files: list[str]) -> list[dict]:
    """package.json at the root or one level down (web/, app/ …)."""
    out = []
    for f in files:
        parts = f.split("/")
        if parts[-1] != "package.json" or len(parts) > 2 or "node_modules" in parts:
            continue
        d = "." if len(parts) == 1 else parts[0]
        pkg = _read_json(root / f)
        scripts = pkg.get("scripts") or {}
        deps = {**(pkg.get("dependencies") or {}), **(pkg.get("devDependencies") or {})}
        run = [s for s in NODE_FAST_SCRIPTS
               if s in scripts and not (s == "test" and NPM_PLACEHOLDER_TEST in scripts[s])]
        tsc = ("typecheck" not in run and "typescript" in deps
               and (root / d / "tsconfig.json").exists())
        out.append({"dir": d, "scripts": run, "tsc": tsc,
                    "installed": (root / d / "node_modules").is_dir()})
    return out


def _unpinned_mcp(root: Path) -> list[str]:
    """`.mcp.json` servers launched as `pkg@latest` or with no version at all —
    every start runs whatever was published last (postmark-mcp, 2025-09)."""
    found = []
    for name, spec in (_read_json(root / ".mcp.json").get("mcpServers") or {}).items():
        cmd = spec.get("command") or ""
        if Path(cmd).name not in ("npx", "bunx", "uvx", "pnpx"):
            continue
        pkgs = [a for a in spec.get("args") or [] if not a.startswith("-")]
        if not pkgs:
            continue
        pkg = pkgs[0]
        bare = pkg.lstrip("@")
        if pkg.endswith("@latest") or "@" not in bare:
            found.append(f"{name}: {cmd} {pkg}")
    return found


def scan(root: Path) -> dict:
    """What the project has and what the floor would be. Writes nothing."""
    root = _toplevel(root)
    files = _tracked(root)
    fset = set(files)

    py = [f for f in files if f.endswith(".py")]
    sh = [f for f in files if f.endswith(".sh")
          or (f.split("/")[0] in SHELL_DIRS or f.startswith(".claude/hooks/"))
          and "." not in Path(f).name and _is_shell(root / f)]
    test_files = [f for f in py if re.search(r"(^|/)tests?/test_[^/]+\.py$", f)]
    test_dirs = sorted({f.rsplit("/", 1)[0] for f in test_files})
    selftest = []
    pytest_style = False
    for f in py:
        try:
            text = (root / f).read_text(errors="ignore")
        except OSError:
            continue
        if f in test_files:
            # bare `def test_x()` with no TestCase: unittest would run 0 of them
            if re.search(r"^def test_", text, re.M) and "TestCase" not in text:
                pytest_style = True
            continue
        if re.search(r"(^|/)tests?/", f):
            continue
        if re.search(SELFTEST_RE, text):
            selftest.append(f)
    pytest = _pytest_configured(root) or pytest_style
    node = _node_packages(root, files)
    context = []
    for f in files:
        if Path(f).name in ("CLAUDE.md", "AGENTS.md"):
            try:
                with open(root / f, encoding="utf-8", errors="ignore") as fh:
                    n = sum(1 for _ in fh)
            except OSError:
                continue
            context.append({"file": f, "lines": n})

    steps = []
    if sh:
        steps.append({"id": "shell_syntax", "fast": True, "count": len(sh)})
    if py:
        steps.append({"id": "python_syntax", "fast": True, "count": len(py)})
    if pytest:
        steps.append({"id": "pytest", "fast": True})
    elif test_files:
        for d in test_dirs:
            n = sum(1 for f in test_files if f.rsplit("/", 1)[0] == d)
            steps.append({"id": "unittest", "fast": True, "dir": d, "count": n})
    if selftest:
        steps.append({"id": "selftest", "fast": True, "count": len(selftest)})
    for p in node:
        root_pkg = p["dir"] == "."
        for s in p["scripts"]:
            steps.append({"id": "npm", "dir": p["dir"], "script": s, "fast": root_pkg})
        if p["tsc"]:
            steps.append({"id": "tsc", "dir": p["dir"], "fast": False})
    if context:
        steps.append({"id": "context_cap", "fast": True, "max": CONTEXT_MAX_LINES})

    hooks_path = _git(root, "config", "core.hooksPath").stdout.strip()
    existing_verify = [p for p in ("scripts/verify.sh", "scripts/verify", "bin/verify")
                       if p in fset]
    ci = [f for f in files if f.startswith(".github/workflows/")]
    claude_ignored = _git(root, "check-ignore", "-q", ".claude/settings.json").returncode == 0

    findings = []
    if not existing_verify:
        findings.append(("floor", "no single verify script — nothing answers 'is it done?'"))
    if not ci:
        findings.append(("floor", "no CI workflow — the pre-push gate is the only automatic check"))
    if hooks_path != ".githooks":
        findings.append(("floor", "pre-push gate not armed (core.hooksPath)"))
    if not [s for s in steps if s["id"] in ("pytest", "unittest", "selftest")
            or (s["id"] == "npm" and s["script"] == "test")]:
        findings.append(("floor", "no runnable tests found — verify can only check syntax"))
    if not [s for s in steps if s["id"] != "context_cap"]:
        findings.append(("floor", "no supported checks for this stack — verify.sh would report ⛔ "
                                  "until you add a step (python · shell · node are detected today)"))
    for c in context:
        if c["lines"] > CONTEXT_MAX_LINES:
            findings.append(("context", f"{c['file']} is {c['lines']} lines (> {CONTEXT_MAX_LINES}) — "
                                        "long context files cost tokens and get skimmed"))
    for p in node:
        if not p["installed"] and (p["scripts"] or p["tsc"]):
            findings.append(("env", f"{p['dir']}/node_modules missing — node steps will report ⛔"))
    for u in _unpinned_mcp(root):
        findings.append(("trust", f"unpinned MCP server — {u} (pin an exact version)"))
    if claude_ignored:
        findings.append(("share", ".claude/ is gitignored — project hooks/settings won't reach collaborators"))

    codex = bool(shutil.which("codex")) or (root / ".codex").exists()
    if codex and not (root / ".codex/hooks.json").exists():
        findings.append(("agents", "Codex is used here but has no project hooks — effi's policy "
                                   "reaches Claude only (H4: a Codex worker got none of it)"))
    if (root / "CLAUDE.md").exists() and (root / "AGENTS.md").exists() and \
            "AGENTS.md" not in (root / "CLAUDE.md").read_text(errors="ignore"):
        findings.append(("agents", "CLAUDE.md doesn't import AGENTS.md — add a line `@AGENTS.md` so "
                                   "Claude reads the same rules as the other agents"))
    return {
        "root": str(root),
        "codex": codex,
        "counts": {"python": len(py), "shell": len(sh), "tests": len(test_files),
                   "selftest": len(selftest), "node_packages": len(node), "ci": len(ci)},
        "steps": steps,
        "existing": {"verify": existing_verify, "hooks_path": hooks_path or None,
                     "claude_settings": ".claude/settings.json" in fset or
                                        (root / ".claude/settings.json").exists()},
        "findings": findings,
    }


# ── generate ──────────────────────────────────────────────────────────

VERIFY_HEAD = r'''#!/usr/bin/env bash
#
# verify.sh — the one answer to "is it done?"   (generated by `effi harness`)
# Edit freely: effi never overwrites this file (it writes verify.sh.effi-new).
#
#   bash scripts/verify.sh          everything
#   bash scripts/verify.sh --fast   pre-push subset
#
# exit: 0 pass · 1 found (❌) · 2 cannot judge (⛔ — NOT a pass)
# A step that counted zero things exits 2: "none found" and "couldn't look"
# must never read the same.

set -u
FAST=0
[ "${1:-}" = "--fast" ] && FAST=1
ROOT="$(cd "$(dirname "$0")/.." && pwd)"
cd "$ROOT" || exit 2
PY="${PYTHON:-python3}"
FOUND=0
fail() { echo "❌ $*"; FOUND=1; }
cannot() { echo "⛔ $*"; exit 2; }
ok() { echo "✅ $*"; }
TMP="$(mktemp -d "${TMPDIR:-/tmp}/verify.XXXXXX")" || cannot "could not create a temp dir"
trap 'rm -rf "$TMP"' EXIT
export PYTHONPYCACHEPREFIX="$TMP/pycache"
command -v git >/dev/null 2>&1 || cannot "git not found"
'''

VERIFY_TAIL = r'''
echo ""
if [ $FOUND -ne 0 ]; then echo "❌ verify: found problems"; exit 1; fi
echo "✅ verify passed$([ $FAST -eq 1 ] && echo ' (--fast)')"
exit 0
'''

NEED_PY = r'''
command -v "$PY" >/dev/null 2>&1 || cannot "python not found ($PY)"
'''

STEP = {
    "shell_syntax": r'''
# ── shell syntax ──
n=0; bad=""
while IFS= read -r -d '' f; do
  [ -f "$f" ] || continue
  case "$f" in *.sh) ;; *) head -1 "$f" | grep -qE '^#!.*[/ ](ba)?sh([[:space:]]|$)' || continue ;; esac
  n=$((n + 1))
  bash -n "$f" 2>"$TMP/e" || bad="$bad
   $f: $(head -1 "$TMP/e")"
done < <(git -c core.quotePath=false ls-files -z -co --exclude-standard -- '*.sh' {shell_dirs})
[ "$n" -gt 0 ] || cannot "shell syntax: counted 0 files"
if [ -n "$bad" ]; then fail "shell syntax:$bad"; else ok "shell syntax: $n files"; fi
''',
    "python_syntax": r'''
# ── python syntax ──
n=0; bad=""
while IFS= read -r -d '' f; do
  [ -f "$f" ] || continue
  n=$((n + 1))
  "$PY" -m py_compile "$f" 2>"$TMP/e" || bad="$bad
   $f: $(tail -1 "$TMP/e")"
done < <(git -c core.quotePath=false ls-files -z -co --exclude-standard -- '*.py')
[ "$n" -gt 0 ] || cannot "python syntax: counted 0 files"
if [ -n "$bad" ]; then fail "python syntax:$bad"; else ok "python syntax: $n files"; fi
''',
    "unittest": r'''
# ── unittest ──
"$PY" -m unittest discover -s {dir} >"$TMP/u" 2>&1
rc=$?
ran="$(grep -Eo '^Ran [0-9]+' "$TMP/u" | grep -Eo '[0-9]+' || echo 0)"
if [ "$ran" = "0" ]; then tail -15 "$TMP/u"; cannot "unittest: ran 0 tests"; fi
if [ $rc -eq 0 ]; then ok "unittest: $ran tests"
else fail "unittest: failures among $ran tests"; grep -E '^(FAIL|ERROR):' "$TMP/u" | head -20 | sed 's/^/   /'; fi
''',
    "pytest": r'''
# ── pytest ──
"$PY" -m pytest --version >/dev/null 2>&1 || cannot "pytest is configured but not installed"
"$PY" -m pytest -q >"$TMP/p" 2>&1
rc=$?
case $rc in
  0) ok "pytest: $(tail -1 "$TMP/p")" ;;
  1) fail "pytest:"; tail -20 "$TMP/p" | sed 's/^/   /' ;;
  5) cannot "pytest: collected 0 tests" ;;
  *) tail -15 "$TMP/p"; cannot "pytest: exit $rc (interrupted or usage error)" ;;
esac
''',
    "selftest": r'''
# ── module selftests (files that accept --selftest) ──
n=0; bad=""
while IFS= read -r -d '' f; do
  case "$f" in tests/*|test/*|*/tests/*|*/test/*) continue ;; esac
  grep -qE 'argv.*--selftest|--selftest.*argv|add_argument\(.*--selftest|==[[:space:]]*.--selftest|.--selftest.[[:space:]]*(==|in[[:space:]])' "$f" 2>/dev/null || continue
  n=$((n + 1))
  "$PY" "$f" --selftest >"$TMP/s" 2>&1 || bad="$bad
   $f: $(tail -1 "$TMP/s")"
done < <(git -c core.quotePath=false ls-files -z -co --exclude-standard -- '*.py')
[ "$n" -gt 0 ] || cannot "selftest: counted 0 modules"
if [ -n "$bad" ]; then fail "selftest:$bad"; else ok "selftest: $n modules"; fi
''',
    "npm": r'''
# ── {dir}: npm run {script} ──
if [ {gate} ]; then
  [ -d "{dir}/node_modules" ] || cannot "{dir}: node_modules missing — run: (cd {dir} && npm ci)"
  if (cd "{dir}" && npm run --silent {script}) >"$TMP/n" 2>&1; then ok "{dir}: npm run {script}"
  else fail "{dir}: npm run {script}"; tail -15 "$TMP/n" | sed 's/^/   /'; fi
fi
''',
    "tsc": r'''
# ── {dir}: typescript ──
if [ $FAST -eq 0 ]; then
  [ -d "{dir}/node_modules" ] || cannot "{dir}: node_modules missing — run: (cd {dir} && npm ci)"
  if (cd "{dir}" && npx --no-install tsc --noEmit) >"$TMP/t" 2>&1; then ok "{dir}: tsc --noEmit"
  else fail "{dir}: tsc --noEmit"; tail -15 "$TMP/t" | sed 's/^/   /'; fi
fi
''',
    "context_cap": r'''
# ── context files stay short (read every session; long ones get skimmed) ──
n=0; bad=""
while IFS= read -r -d '' f; do
  [ -f "$f" ] || continue
  n=$((n + 1))
  l="$(wc -l < "$f" | tr -d ' ')"
  [ "$l" -le {max} ] || bad="$bad
   $f: $l lines > {max} — turn lessons into checks, or move them to path rules"
done < <(git -c core.quotePath=false ls-files -z -co --exclude-standard -- '*CLAUDE.md' '*AGENTS.md')
if [ -n "$bad" ]; then fail "context files:$bad"; else ok "context files: $n ≤ {max} lines"; fi
''',
}


NO_CHECKS = r'''
# no supported checks were detected for this repo — add a step below.
# Until then this script cannot judge anything, and says so.
cannot "no checks configured — verify.sh judges nothing yet (add a step)"
'''


def render_verify(steps: list[dict]) -> str:
    parts = [VERIFY_HEAD]
    if not [s for s in steps if s["id"] != "context_cap"]:
        parts.append(NO_CHECKS)
    if any(s["id"] in ("python_syntax", "unittest", "pytest", "selftest") for s in steps):
        parts.append(NEED_PY)
    for s in steps:
        tpl = STEP[s["id"]]
        if s["id"] == "shell_syntax":
            dirs = " ".join(f"'{d}/*'" for d in SHELL_DIRS)
            parts.append(tpl.replace("{shell_dirs}", dirs))
        elif s["id"] == "unittest":
            parts.append(tpl.replace("{dir}", s["dir"]))
        elif s["id"] == "npm":
            gate = "1 -eq 1" if s["fast"] else "$FAST -eq 0"
            parts.append(tpl.replace("{dir}", s["dir"]).replace("{script}", s["script"])
                         .replace("{gate}", gate))
        elif s["id"] == "tsc":
            parts.append(tpl.replace("{dir}", s["dir"]))
        elif s["id"] == "context_cap":
            parts.append(tpl.replace("{max}", str(s["max"])))
        else:
            parts.append(tpl)
    parts.append(VERIFY_TAIL)
    return "".join(parts)


PRE_PUSH = r'''#!/bin/sh
#
# pre-push — verify --fast before anything leaves this machine.   (effi harness)
# Arm once per clone:  git config core.hooksPath .githooks
#
# Push, not commit: gating every commit teaches people --no-verify.
# Bypass: SKIP_VERIFY=1 git push …  — and write down why. An unrecorded bypass
# means the gate does not exist.

if [ "$SKIP_VERIFY" = "1" ]; then
  echo "⚠️  skipping verify (SKIP_VERIFY=1) — record why"
  exit 0
fi
[ -t 0 ] || cat >/dev/null
root="$(git rev-parse --show-toplevel 2>/dev/null)" || exit 0
[ -f "$root/scripts/verify.sh" ] || exit 0

bash "$root/scripts/verify.sh" --fast
code=$?
if [ $code -eq 2 ]; then
  echo ""
  echo "⛔ verify could not judge — that is not a pass. See the ⛔ line above."
  echo "   To push anyway: SKIP_VERIFY=1 git push …"
  exit 1
fi
if [ $code -ne 0 ]; then
  echo ""
  echo "❌ verify blocked the push. Fix the ❌ lines above and push again."
  exit 1
fi
exit 0
'''

CHECK_EDITED = r'''#!/usr/bin/env bash
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
'''

GUARD_COMMANDS = r'''#!/usr/bin/env bash
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
'''

AGENTS_POLICY = '''<!-- effi:policy v1 — generated by `effi harness`; edit the wording, keep the markers -->
## Working rules (every agent)

- **Done means `bash scripts/verify.sh` exits 0.** Exit 2 ("cannot judge") is not a pass.
- Stage explicit paths only — never `git add -A`, `git add --all`, `git commit -a`, or a force-push.
- Don't edit `scripts/verify.sh`, `.githooks/`, `.claude/`, `.codex/` or CI config unless the task is about them.
- Keep changes minimal and on-task; end by saying what you changed and why.
<!-- effi:policy end -->
'''


def codex_hooks() -> str:
    # git-root form: the same file is found from any subdirectory (Codex runs
    # hooks from the session cwd). Hooks are skipped until a person trusts this
    # exact definition in Codex's /hooks — effi never bypasses that.
    def cmd(script):
        return f'bash "$(git rev-parse --show-toplevel)/.claude/hooks/{script}"'
    return json.dumps({"hooks": {
        "PreToolUse": [{"matcher": "Bash", "hooks": [
            {"type": "command", "command": cmd("guard-commands.sh"), "timeout": 10}]}],
        "PostToolUse": [{"matcher": "apply_patch", "hooks": [
            {"type": "command", "command": cmd("check-edited.sh"), "timeout": 15}]}],
        "Stop": [{"hooks": [
            {"type": "command", "command": cmd("stop-verify.sh"), "timeout": 300}]}],
    }}, indent=2) + "\n"


STOP_VERIFY = r'''#!/usr/bin/env bash
# Stop: only when VERIFY_ON_STOP=1 (unattended runs), run verify --fast before
# the turn may end; red sends the result back (exit 2).   (effi harness)
# Interactive sessions: does nothing — making people wait every turn gets hooks disabled.
# Gives up after 5 reds in a row so a stuck loop can't spin forever.

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
if [ $rc -eq 0 ]; then echo 0 > "$count_file"; exit 0; fi
n=$((n + 1)); echo "$n" > "$count_file"
if [ "$n" -ge 5 ]; then
  echo 0 > "$count_file"
  echo "verify was red 5 times in a row. Not blocking again — write down the state and stop." >&2
  exit 0
fi
{ echo "verify --fast did not pass (exit $rc, ${n} in a row). Fix it before finishing."; echo "$out" | tail -25; } >&2
exit 2
'''

SETTINGS = {
    "permissions": {
        "deny": [
            "Bash(git push --force *)",
            "Bash(git push -f *)",
            "Bash(git add -A *)",
            "Bash(git add --all *)",
            "Bash(git commit -a *)",
            "Bash(git commit -am *)",
            "Read(./.env)",
            "Read(./.env.*.local)",
            "Read(./.env.local)",
            "Read(./.env.production)",
        ]
    },
    "hooks": {
        "PostToolUse": [{"matcher": "Edit|Write", "hooks": [{
            "type": "command",
            "command": "bash \"${CLAUDE_PROJECT_DIR}/.claude/hooks/check-edited.sh\"",
            "timeout": 15}]}],
        "Stop": [{"hooks": [{
            "type": "command",
            "command": "bash \"${CLAUDE_PROJECT_DIR}/.claude/hooks/stop-verify.sh\"",
            "timeout": 300}]}],
    },
}


def plan(root: Path, report: Optional[dict] = None) -> list[dict]:
    """Files the floor consists of. Each: path, content, executable, action
    (create | same | sidecar). Writes nothing."""
    root = _toplevel(root)
    rep = report or scan(root)
    files = [
        ("scripts/verify.sh", render_verify(rep["steps"]), True),
        (".githooks/pre-push", PRE_PUSH, True),
        (".claude/hooks/check-edited.sh", CHECK_EDITED, True),
        (".claude/hooks/stop-verify.sh", STOP_VERIFY, True),
        (".claude/hooks/guard-commands.sh", GUARD_COMMANDS, True),
        (".claude/settings.json", json.dumps(SETTINGS, indent=2, ensure_ascii=False) + "\n", False),
    ]
    if rep.get("codex"):
        files.append((".codex/hooks.json", codex_hooks(), False))
    # AGENTS.md is read by Codex, Gemini/Antigravity, Grok and others: the one
    # place a policy reaches every agent. Never rewrite the user's file — an
    # existing AGENTS.md without the block gets a sidecar with the block appended.
    agents = root / "AGENTS.md"
    if not agents.exists():
        files.append(("AGENTS.md", "# Agent instructions\n\n" + AGENTS_POLICY, False))
    else:
        cur = agents.read_text(errors="ignore")
        if "<!-- effi:policy" not in cur:
            files.append(("AGENTS.md", cur.rstrip("\n") + "\n\n" + AGENTS_POLICY, False))
    out = []
    for rel, content, exe in files:
        p = root / rel
        if not p.exists():
            action = "create"
        elif p.read_text(errors="ignore") == content:
            action = "same"
        else:
            action = "sidecar"
        out.append({"path": rel, "content": content, "executable": exe, "action": action})
    return out


def apply(root: Path, items: list[dict], arm: bool = False) -> dict:
    """Write the planned files. Never overwrites: a differing file gets
    `<path>.effi-new` beside it. `arm` sets core.hooksPath=.githooks, but only
    when it is unset or already .githooks (someone else's hook dir is theirs)."""
    root = _toplevel(root)
    written = []
    for it in items:
        if it["action"] == "same":
            continue
        target = root / it["path"]
        if it["action"] == "sidecar":
            target = target.with_name(target.name + ".effi-new")
        target.parent.mkdir(parents=True, exist_ok=True)
        target.write_text(it["content"])
        if it["executable"]:
            target.chmod(target.stat().st_mode | stat.S_IXUSR | stat.S_IXGRP | stat.S_IXOTH)
        written.append(str(target.relative_to(root)))
    armed, why = None, None
    if arm:
        cur = _git(root, "config", "core.hooksPath").stdout.strip()
        live = _live_git_hooks(root)
        if cur not in ("", ".githooks"):
            armed, why = False, f"core.hooksPath already points to {cur}"
        elif cur == "" and live:
            # switching hooksPath would silently stop these from running
            armed, why = False, "existing hooks in .git/hooks would stop running: " + ", ".join(live)
        else:
            _git(root, "config", "core.hooksPath", ".githooks")
            armed = True
    return {"written": written, "armed": armed, "why": why}


def _live_git_hooks(root: Path) -> list[str]:
    d = _git(root, "rev-parse", "--git-path", "hooks").stdout.strip()
    hooks = (root / d) if d and not Path(d).is_absolute() else Path(d or root / ".git/hooks")
    if not hooks.is_dir():
        return []
    return sorted(p.name for p in hooks.iterdir()
                  if p.is_file() and not p.name.endswith(".sample"))


# ── prove ─────────────────────────────────────────────────────────────

def _copy_project(root: Path, dest: Path) -> None:
    for rel in _tracked(root):
        src = root / rel
        if not src.is_file():
            continue
        d = dest / rel
        d.parent.mkdir(parents=True, exist_ok=True)
        shutil.copy2(src, d)
    # node_modules is gitignored, so the copy would only ever say ⛔ for node
    # steps — link the real one in (read through; faults go into source files)
    for pkg in [f for f in _tracked(root) if Path(f).name == "package.json" and f.count("/") <= 1]:
        nm = (root / pkg).parent / "node_modules"
        link = (dest / pkg).parent / "node_modules"
        if nm.is_dir() and not link.exists():
            link.symlink_to(nm, target_is_directory=True)
    subprocess.run(["git", "init", "-q"], cwd=dest, check=True)
    subprocess.run(["git", "add", "."], cwd=dest, check=True, capture_output=True)


def _run_verify(cwd: Path, fast: bool = True, env: Optional[dict] = None,
                timeout: int = 900) -> tuple[int, str]:
    cmd = ["bash", "scripts/verify.sh"] + (["--fast"] if fast else [])
    e = dict(os.environ)
    e.update(env or {})
    try:
        r = subprocess.run(cmd, cwd=cwd, env=e, capture_output=True, text=True, timeout=timeout)
    except subprocess.TimeoutExpired:
        return 124, "timeout"
    return r.returncode, r.stdout + r.stderr


def _names(out: str, rel: str) -> bool:
    """True when verify printed `rel` as a detail line (`   <rel>: …`), the
    shape every file-level ❌ uses. A bare substring would match unrelated text."""
    return any(l.startswith(f"   {rel}:") for l in out.splitlines())


def _selftest_fault(base: Path, rel: str, results: list, base_out: str) -> None:
    """Make one module's --selftest fail *when run*, not at the end of the
    file: a selftest usually exits before reaching trailing code, and a fault
    that fires on import would blame the importer instead."""
    src = (base / rel).read_text(errors="ignore").splitlines(keepends=True)
    at = 0
    for i, line in enumerate(src):
        if line.startswith("from __future__"):
            at = i + 1
    if at == 0 and src and src[0].startswith("#!"):
        at = 1
    fault = ('import sys as _effi_prove\n'
             'if _effi_prove.argv[1:2] == ["--selftest"]: raise SystemExit("effi prove: injected selftest failure")\n')
    with tempfile.TemporaryDirectory(prefix="effi-prove-") as t2:
        cp = Path(t2) / "p"
        shutil.copytree(base, cp, symlinks=True)
        (cp / rel).write_text("".join(src[:at]) + fault + "".join(src[at:]))
        got, out = _run_verify(cp)
        caught = got == 1 and _names(out, rel) and not _names(base_out, rel)
        results.append({"name": "failing selftest is caught", "expect": f"1 naming {rel}",
                        "got": got if caught or got != 1 else f"{got} (file not named)",
                        "ok": caught, "file": rel})


def prove(root: Path) -> list[dict]:
    """Break a copy of the project on purpose and check the floor notices.

    Each check: name, expect, got, ok. The user's tree is never modified —
    every fault goes into a temp copy."""
    root = _toplevel(root)
    results = []
    if not (root / "scripts/verify.sh").exists():
        return [{"name": "verify.sh exists", "expect": "file", "got": "missing", "ok": False}]

    rc, _ = _run_verify(root)
    results.append({"name": "verify on the real tree (informational)",
                    "expect": "0/1/2", "got": rc, "ok": rc in (0, 1, 2)})

    rep = scan(root)
    ids = {s["id"] for s in rep["steps"]}
    with tempfile.TemporaryDirectory(prefix="effi-prove-") as td:
        base = Path(td) / "clean"
        _copy_project(root, base)
        base_rc, base_out = _run_verify(base)

        def broken(name: str, rel: str, fault: str, expect: int, loose: bool = False):
            with tempfile.TemporaryDirectory(prefix="effi-prove-") as t2:
                cp = Path(t2) / "p"
                shutil.copytree(base, cp, symlinks=True)
                with open(cp / rel, "a") as fh:
                    fh.write(fault)
                got, out = _run_verify(cp)
                # a tree that is already red returns 1 anyway — the fault only
                # counts as caught when its own file is named on a ❌ detail
                # line that the clean copy did not already print
                if loose:
                    # tool output (tsc, eslint) names the file in its own format
                    caught = got == expect and rel in out and rel not in base_out
                else:
                    caught = got == expect and _names(out, rel) and not _names(base_out, rel)
                results.append({"name": name, "expect": f"{expect} naming {rel}",
                                "got": got if caught or got != expect else f"{got} (file not named)",
                                "ok": caught, "file": rel})

        def _selftest(rel):
            _selftest_fault(base, rel, results, base_out)

        if base_rc not in (0, 1):
            results.append({"name": "clean copy is judgeable", "expect": "0 or 1",
                            "got": base_rc, "ok": False})
        else:
            py = sorted(f for f in _tracked(base) if f.endswith(".py"))
            sh = [f for f in _tracked(base) if f.endswith(".sh")]
            if "python_syntax" in ids and py:
                broken("python syntax error is caught", py[0], "\ndef (:\n", 1)
            if "shell_syntax" in ids and sh:
                broken("shell syntax error is caught", sh[0], "\nif then\n", 1)
            # prove runs --fast, so only a typecheck that runs in --fast can be proven
            ts_steps = [st for st in rep["steps"] if st["fast"] and
                        (st["id"] == "tsc" or (st["id"] == "npm" and st["script"] == "typecheck"))]
            if ts_steps:
                d = ts_steps[0]["dir"]
                pre = "" if d == "." else d + "/"
                ts = sorted(f for f in _tracked(base) if f.startswith(pre)
                            and re.search(r"\.tsx?$", f) and not f.endswith(".d.ts")
                            and "/node_modules/" not in f and not Path(f).name.startswith(("next.config", "vite.config")))
                ts.sort(key=lambda f: (not re.match(rf"{re.escape(pre)}(app|src|lib|components)/", f), f))
                if ts:
                    broken("type error is caught", ts[0],
                           '\nexport const __effiProve: number = "not a number";\n', 1, loose=True)
            # a failing *test*, not just a syntax error — the step that judges
            # behaviour has to go red too
            sts = [st for st in rep["steps"] if st["id"] in ("unittest", "pytest", "selftest")]
            if sts:
                st = sts[0]
                if st["id"] == "selftest":
                    mods = sorted(f for f in py if not re.search(r"(^|/)tests?/", f)
                                  and re.search(SELFTEST_RE, (base / f).read_text(errors="ignore")))
                    if mods:
                        _selftest(mods[0])
                else:
                    tdir = st.get("dir") or "tests"
                    rel = f"{tdir}/test_effi_prove.py"
                    (base / tdir).mkdir(parents=True, exist_ok=True)
                    with tempfile.TemporaryDirectory(prefix="effi-prove-") as t2:
                        cp = Path(t2) / "p"
                        shutil.copytree(base, cp, symlinks=True)
                        (cp / rel).write_text(
                            "import unittest\n\n"
                            "class EffiProve(unittest.TestCase):\n"
                            "    def test_effi_prove_must_fail(self):\n"
                            "        self.assertEqual(1, 2)\n")
                        subprocess.run(["git", "add", rel], cwd=cp, capture_output=True)
                        got, out = _run_verify(cp)
                        caught = (got == 1 and "test_effi_prove" in out
                                  and "test_effi_prove" not in base_out)
                        results.append({"name": "failing test is caught",
                                        "expect": "1 naming test_effi_prove", "got": got,
                                        "ok": caught, "file": rel})
            if ids & {"python_syntax", "unittest", "pytest", "selftest"}:
                got, _ = _run_verify(base, env={"PYTHON": "/nonexistent/python3"})
                results.append({"name": "missing python reads as 'cannot judge', not pass",
                                "expect": 2, "got": got, "ok": got == 2})

    # agent hooks, fed the payload shapes each agent actually sends (measured
    # 2026-10-04) — this proves the scripts; Codex still has to trust them in /hooks
    guard = root / ".claude/hooks/guard-commands.sh"
    if guard.exists():
        def hook(script, payload):
            r = subprocess.run(["bash", str(script)], input=json.dumps(payload),
                               capture_output=True, text=True, cwd=str(root))
            return r.returncode
        blocked = hook(guard, {"tool_name": "Bash", "tool_input": {"command": "git add -A"}})
        allowed = hook(guard, {"tool_name": "Bash", "tool_input": {"command": "git add app.py"}})
        results.append({"name": "command guard blocks git add -A (Codex/Bash payload)",
                        "expect": "2 then 0", "got": f"{blocked} then {allowed}",
                        "ok": blocked == 2 and allowed == 0})
    chk = root / ".claude/hooks/check-edited.sh"
    if chk.exists():
        with tempfile.TemporaryDirectory(prefix="effi-prove-") as t3:
            bad = Path(t3) / "broken.py"
            bad.write_text("def (:\n")
            patch = f"*** Begin Patch\n*** Update File: {bad}\n@@\n-x\n+def (:\n*** End Patch"
            r = subprocess.run(["bash", str(chk)], capture_output=True, text=True,
                               input=json.dumps({"tool_name": "apply_patch", "tool_input": {"command": patch}}))
            results.append({"name": "edit check catches a Codex apply_patch syntax error",
                            "expect": 2, "got": r.returncode, "ok": r.returncode == 2})
    hp = _git(root, "config", "core.hooksPath").stdout.strip()
    hook = root / ".githooks/pre-push"
    armed = hp == ".githooks" and hook.exists() and os.access(hook, os.X_OK)
    results.append({"name": "pre-push gate armed", "expect": True, "got": armed, "ok": armed})
    return results
