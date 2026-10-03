"""effi delegate — hand one task from the Claude main thread to another provider.

Claude Code's own orchestration only drives Claude models. This runs one task
through Codex / Gemini / Grok / a local model via their official CLIs and
returns paths + a short summary. effi owns only: decide · isolate · run ·
gate · record (docs/02-design/delegate.md). Execution belongs to the CLIs.

Trust model — the delegate is untrusted code and untrusted text:
  - it runs inside an OS sandbox: its own (codex seatbelt) or effi's
    sandbox-exec fence, which allows writes only to the job's private temp dir,
    its CLI's state dir and — for write jobs — its worktree, and denies reads
    of other providers' credentials, ssh/cloud keys and effi's own state.
    No sandbox available → refused (fail closed), read jobs included.
  - nothing effi runs unsandboxed trusts the worktree: git is pointed at the
    gitdir effi recorded at creation (the worktree's `.git` file is ignored),
    hooks/fsmonitor are off, diffs are bytes.
  - the gate verifies the *frozen artifact*: a fresh checkout of the base plus
    the hash-pinned patch, with the main tree's verify.sh written in by effi
    (symlinks refused), run fenced without network.
  - apply feeds the validated bytes to `git apply` on stdin; refuses on a
    dirty or moved main tree, failed verify, or guard paths (both sides of
    renames) unless forced. Nothing is ever committed.
  - output is data, not instructions: printed under an "untrusted" banner,
    control/bidi characters stripped.
Known limit: a descendant that calls setsid() leaves the process group and
survives the group kill (macOS has no cgroups to bind it).
Each point traces to a Codex review of the first cut (2026-10-03).
"""
from __future__ import annotations

import hashlib
import json
import os
import re
import secrets
import shutil
import signal
import subprocess
import sys
import tempfile
import time
from datetime import datetime
from pathlib import Path
from typing import Optional

PROVIDERS = ("codex", "gemini", "grok", "local", "claude")
ALIASES = {"openai": "codex", "gpt": "codex", "xai": "grok", "ollama": "local",
           "anthropic": "claude", "google": "gemini"}
REALTIME = re.compile(r"(?i)\b(latest|news|today|current(ly)?|recent|this (week|month)|"
                      r"release[sd]?|20\d\d|right now|trending)\b|최신|요즘|오늘|현재|뉴스")
ASKING = re.compile(r"(?i)\b(what|which|who|when|find|look up|search|research|compare|list|"
                    r"is there|are there)\b|\?|조사|찾아|알려|비교|뭐|무엇|어떤")
REVIEW = re.compile(r"(?i)\b(review|audit|critique|second opinion|double-check)\b|검토|리뷰|감사")
REVIEW_ORDER = ["codex", "grok", "gemini"]
RESEARCH_ORDER = ["grok", "gemini", "codex"]
GUARD_PATHS = re.compile(r"(?i)^(scripts(/|$)|\.githooks(/|$)|\.claude(/|$)|\.codex(/|$)|"
                         r"\.github/workflows(/|$)|\.effi(/|$)|\.gitattributes$|\.gitmodules$|"
                         r"AGENTS\.md$|CLAUDE\.md$)")
ENV_BASE = ("PATH", "HOME", "USER", "LOGNAME", "SHELL", "LANG", "LC_ALL", "LC_CTYPE",
            "TERM", "TZ")
ENV_PROVIDER = {
    "codex": ("OPENAI_API_KEY", "OPENAI_BASE_URL", "CODEX_HOME"),
    "gemini": ("GEMINI_API_KEY", "GOOGLE_API_KEY", "GOOGLE_CLOUD_PROJECT", "GOOGLE_GENAI_USE_VERTEXAI"),
    "grok": ("XAI_API_KEY", "GROK_HOME"),
    "claude": ("ANTHROPIC_API_KEY", "CLAUDE_CONFIG_DIR"),
    "local": ("OLLAMA_HOST", "EFFI_LOCAL_MODEL", "PYTHONPATH"),
}
STATE_DIRS = {"codex": ["~/.codex"], "gemini": ["~/.gemini"], "grok": ["~/.grok"],
              "claude": ["~/.claude", "~/.claude.json", "~/.config/claude"],
              "local": ["~/.ollama", "~/.config/effi"]}
# inside its own state dir a delegate may write sessions/logs, never the files a
# later *unsandboxed* session of that CLI would load (Codex review, 2nd pass)
CONFIG_OF = {"codex": ["~/.codex/config.toml", "~/.codex/hooks.json", "~/.codex/rules", "~/.codex/prompts",
                       "~/.codex/skills", "~/.codex/AGENTS.md"],
             "gemini": ["~/.gemini/settings.json", "~/.gemini/GEMINI.md", "~/.gemini/commands",
                        "~/.gemini/extensions"],
             "grok": ["~/.grok/config.toml", "~/.grok/settings.json", "~/.grok/hooks", "~/.grok/skills",
                      "~/.grok/agents", "~/.grok/AGENTS.md"],
             "claude": ["~/.claude/settings.json", "~/.claude/settings.local.json", "~/.claude/hooks",
                        "~/.claude/skills", "~/.claude/agents", "~/.claude/commands", "~/.claude/plugins",
                        "~/.claude/CLAUDE.md"],
             "local": []}
# never readable by any delegate (its own provider's state dir is exempted)
SECRET_PATHS = ["~/.ssh", "~/.aws", "~/.gnupg", "~/.config/gh", "~/.netrc", "~/.docker/config.json",
                "~/.kube", "~/.config/gcloud", "~/.npmrc", "~/.pypirc", "~/.config/effi"]
# CLIs that sandbox themselves; nested sandbox_apply is refused by macOS
# (measured: codex exec → "sandbox_apply: Operation not permitted")
NATIVE_SANDBOX = {"codex"}
JUNK = [":(exclude,glob)**/__pycache__/**", ":(exclude,glob)**/*.pyc", ":(exclude,glob)**/.DS_Store",
        ":(exclude,glob)**/node_modules/**", ":(exclude,glob)**/.pytest_cache/**"]
SUMMARY_LINES = 15
DEFAULT_TIMEOUT = 900


def norm(provider: Optional[str]) -> Optional[str]:
    if not provider:
        return None
    p = provider.lower().strip()
    return ALIASES.get(p, p)


# ── where things live ─────────────────────────────────────────────────

def home_dir() -> Path:
    return Path(os.environ.get("EFFI_DELEGATE_HOME") or Path.home() / ".config/effi/delegate")


def job_dir(job_id: str) -> Path:
    return home_dir() / job_id


def worktrees_dir() -> Path:
    # not under ~/.config/effi: the fence denies reads there, and every process
    # needs to resolve its own cwd through the parent directories
    return Path(os.environ.get("EFFI_DELEGATE_WORKTREES") or Path.home() / ".cache/effi/worktrees")


def _private_dir(p: Path) -> Path:
    p.mkdir(parents=True, exist_ok=True)
    p.chmod(0o700)
    return p


def _write_private(p: Path, data) -> None:
    if isinstance(data, str):
        data = data.encode()
    p.write_bytes(data)
    p.chmod(0o600)


# ── decide ────────────────────────────────────────────────────────────

def usable_providers() -> dict:
    """provider → (usable, reason) from effi's own connection check."""
    from effi_core import connect_report
    out = {}
    for row in connect_report(probe=False).get("providers") or []:
        pid = row.get("id")
        if pid not in PROVIDERS:
            continue
        ok = row.get("connection") == "connected" and bool(row.get("cli"))
        out[pid] = (ok, row.get("detail") or row.get("connection") or "?")
    return out


def decide(task: str, to: Optional[str] = None, kind: Optional[str] = None,
           write: bool = False, usable: Optional[dict] = None) -> dict:
    """Pick a provider. Returns {provider|None, reason, candidates, skipped}."""
    from effi_core import recommend
    rec = recommend(task)
    mode = rec.get("mode") or "cruise"
    dom = rec.get("domain")
    # intent is read directly, not only via the domain classifier: measured
    # 2026-10-03 it filed "review this diff for regressions" under debug and
    # "latest MCP registry changes this month" under test
    if to:
        cands, why = [norm(to)], f"forced (--to {to})"
    elif kind == "review" or dom == "review" or (not write and REVIEW.search(task)):
        cands, why = list(REVIEW_ORDER), "review → a different model than the main thread"
    elif not write and REALTIME.search(task) and (dom == "research" or ASKING.search(task)):
        cands, why = list(RESEARCH_ORDER), "realtime research → live search first"
    else:
        prim = norm(rec.get("primary_provider"))
        if prim == "claude":
            return {"provider": None, "mode": mode, "domain": dom, "candidates": [],
                    "skipped": [], "reason": "recommended provider is Claude — stay on the main thread "
                                             "(use --to <provider> to delegate anyway)"}
        cands = [prim] + [norm(a.get("provider")) for a in rec.get("alternates") or []]
        why = f"{dom} → recommended {prim}"
    seen, ordered = set(), []
    for c in cands:
        if c and c not in seen and (to or c != "claude"):
            seen.add(c)
            ordered.append(c)
    usable = usable if usable is not None else usable_providers()
    skipped = []
    for c in ordered:
        if c not in PROVIDERS:
            skipped.append((c, "unknown provider"))
        elif c == "local" and write:
            skipped.append((c, "local runs text only — no write jobs"))
        elif c == "local" and mode == "apex" and not to:
            skipped.append((c, "apex never picks local"))
        elif not usable.get(c, (False, "not configured"))[0]:
            skipped.append((c, f"not usable: {usable.get(c, (False, 'not configured'))[1]}"))
        else:
            return {"provider": c, "mode": mode, "domain": dom, "candidates": ordered,
                    "skipped": skipped, "reason": why}
    return {"provider": None, "mode": mode, "domain": dom, "candidates": ordered,
            "skipped": skipped, "reason": f"{why} — no usable provider"}


# ── isolate ───────────────────────────────────────────────────────────

def _fence_available() -> bool:
    return sys.platform == "darwin" and shutil.which("sandbox-exec") is not None


def _real(p) -> str:
    return os.path.realpath(os.path.expanduser(str(p)))


def _q(s: str) -> str:
    return s.replace("\\", "\\\\").replace('"', '\\"')


def fence_profile(writable: list, network: bool, deny_read: list = (), allow_read: list = (),
                  deny_write: list = ()) -> str:
    """sandbox-exec profile: allow by default, then deny every write outside
    `writable` (+ /dev/null · tty · fd), deny reads of `deny_read`, and
    optionally deny outbound network."""
    allow = "".join(f' (subpath "{_q(_real(p))}")' for p in writable)
    prof = ("(version 1)(allow default)(deny file-write*)"
            f'(allow file-write*{allow} (literal "/dev/null") (literal "/dev/tty")'
            ' (regex #"^/dev/fd/") (regex #"^/dev/ttys"))')
    if deny_read:
        prof += "(deny file-read*" + "".join(f' (subpath "{_q(_real(p))}")' for p in deny_read) + ")"
    if deny_write:  # later rules win: carve config files back out of an allowed dir
        prof += "(deny file-write*" + "".join(f' (subpath "{_q(_real(p))}")' for p in deny_write) + ")"
    if allow_read:  # later rules win: re-open the job's own dir inside a denied parent
        prof += "(allow file-read*" + "".join(f' (subpath "{_q(_real(p))}")' for p in allow_read) + ")"
    if not network:
        prof += "(deny network-outbound (remote ip))"
    return prof


# what another provider must not read. Mostly the whole state dir — but other
# CLIs read ~/.claude for skill/settings compatibility (measured: denying all
# of it silently stopped grok's tool loop), so only Claude's private parts.
PRIVATE_OF = {"codex": ["~/.codex"], "gemini": ["~/.gemini"], "grok": ["~/.grok"],
              "claude": ["~/.claude/projects", "~/.claude/history.jsonl", "~/.claude/.credentials.json",
                         "~/.claude/shell-snapshots", "~/.claude/file-history", "~/.claude/session-env",
                         "~/.claude/todos", "~/.claude.json"],
              "local": ["~/.ollama"]}


def _secret_paths_for(provider: str) -> list:
    own = {_real(p) for p in STATE_DIRS.get(provider, [])}
    others = [p for prov, ps in PRIVATE_OF.items() if prov != provider for p in ps]
    return [p for p in SECRET_PATHS + others
            if _real(p) not in own and os.path.exists(_real(p))]


def _env(provider: str, tmp: Path) -> dict:
    keep = ENV_BASE + ENV_PROVIDER.get(provider, ())
    env = {k: os.environ[k] for k in keep if k in os.environ}
    env.update({k: v for k, v in os.environ.items() if k.startswith("LC_")})
    env.update({"TMPDIR": str(tmp), "EFFI_NO_SPLASH": "1"})
    return env


GIT_SAFE = ["-c", "core.hooksPath=/dev/null", "-c", "core.fsmonitor=false",
            "-c", "core.untrackedCache=false", "-c", "protocol.file.allow=never"]


def _git(cwd: Path, *args: str) -> subprocess.CompletedProcess:
    return subprocess.run(["git", "-C", str(cwd), *GIT_SAFE, *args], capture_output=True, text=True)


def _git_wt(gitdir: str, wt: Path, *args: str, text: bool = True) -> subprocess.CompletedProcess:
    """git on a delegate's worktree without trusting anything inside it: the
    gitdir effi recorded at creation, never the worktree's own `.git` file;
    external diff/textconv off; and — because `git add` runs .gitattributes
    filters, which a delegate controls — inside a no-network fence that may
    only write the repo's git dir, the worktree and temp."""
    argv = ["git", *GIT_SAFE, f"--git-dir={gitdir}", f"--work-tree={wt}", *args]
    if _fence_available():
        common = Path(gitdir).parent.parent  # <repo>/.git/worktrees/<name> → <repo>/.git
        argv = ["sandbox-exec", "-p", fence_profile([common, wt, tempfile.gettempdir()], network=False,
                                                    deny_read=_secret_paths_for("none")), *argv]
    return subprocess.run(argv, capture_output=True, text=text, cwd=str(wt))


def _repo_root(start: Path) -> Path:
    r = _git(start, "rev-parse", "--show-toplevel")
    if r.returncode != 0:
        raise ValueError(f"{start} is not a git repository")
    return Path(r.stdout.strip())


# ── run ───────────────────────────────────────────────────────────────

def _argv(provider: str, prompt: str, cwd: Path, write: bool, out: Path,
          model: Optional[str]) -> list[str]:
    m = ["-m", model] if model else []
    if provider == "codex":
        return ["codex", "exec", "-s", "workspace-write" if write else "read-only",
                "-C", str(cwd), "-o", str(out), *m, prompt]
    if provider == "gemini":
        return ["gemini", "-p", prompt, "--approval-mode", "auto_edit" if write else "plan",
                "-o", "text", *m]
    if provider == "grok":
        # without --max-turns, `grok -p` is one model turn with no tool loop
        # (measured: exit 0 after 10 s, nothing edited)
        return ["grok", "-p", prompt, "--cwd", str(cwd), "--max-turns", "30",
                "--output-format", "plain",
                "--permission-mode", "acceptEdits" if write else "plan", *m]
    if provider == "claude":
        return ["claude", "-p", prompt, "--permission-mode", "acceptEdits" if write else "plan", *m]
    if provider == "local":
        effi_run = Path(__file__).resolve().parents[1] / "bin/effi-run"
        return [str(effi_run), *(["-m", model] if model else []), prompt]
    raise ValueError(provider)


PROMPT_HEAD = {
    False: ("You are a delegated worker for another agent. READ-ONLY job: do not create, edit or "
            "delete files and do not run commands that change state. Answer the task, then end "
            "with a summary of at most 10 lines."),
    True: ("You are a delegated worker for another agent. Work only inside the current directory "
           "(a disposable git worktree). Keep changes minimal and focused on the task. Do not "
           "commit, push, or touch scripts/, .githooks/, .claude/, .codex/, CI config, "
           ".gitattributes, AGENTS.md or CLAUDE.md. End with a summary of at most 10 lines: "
           "what you changed and why."),
}


def _kill_group(pid: int) -> None:
    for sig in (signal.SIGTERM, signal.SIGKILL):
        try:
            os.killpg(pid, sig)
        except (ProcessLookupError, PermissionError):
            return
        time.sleep(0.5 if sig == signal.SIGTERM else 0)


def _run_fenced(argv: list[str], cwd: Path, env: dict, writable: list, network: bool,
                timeout: int, log: Path, native: bool = False,
                deny_read: list = (), allow_read: list = (),
                deny_write: list = ()) -> tuple[int, float, bool]:
    """Run argv in its own process group, inside effi's fence unless the CLI
    sandboxes itself. The whole group is killed afterwards — on timeout, on
    interruption, and after a normal exit (no stragglers keep writing).
    Returns (exit, seconds, timed_out)."""
    if not native:
        argv = ["sandbox-exec", "-p", fence_profile(writable, network, deny_read, allow_read, deny_write), *argv]
    t0 = time.time()
    timed_out, rc = False, 124
    with open(log, "wb") as fh:
        os.chmod(log, 0o600)
        p = subprocess.Popen(argv, cwd=str(cwd), env=env, stdin=subprocess.DEVNULL,
                             stdout=fh, stderr=subprocess.STDOUT, start_new_session=True)
        try:
            rc = p.wait(timeout=timeout)
        except subprocess.TimeoutExpired:
            timed_out = True
        finally:
            _kill_group(p.pid)
            try:
                p.wait(timeout=5)
            except subprocess.TimeoutExpired:
                pass
    return (124 if timed_out else rc), round(time.time() - t0, 1), timed_out


def _changes(name_status_z: bytes) -> list[tuple[str, list[str]]]:
    """Parse `--name-status -z`: renames/copies carry two paths."""
    parts = name_status_z.decode("utf-8", "surrogateescape").split("\0")
    out, i = [], 0
    while i < len(parts) and parts[i]:
        st = parts[i]
        n = 2 if st[:1] in ("R", "C") else 1
        out.append((st, parts[i + 1:i + 1 + n]))
        i += 1 + n
    return out


def _freeze(gitdir: str, wt: Path, base: str, jd: Path) -> dict:
    """Everything the delegate produced, relative to base, as one patch of
    bytes (untracked files, binaries and its own commits included)."""
    # caches the delegate's own test runs leave behind are never part of the change,
    # even in a repo whose .gitignore forgot them
    steps = [
        _git_wt(gitdir, wt, "add", "-A", "--", ".", *JUNK),
        _git_wt(gitdir, wt, "diff", "--cached", "--binary", "--no-ext-diff",
                "--no-textconv", base, text=False),
        _git_wt(gitdir, wt, "diff", "--cached", "--name-status", "-z", "--no-renames",
                base, text=False),
    ]
    for st in steps:
        if st.returncode != 0:
            err = st.stderr if isinstance(st.stderr, str) else st.stderr.decode(errors="ignore")
            raise RuntimeError(f"freezing the delegate's changes failed: {err.strip()[:200]}")
    patch, ns = steps[1].stdout, steps[2].stdout
    _write_private(jd / "changes.patch", patch)
    changes = _changes(ns)
    paths = sorted({p for _, ps in changes for p in ps})
    return {"sha256": hashlib.sha256(patch).hexdigest(), "bytes": len(patch), "files": paths,
            "name_status": [f"{st}\t" + "\t".join(ps) for st, ps in changes],
            "guard_touched": [p for p in paths if GUARD_PATHS.match(p)]}


def _verify_gate(repo: Path, base: str, jd: Path, timeout: int) -> dict:
    """Verify the frozen artifact, not the delegate's worktree: fresh checkout
    of base + the hash-pinned patch, then the main tree's verify.sh written in
    by effi (refusing symlinks), run fenced with no network."""
    trusted = _git(repo, "show", f"{base}:scripts/verify.sh")
    if trusted.returncode != 0:
        return {"verify": "absent"}
    if not _fence_available() and not os.environ.get("EFFI_DELEGATE_UNSANDBOXED"):
        return {"verify": "skipped", "why": "no OS fence to run the delegate's code in"}
    patch = (jd / "changes.patch").read_bytes()
    # outside ~/.config/effi: the fence denies reads there, and git walks parents
    vt = Path(tempfile.mkdtemp(prefix="effi-verify-"))
    vdir = vt / "tree"
    try:
        r = _git(repo, "worktree", "add", "-q", "--detach", str(vdir), base)
        if r.returncode != 0:
            return {"verify": 2, "why": "could not create the verify checkout"}
        if patch:
            a = subprocess.run(["git", "-C", str(vdir), *GIT_SAFE, "apply", "--binary", "-"],
                               input=patch, capture_output=True)
            if a.returncode != 0:
                return {"verify": 2, "why": "frozen patch does not apply to its own base"}
        sd, vf = vdir / "scripts", vdir / "scripts/verify.sh"
        if sd.is_symlink() or vf.is_symlink() or (sd.exists() and not sd.is_dir()):
            return {"verify": 1, "why": "scripts/ or scripts/verify.sh is a symlink — refused"}
        sd.mkdir(exist_ok=True)
        fd = os.open(vf, os.O_WRONLY | os.O_CREAT | os.O_TRUNC | getattr(os, "O_NOFOLLOW", 0), 0o755)
        with os.fdopen(fd, "w") as fh:
            fh.write(trusted.stdout)
        tmp = _private_dir(vt / "tmp")
        env = {k: os.environ[k] for k in ENV_BASE if k in os.environ}
        env["TMPDIR"] = str(tmp)
        rc, secs, to = _run_fenced(["bash", "scripts/verify.sh", "--fast"], vdir, env,
                                   [vdir, tmp], network=False, timeout=timeout,
                                   log=jd / "verify.log", deny_read=_secret_paths_for("none"))
        return {"verify": rc, "verify_seconds": secs, "verify_timeout": to}
    finally:
        _git(repo, "worktree", "remove", "--force", str(vdir))
        shutil.rmtree(vt, ignore_errors=True)


def run(task: str, to: Optional[str] = None, kind: Optional[str] = None, write: bool = False,
        model: Optional[str] = None, timeout: int = DEFAULT_TIMEOUT, start: Optional[Path] = None,
        usable: Optional[dict] = None) -> dict:
    repo = _repo_root(Path(start or os.getcwd()))
    d = decide(task, to=to, kind=kind, write=write, usable=usable)
    job_id = datetime.now().strftime("d%y%m%d-%H%M%S-") + secrets.token_hex(2)
    meta = {"id": job_id, "at": datetime.now().isoformat(timespec="seconds"), "repo": str(repo),
            "task": task, "kind": kind or d.get("domain"), "write": write, "decision": d,
            "provider": d["provider"], "model": model or "cli-default"}
    if not d["provider"]:
        meta["status"] = "not_delegated"
        _record(meta, None)
        return meta
    native = d["provider"] in NATIVE_SANDBOX
    if not native and not _fence_available():
        meta["status"] = "refused"
        meta["why"] = (f"{d['provider']} has no sandbox of its own and effi's fence (macOS "
                       "sandbox-exec) is not available here — refused")
        _record(meta, None)
        return meta

    jd = _private_dir(job_dir(job_id))
    # private temp in the system temp area (mode 700) — not under ~/.config/effi,
    # which the fence makes unreadable
    tmp = Path(tempfile.mkdtemp(prefix=f"effi-{job_id}-"))
    meta["tmp"] = str(tmp)
    head = _git(repo, "rev-parse", "HEAD").stdout.strip()
    dirty = bool(_git(repo, "status", "--porcelain").stdout.strip())
    meta.update({"base": head, "main_dirty_at_start": dirty})
    cwd = repo
    writable = [tmp] + [Path(p) for p in STATE_DIRS.get(d["provider"], [])]
    gitdir = None
    if write:
        wt = _private_dir(worktrees_dir()) / f"{repo.name}-{job_id}"
        r = _git(repo, "worktree", "add", "-q", "-b", f"effi/delegate-{job_id}", str(wt), head)
        if r.returncode != 0:
            meta["status"] = "error"
            meta["why"] = f"git worktree add failed: {r.stderr.strip()[:200]}"
            _record(meta, jd)
            return meta
        # record the gitdir now — before the delegate runs, wt/.git is still effi's
        gitdir = _git(wt, "rev-parse", "--absolute-git-dir").stdout.strip()
        meta.update({"worktree": str(wt), "gitdir": gitdir})
        cwd = wt
        writable.append(wt)

    prompt = PROMPT_HEAD[write] + "\n\nTASK:\n" + task
    _write_private(jd / "prompt.md", prompt)
    out = jd / "output.md"
    argv = _argv(d["provider"], prompt, cwd, write, out, model)
    rc, secs, timed_out = _run_fenced(argv, cwd, _env(d["provider"], tmp), writable, network=True,
                                      timeout=timeout, log=jd / "run.log", native=native,
                                      deny_read=_secret_paths_for(d["provider"]),
                                      deny_write=CONFIG_OF.get(d["provider"], []))
    if not out.exists() or not out.read_bytes().strip():
        shutil.copyfile(jd / "run.log", out)
    out.chmod(0o600)
    meta.update({"exit": rc, "seconds": secs,
                 "fence": "native (writes confined; reads unrestricted)" if native else "effi",
                 "status": "timeout" if timed_out else ("done" if rc == 0 else "failed")})
    if write and meta["status"] == "done":
        try:
            meta["artifact"] = _freeze(gitdir, wt, head, jd)
        except RuntimeError as e:
            meta.update({"status": "error", "why": str(e)})
            _record(meta, jd)
            return meta
        meta.update(_verify_gate(repo, head, jd, timeout) if meta["artifact"]["files"]
                    else {"verify": "nothing to verify"})
    _record(meta, jd)
    return meta


# ── record ────────────────────────────────────────────────────────────

def _record(meta: dict, jd: Optional[Path]) -> None:
    home = _private_dir(home_dir())
    if jd:
        _write_private(jd / "meta.json", json.dumps(meta, indent=2, ensure_ascii=False) + "\n")
    line = {k: meta.get(k) for k in ("id", "at", "repo", "task", "provider", "kind", "write",
                                     "status", "exit", "seconds", "verify", "worktree")}
    line["reason"] = (meta.get("decision") or {}).get("reason")
    led = home / "ledger.ndjson"
    with open(led, "a") as fh:
        fh.write(json.dumps(line, ensure_ascii=False) + "\n")
    led.chmod(0o600)
    try:
        if meta.get("provider") and meta.get("status") in ("done", "failed", "timeout"):
            from effi_core import record_usage
            record_usage(meta["provider"], meta.get("model") or "cli-default",
                         task=(meta.get("task") or "")[:80], session=meta["id"])
    except Exception:
        pass


def load(job_id: str) -> dict:
    if not re.fullmatch(r"d\d{6}-\d{6}-[0-9a-f]{4}", job_id or ""):
        raise KeyError(job_id)
    p = job_dir(job_id) / "meta.json"
    if not p.exists():
        raise KeyError(job_id)
    return json.loads(p.read_text())


def ledger(limit: int = 20) -> list[dict]:
    led = home_dir() / "ledger.ndjson"
    if not led.exists():
        return []
    rows = []
    for l in led.read_text().splitlines():
        try:
            rows.append(json.loads(l))
        except ValueError:
            continue
    return rows[-limit:]


# ── untrusted output ──────────────────────────────────────────────────

_CTRL = re.compile(r"\x1b\[[0-9;?]*[ -/]*[@-~]|\x1b\][^\x07]*\x07|[\x00-\x08\x0b-\x1f\x7f]|"
                   r"[​-‏‪-‮⁦-⁩]")


def summary(meta: dict, lines: int = SUMMARY_LINES) -> list[str]:
    """Tail of the delegate's answer, stripped of escape/control/bidi chars
    and capped per line. It is data for the main thread, not instructions."""
    p = job_dir(meta["id"]) / "output.md"
    if not p.exists():
        return []
    text = _CTRL.sub("", p.read_text(errors="ignore"))
    return [l.rstrip()[:300] for l in text.strip().splitlines()][-lines:]


# ── apply · clean ─────────────────────────────────────────────────────

def apply(job_id: str, force: bool = False, start: Optional[Path] = None) -> dict:
    """Apply a write job's frozen patch to the main tree without committing.
    Refuses on: unfinished job, artifact hash mismatch (never forceable),
    verify ≠ 0, guard paths touched, main HEAD moved, main tree dirty —
    the last four only unless forced."""
    meta = load(job_id)
    repo = _repo_root(Path(start or meta["repo"]))
    art = meta.get("artifact")
    if not meta.get("write") or meta.get("status") != "done" or not art:
        return {"applied": False, "problems": ["not a finished write job"]}
    patch_p = job_dir(job_id) / "changes.patch"
    patch = patch_p.read_bytes() if patch_p.exists() else b""
    if hashlib.sha256(patch).hexdigest() != art["sha256"]:
        return {"applied": False, "problems": ["patch artifact changed since the job finished"]}
    if not art["files"]:
        return {"applied": False, "problems": ["the delegate changed nothing"]}
    problems = []
    if meta.get("verify") != 0:
        problems.append(f"verify was {meta.get('verify')} (needs 0)")
    if art.get("guard_touched"):
        problems.append("touches guard paths: " + ", ".join(art["guard_touched"]))
    head = _git(repo, "rev-parse", "HEAD").stdout.strip()
    if head != meta.get("base"):
        problems.append(f"main HEAD moved ({meta.get('base', '')[:7]} → {head[:7]}) — verify ran on the old base")
    if _git(repo, "status", "--porcelain", "--untracked-files=no").stdout.strip():
        problems.append("main tree has uncommitted changes — the combination was never verified")
    warnings = []
    untracked = _git(repo, "ls-files", "--others", "--exclude-standard").stdout.split()
    if untracked:
        warnings.append(f"{len(untracked)} untracked file(s) in the main tree were not part of verify: "
                        + ", ".join(untracked[:5]))
    if problems and not force:
        return {"applied": False, "problems": problems, "warnings": warnings}
    base_cmd = ["git", "-C", str(repo), *GIT_SAFE, "apply", "--binary"]
    chk = subprocess.run(base_cmd + ["--check", "-"], input=patch, capture_output=True)
    if chk.returncode != 0:
        return {"applied": False,
                "problems": problems + ["git apply --check failed: " + chk.stderr.decode(errors="ignore").strip()[:300]]}
    r = subprocess.run(base_cmd + ["-"], input=patch, capture_output=True)
    meta["applied_at"] = datetime.now().isoformat(timespec="seconds")
    meta["applied_forced"] = bool(problems)
    _write_private(job_dir(job_id) / "meta.json", json.dumps(meta, indent=2, ensure_ascii=False) + "\n")
    return {"applied": r.returncode == 0, "problems": problems, "warnings": warnings, "files": art["files"],
            "error": r.stderr.decode(errors="ignore").strip()[:300] if r.returncode else None}


def clean(job_id: str) -> dict:
    meta = load(job_id)
    removed = []
    wt = meta.get("worktree")
    if wt:
        repo = Path(meta["repo"])
        _git(repo, "worktree", "remove", "--force", wt)
        _git(repo, "branch", "-D", f"effi/delegate-{job_id}")
        removed.append(wt)
    if meta.get("tmp") and Path(meta["tmp"]).name.startswith("effi-"):
        shutil.rmtree(meta["tmp"], ignore_errors=True)
    return {"removed": removed, "kept": str(job_dir(job_id))}
