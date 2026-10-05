"""effi verify — a change judged by a different model, and the verdict recorded.

The verification ledger's core move (generator ≠ verifier): take a change —
the working tree or a commit — have a model from a *different provider* than
the one that wrote it review it read-only (through `effi delegate`), parse a
machine-readable verdict, and record it where git can carry it.

  verdict   CONFIRMED  no defect that blocks the change
            PLAUSIBLE  only minor or uncertain findings
            REFUTED    at least one real defect (any high/critical finding forces it)
            UNVERIFIED nobody could judge — never a pass
  record    before commit: .effi/verify/<patch-id>.json  (local)
            on a commit:   git note  refs/notes/effi      (one JSON line per verification)
            `attach` moves a pre-commit verdict onto the commit with the same patch-id.

Provenance (who wrote which line) is git-ai's job; this records only what
nobody else does — whether a second model checked it, and what it said.
"""
from __future__ import annotations

import hashlib
import json
import os
import re
import shutil
import subprocess
import tempfile
from datetime import datetime
from pathlib import Path
from typing import Optional

import effi_delegate as dl

VERDICTS = ("CONFIRMED", "PLAUSIBLE", "REFUTED")
BLOCKING = {"critical", "high"}
NOTES_REF = "effi"
SCHEMA = "effi.verify/1"
MAX_DIFF_CHARS = 120_000
# effi's own bookkeeping is never part of the change being judged — the task
# log is written *after* a verdict and would otherwise change the fingerprint
OWN_FILES = (":(exclude,glob)**/.effi/**", ":(exclude,glob).effi/**",
             ":(exclude,glob)**/tasks/*/log.md", ":(exclude,glob)tasks/*/log.md")
EXIT = {"CONFIRMED": 0, "PLAUSIBLE": 0, "REFUTED": 1, "UNVERIFIED": 2}

CONTRACT = """You are verifying a code change written by another model ({generator}).
Review it for correctness, security and regressions. You may read files in the
repository for context. Do not modify anything.

Finish with exactly one fenced JSON block and nothing after it:

```json
{{"verdict": "CONFIRMED | PLAUSIBLE | REFUTED",
  "summary": "one sentence",
  "findings": [{{"severity": "critical|high|medium|low", "file": "path", "line": 0,
                "issue": "what is wrong", "fix": "one-line fix"}}]}}
```

CONFIRMED = nothing found that should block this change.
PLAUSIBLE = only minor or uncertain findings.
REFUTED   = at least one real defect.
Report only issues you can point to in the change; say CONFIRMED with an empty
list if you find none.

CHANGE ({kind}{rev}):
```diff
{diff}
```
"""


def _git(repo: Path, *args: str, env: Optional[dict] = None, text: bool = True,
         inp=None) -> subprocess.CompletedProcess:
    # lossless text: a non-UTF-8 diff must fingerprint and reach the reviewer,
    # not crash the run (Codex, 2026-10-05)
    kw = {"encoding": "utf-8", "errors": "surrogateescape"} if text else {}
    argv = ["git", "-C", str(repo), *dl.GIT_SAFE, *args]
    if args and args[0] in ("add", "diff", "diff-tree") and dl._fence_available():
        # `add` runs .gitattributes clean filters and `diff` may run textconv —
        # the change under review controls which ones. Same containment as the
        # delegate's freeze: no network, writes only to the git dir and temp
        # (Codex, 2026-10-05)
        # the real .git is read-only in here: new blobs go to a throwaway object
        # dir, the index is a throwaway file — a filter that tries to rewrite
        # hooks or config gets nothing (Codex, 2026-10-05)
        e = env or {}
        # only this snapshot's own dir — not all of the system temp dir, where
        # other repositories may live
        writable = [p for p in (os.path.dirname(e.get("GIT_INDEX_FILE", "")),
                                e.get("GIT_OBJECT_DIRECTORY", "")) if p]
        argv = ["sandbox-exec", "-p", dl.fence_profile(writable, network=False,
                                                         deny_read=dl._secret_paths_for("none")), *argv]
    return subprocess.run(argv, capture_output=True, text=text, env=env, input=inp, **kw)


def _filters_in_play(repo: Path) -> bool:
    """Could `git add` / `git diff` run a filter or textconv command here?
    Asks git itself (`check-attr`), so every attributes source counts — in-tree,
    info/attributes, core.attributesFile, and the default global and system
    files a hand-rolled scan missed (Codex, 2026-10-05). Unknown → True."""
    # through _git: GIT_SAFE keeps core.fsmonitor and hooks off here too
    files = _git(repo, "ls-files", "-z", "-co", "--exclude-standard", text=False)
    if files.returncode != 0:
        return True
    if not files.stdout:
        return False
    r = _git(repo, "check-attr", "--stdin", "-z", "filter", "diff", text=False, inp=files.stdout)
    if r.returncode != 0:
        return True
    parts = r.stdout.split(b"\0")
    # records are path, attribute, value
    for k in range(2, len(parts), 3):
        if parts[k] not in (b"unspecified", b"unset", b""):
            return True
    return False


def _empty_tree(repo: Path) -> str:
    """The empty tree's id in this repo's hash format — the base of a root commit."""
    return _ok(_git(repo, "hash-object", "-t", "tree", "--stdin", inp=""), "hash-object").strip()


def repo_root(start: Optional[Path] = None) -> Path:
    # same resolution as effi_core.project_root: EFFI_PROJECT wins over cwd,
    # so verify and `effi log COMPLETE` look at the same repository
    if start is None and os.environ.get("EFFI_PROJECT"):
        start = Path(os.path.expanduser(os.environ["EFFI_PROJECT"]))
    r = _git(Path(start or os.getcwd()), "rev-parse", "--show-toplevel")
    if r.returncode != 0:
        raise ValueError("not a git repository")
    return Path(r.stdout.strip())


def patch_id(diff: str) -> Optional[str]:
    """Exact fingerprint of the change. Not `git patch-id`: it ignores
    whitespace, so `"a b"` and `"ab"` inside a string share an id and a verdict
    could be attached to code nobody reviewed (Codex, via effi verify, 2026-10-05)."""
    if not diff.strip():
        return None
    return hashlib.sha256(diff.encode("utf-8", "surrogateescape")).hexdigest()[:32]


class SnapshotError(RuntimeError):
    """A git step failed — the snapshot cannot be trusted, so nothing is judged."""


def _ok(r: subprocess.CompletedProcess, what: str) -> str:
    # every git step is checked: a failed `add` would leave the throwaway index
    # at HEAD and fingerprint "nothing changed" (Codex, 2026-10-05)
    if r.returncode != 0:
        raise SnapshotError(f"git {what} failed: {(r.stderr or '').strip()[:200]}")
    return r.stdout


def _diff(repo: Path, *args: str, env: Optional[dict] = None) -> str:
    """A diff with effi's bookkeeping excluded on the diff itself too — so a
    committed task log never enters a commit's or a range's fingerprint."""
    # --ignore-submodules=none: a repo setting must not hide a submodule
    # pointer change from the fingerprint (Codex, 2026-10-05)
    return _ok(_git(repo, "diff", "--binary", "--no-ext-diff", "--no-textconv",
                    "--ignore-submodules=none", *args,
                    "--", ".", *OWN_FILES, env=env), "diff")


def _snapshot(repo: Path, base: str) -> str:
    """Everything that differs from `base` now, untracked files included, via a
    throwaway index (the user's staging area is untouched)."""
    # a submodule with uncommitted work is invisible to a cached diff (only its
    # commit pointer is recorded) — refuse rather than fingerprint half of it
    st = _ok(_git(repo, "status", "--porcelain=v2", "--ignore-submodules=none"), "status")
    for line in st.splitlines():
        parts = line.split(" ")
        if line[:1] in ("1", "2") and len(parts) > 2 and parts[2].startswith("S") and parts[2][2:4] != "..":
            raise SnapshotError(f"submodule {parts[-1]} has uncommitted changes — commit inside it first")
    if not dl._fence_available() and _filters_in_play(repo):
        # no OS fence here (e.g. Linux) and the change could pick a filter or
        # textconv command — don't run it unconfined, don't pretend to judge
        raise SnapshotError("filter/textconv attributes are in play and no sandbox is available "
                            "to run them in")
    with tempfile.TemporaryDirectory(prefix="effi-verify-idx-") as td:
        real_objects = _ok(_git(repo, "rev-parse", "--path-format=absolute", "--git-path", "objects"),
                           "rev-parse").strip()
        (Path(td) / "objects").mkdir()
        env = dict(os.environ, GIT_INDEX_FILE=str(Path(td) / "index"),
                   GIT_OBJECT_DIRECTORY=str(Path(td) / "objects"),
                   GIT_ALTERNATE_OBJECT_DIRECTORIES=real_objects)
        # a fresh index from HEAD — never a copy of the user's: a copy carries
        # assume-unchanged/skip-worktree flags that make `add -A` skip edits,
        # and a fresh mtime hides same-size edits (Codex, 2026-10-05)
        _ok(_git(repo, "read-tree", "HEAD", env=env), "read-tree")
        _ok(_git(repo, "add", "-A", "--", ".", env=env), "add")
        # files the user force-staged despite .gitignore are part of the change
        forced = _ok(_git(repo, "ls-files", "-z", "-i", "-c", "--exclude-standard"), "ls-files")
        forced = [f for f in forced.split("\0") if f and ((repo / f).exists() or (repo / f).is_symlink())]
        if forced:
            _ok(_git(repo, "add", "-f", "--", *forced, env=env), "add -f")
        # then drop effi's bookkeeping from the throwaway index — naming an
        # ignored path in an exclude pathspec makes `git add` itself fail
        # (measured on effi-code, whose .gitignore ignores tasks/)
        _ok(_git(repo, "rm", "-r", "--cached", "-q", "--ignore-unmatch", "--",
                 ":(glob)**/.effi/**", ":(glob).effi/**",
                 ":(glob)**/tasks/*/log.md", ":(glob)tasks/*/log.md", env=env), "rm --cached")
        return _diff(repo, "--cached", base, env=env)


def collect(repo: Path, rev: Optional[str] = None) -> dict:
    """The change to verify. No rev: the working tree vs HEAD (untracked files
    included). A rev: that commit against its first parent. `A..B` / `A...B`:
    a range. Raises SnapshotError when any git step fails."""
    if rev and (".." in rev):
        # the base a COMPLETE check compares against: A for A..B, the
        # merge-base for A...B (what `git diff A...B` diffs from)
        left, sym, right = rev.partition("...") if "..." in rev else rev.partition("..")
        left, right = left or "HEAD", right or "HEAD"
        base = (_ok(_git(repo, "merge-base", left, right), "merge-base").strip() if sym == "..."
                else _ok(_git(repo, "rev-parse", "--verify", left), "rev-parse").strip())
        head = _ok(_git(repo, "rev-parse", "--verify", f"{right}^{{commit}}"), "rev-parse").strip()
        diff = _diff(repo, base, head)
        return {"kind": "range", "rev": rev, "head": head, "base": base, "diff": diff,
                "patch_id": patch_id(diff)}
    if rev:
        sha = _git(repo, "rev-parse", "--verify", f"{rev}^{{commit}}").stdout.strip()
        if not sha:
            raise ValueError(f"unknown revision: {rev}")
        parent = _git(repo, "rev-parse", "--verify", "--quiet", f"{sha}^").stdout.strip() or None
        # never `git show`: on a merge it prints a combined diff that hides
        # hunks not changed against every parent (Codex, 2026-10-05)
        base = parent or _empty_tree(repo)   # a root commit diffs from the empty tree
        diff = _diff(repo, base, sha)
        return {"kind": "commit", "rev": sha, "base": base, "diff": diff, "patch_id": patch_id(diff)}
    head = _ok(_git(repo, "rev-parse", "HEAD"), "rev-parse").strip()
    diff = _snapshot(repo, head)
    return {"kind": "working", "rev": None, "base": head, "diff": diff, "patch_id": patch_id(diff)}


def fingerprint_since(repo: Path, base: Optional[str]) -> Optional[str]:
    """Fingerprint of everything that differs from `base` right now — committed
    or not, untracked files included. Equals a verdict's patch_id exactly when
    the work at hand is the change that was verified, whether it is still in
    the working tree or was committed as-is on top of `base`. Raises
    SnapshotError when a git step fails (the caller must refuse, not pass)."""
    if not base:
        return None
    return patch_id(_snapshot(repo_root(Path(repo)), base))   # always the repository root


def pick_reviewer(generator: str, to: Optional[str] = None,
                  usable: Optional[dict] = None) -> tuple[Optional[str], list]:
    """A provider other than the generator. Cross-provider is the rule —
    forcing the generator's own provider is refused, not silently allowed."""
    gen = dl.norm(generator)
    usable = usable if usable is not None else dl.usable_providers()
    if to:
        cands = dl.GEMINI_VIA if dl.norm(to) == "gemini" else [dl.norm(to)]
    else:
        cands = []
        for c in dl.REVIEW_ORDER:
            cands += dl.GEMINI_VIA if c == "gemini" else [c]
    skipped = []
    for c in cands:
        same = c == gen or (gen == "gemini" and c == "antigravity") or (gen == "antigravity" and c == "gemini")
        if same:
            skipped.append((c, "same provider as the generator"))
        elif not usable.get(c, (False, "not configured"))[0]:
            skipped.append((c, f"not usable: {usable.get(c, (False, 'not configured'))[1]}"))
        else:
            return c, skipped
    return None, skipped


def parse_verdict(text: str) -> dict:
    """The last JSON object with a `verdict`. Anything unparseable or outside
    the contract is UNVERIFIED — never read as a pass."""
    # the final fenced block, whatever it contains — not "the last block that
    # looks like valid JSON" (a truncated final verdict must not let an earlier
    # one through; Codex, 2026-10-05)
    if text.count("```") % 2:
        # an unterminated final fence: the answer was cut off mid-verdict
        return {"verdict": "UNVERIFIED", "why": "the answer ends inside an unterminated code block",
                "findings": []}
    blocks = [b.strip() for b in re.findall(r"```[a-zA-Z]*\s*\n?(.*?)```", text, re.S)]
    if not blocks:
        blocks = re.findall(r"(\{[^{}]*\"verdict\".*\})", text, re.S)
    # only the final block is the answer: a malformed last verdict must not fall
    # back to an earlier one (Codex, 2026-10-05: CONFIRMED then a broken
    # REFUTED read as CONFIRMED)
    for raw in blocks[-1:]:
        try:
            obj = json.loads(raw)
        except ValueError:
            return {"verdict": "UNVERIFIED", "why": "the final verdict block is not valid JSON", "findings": []}
        if not isinstance(obj, dict) or "verdict" not in obj:
            return {"verdict": "UNVERIFIED", "why": "the final block has no verdict", "findings": []}
        verdict = str(obj.get("verdict", "")).strip().upper()
        raw_f = obj.get("findings", [])
        if raw_f is None:
            raw_f = []
        if not isinstance(raw_f, list) or not all(isinstance(f, dict) for f in raw_f):
            # a dropped malformed finding could hide a high one — fail closed
            return {"verdict": "UNVERIFIED", "why": "findings are not a list of objects", "findings": []}
        findings = raw_f
        if verdict not in VERDICTS:
            return {"verdict": "UNVERIFIED", "why": f"verdict outside the contract: {verdict!r}",
                    "findings": findings}
        out = {"verdict": verdict, "summary": str(obj.get("summary") or "")[:300], "findings": findings}
        blocking = [f for f in findings if str(f.get("severity", "")).lower() in BLOCKING]
        if blocking and verdict != "REFUTED":
            # the reviewer's own findings outrank its headline
            out.update(verdict="REFUTED", normalized=f"{verdict} → REFUTED: {len(blocking)} high/critical finding(s)")
        return out
    return {"verdict": "UNVERIFIED", "why": "no JSON verdict in the reviewer's answer", "findings": []}


def _pending_dir(repo: Path) -> Path:
    p = repo / ".effi/verify"
    p.mkdir(parents=True, exist_ok=True)
    return p


def _note_line(rec: dict) -> str:
    keep = ("schema", "verdict", "generator", "reviewer", "job", "at", "patch_id", "kind", "base", "rev",
            "findings_total", "findings_blocking", "summary")
    return json.dumps({k: rec.get(k) for k in keep}, ensure_ascii=False, sort_keys=True)


def add_note(repo: Path, sha: str, rec: dict) -> bool:
    r = _git(repo, "notes", f"--ref={NOTES_REF}", "append", "-m", _note_line(rec), sha)
    return r.returncode == 0


def notes(repo: Path, rev: str = "HEAD") -> list[dict]:
    r = _git(repo, "notes", f"--ref={NOTES_REF}", "show", rev)
    out = []
    for line in r.stdout.splitlines() if r.returncode == 0 else []:
        try:
            out.append(json.loads(line))
        except ValueError:
            continue
    return out


def verify(start: Optional[Path] = None, rev: Optional[str] = None, generator: str = "claude",
           to: Optional[str] = None, task: Optional[str] = None, timeout: int = dl.DEFAULT_TIMEOUT,
           usable: Optional[dict] = None) -> dict:
    repo = repo_root(start)
    try:
        ch = collect(repo, rev)
    except (SnapshotError, UnicodeError, OSError) as e:
        rec = {"schema": SCHEMA, "at": datetime.now().isoformat(timespec="seconds"), "kind": "working",
               "rev": rev, "patch_id": None, "generator": dl.norm(generator),
               "verdict": "UNVERIFIED", "why": str(e)}
        _record(repo, rec, task)
        return rec
    rec = {"schema": SCHEMA, "at": datetime.now().isoformat(timespec="seconds"), "kind": ch["kind"],
           "rev": ch["rev"], "head": ch.get("head"), "base": ch.get("base"), "patch_id": ch["patch_id"],
           "generator": dl.norm(generator)}
    if not ch["diff"].strip():
        rec.update(verdict="UNVERIFIED", why="nothing to verify — the change is empty")
        _record(repo, rec, task)
        return rec
    if len(ch["diff"]) > MAX_DIFF_CHARS:
        rec.update(verdict="UNVERIFIED",
                   why=f"change is {len(ch['diff']):,} chars (> {MAX_DIFF_CHARS:,}) — verify it in smaller commits")
        _record(repo, rec, task)
        return rec
    reviewer, skipped = pick_reviewer(generator, to, usable)
    rec["skipped"] = skipped
    if not reviewer:
        rec.update(verdict="UNVERIFIED", why="no provider other than the generator is usable")
        _record(repo, rec, task)
        return rec
    # the fingerprint keeps the exact bytes; the reviewer gets valid text
    # (undecodable bytes shown as U+FFFD) so the prompt can be written at all
    shown = ch["diff"].encode("utf-8", "surrogateescape").decode("utf-8", "replace")
    prompt = CONTRACT.format(generator=rec["generator"], kind=ch["kind"],
                             rev=f" {ch['rev']}" if ch["rev"] else "", diff=shown)
    try:
        job = dl.run(prompt, to=reviewer, kind="review", start=repo, timeout=timeout, usable=usable)
    except (OSError, UnicodeError, ValueError, RuntimeError) as e:
        rec.update(verdict="UNVERIFIED", why=f"could not run the review: {e}"[:300],
                   reviewer={"provider": reviewer, "model": None})
        _record(repo, rec, task)
        return rec
    rec["job"] = job.get("id")
    rec["reviewer"] = {"provider": job.get("provider"), "model": job.get("model")}
    if job.get("status") != "done":
        rec.update(verdict="UNVERIFIED", why=f"review job {job.get('status')}: {job.get('why') or ''}".strip())
    else:
        out = (dl.job_dir(job["id"]) / "output.md").read_text(errors="ignore")
        rec.update(parse_verdict(out))
    rec["findings_total"] = len(rec.get("findings") or [])
    rec["findings_blocking"] = sum(1 for f in rec.get("findings") or []
                                   if str(f.get("severity", "")).lower() in BLOCKING)
    _record(repo, rec, task)
    return rec


def _record(repo: Path, rec: dict, task: Optional[str]) -> None:
    """Every outcome lands — an UNVERIFIED after a CONFIRMED must replace it
    in the task log, or COMPLETE passes on a stale verdict (Codex, 2026-10-05).
    Only real verdicts become notes or pending records."""
    rec.setdefault("findings_total", len(rec.get("findings") or []))
    rec.setdefault("findings_blocking", 0)
    if rec["verdict"] == "UNVERIFIED":
        pass
    elif rec["kind"] == "commit" and rec.get("rev"):
        rec["noted"] = add_note(repo, rec["rev"], rec)
    elif rec["kind"] == "range" and rec.get("head"):
        # a range verdict lives on the range's last commit, with its base
        rec["noted"] = add_note(repo, rec["head"], rec)
    elif rec["kind"] == "working" and rec.get("patch_id"):
        (_pending_dir(repo) / f"{rec['patch_id']}.json").write_text(
            json.dumps(rec, ensure_ascii=False, indent=2) + "\n")
    if task:
        from effi_core import append_task_log
        msg = (f"verdict={rec['verdict']} reviewer={(rec.get('reviewer') or {}).get('provider')} "
               f"generator={rec['generator']} findings={rec.get('findings_total', 0)} job={rec.get('job')} "
               f"fp={rec.get('patch_id')} base={rec.get('base')}")
        # the task log lives where `effi log` reads it: the selected project
        # (EFFI_PROJECT may name a subdirectory of the repository)
        append_task_log(task, "VERIFICATION", msg)


def attach(start: Optional[Path] = None, rev: str = "HEAD") -> dict:
    """Move a pre-commit verdict onto the commit that has the same patch-id
    (i.e. exactly the change that was verified — nothing more, nothing less)."""
    repo = repo_root(start)
    try:
        ch = collect(repo, rev)
    except SnapshotError as e:
        return {"attached": False, "why": str(e)}
    if not ch["patch_id"]:
        return {"attached": False, "why": "empty commit"}
    p = repo / ".effi/verify" / f"{ch['patch_id']}.json"
    if not p.exists():
        return {"attached": False, "why": "no verification recorded for exactly this change — "
                                          "run `effi verify` before committing, or `effi verify HEAD`"}
    rec = json.loads(p.read_text())
    if rec.get("base") and ch.get("base") and rec["base"] != ch["base"]:
        return {"attached": False, "why": "the verified change sat on a different parent commit"}
    rec["attached_from"] = "working"
    ok = add_note(repo, ch["rev"], rec)
    if ok:
        p.unlink()
        return {"attached": True, "rev": ch["rev"], "verdict": rec.get("verdict")}
    return {"attached": False, "rev": ch["rev"], "verdict": rec.get("verdict"),
            "why": "git notes append failed (is refs/notes/effi writable?)"}


# ── the COMPLETE gate ─────────────────────────────────────────────────

GRADE_RE = re.compile(r"\bgrade=(XS|S|M|L|XL)\b")
VERDICT_RE = re.compile(r"\bverdict=(CONFIRMED|PLAUSIBLE|REFUTED|UNVERIFIED)\b")
FP_RE = re.compile(r"\bfp=([0-9a-f]{16,64})\b")
BASE_RE = re.compile(r"\bbase=([0-9a-f]{7,64})\b")


def complete_check(log_text: str, repo: Optional[Path] = None) -> tuple[bool, str]:
    """M+ work may log COMPLETE only after a passing VERIFICATION *of this
    work*: the last verdict must pass, and — given the repo — everything that
    changed since the verdict's base must fingerprint exactly as what was
    reviewed (Codex, 2026-10-05: a stale verdict let later edits through).
    The grade comes from TRIAGE; no TRIAGE → treated as M."""
    grade = verdict = fp = base = None
    for line in log_text.splitlines():
        if "[TRIAGE]" in line:
            m = GRADE_RE.search(line)
            grade = m.group(1) if m else grade
        if "[VERIFICATION]" in line:
            m = VERDICT_RE.search(line)
            verdict = m.group(1) if m else verdict
            fm, bm = FP_RE.search(line), BASE_RE.search(line)
            fp, base = (fm.group(1) if fm else None), (bm.group(1) if bm else None)
    if (grade or "M") in ("XS", "S"):
        return True, f"grade {grade} — verification optional"
    if verdict not in ("CONFIRMED", "PLAUSIBLE"):
        return False, (f"grade {grade or 'M (no TRIAGE)'} needs a passing verdict before COMPLETE "
                       f"(last: {verdict or 'none'}) — run: effi verify --task <name>")
    if repo is None:
        return True, f"last verdict {verdict}"
    if not fp or not base:
        return False, "the last verdict does not say which change it reviewed — re-run effi verify --task <name>"
    try:
        now = fingerprint_since(Path(repo), base)
    except SnapshotError as e:
        return False, f"could not snapshot the work ({e}) — COMPLETE refused"
    if now != fp:
        return False, "the work changed since it was verified — re-run effi verify --task <name>"
    return True, f"last verdict {verdict} covers exactly this change"
