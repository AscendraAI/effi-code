"""effi trust — what third-party code your Claude Code runs, and what changed.

Inventory the tools that execute with your permissions — MCP servers, plugins
(their content and the hooks they register), marketplaces, skills, hooks in
your settings — flag the risky shapes, and compare against a baseline you
approved (`accept`). The baseline is the part nobody else does: the attacks
that worked were updates *after* approval (postmark-mcp: 15 clean versions,
then a BCC; CVE-2025-54136: a tool that changes after you approve it).

Read-only except `accept`, which writes effi's own files under
~/.config/effi/ (trust-lock.json + a per-machine HMAC key, both mode 600).

Secrets: nothing user-written is printed or stored verbatim. Hook commands,
MCP args after an option flag, URL userinfo/query and env/header values are
never shown; they only enter a keyed fingerprint.

Evidence for the rules: docs/01-plan/v5-direction.md §4.4 · §6.1 (H2).
Review 2026-10-03 (clean context) found 13 issues in the first cut; each fixed
one has a regression test in tests/test_trust.py.
"""
from __future__ import annotations

import hashlib
import hmac
import json
import os
import re
import secrets
import stat
from datetime import datetime
from pathlib import Path
from typing import Optional
from urllib.parse import urlsplit, urlunsplit

OFFICIAL_MARKETPLACE_REPOS = ("anthropics/",)
RUNNERS = ("npx", "bunx", "pnpx", "uvx")
# runner flags that take no value; any other flag is assumed to take one
BOOL_FLAGS = {"-y", "--yes", "-q", "--quiet", "--silent", "--no-install", "--offline",
              "--isolated", "--refresh", "--no-cache", "-n"}
# runner flags whose value *is* the package
PACKAGE_FLAGS = {"-p", "--package", "--from"}
NPM_EXACT = re.compile(r"^(@[^/@]+/)?[^@]+@v?\d+\.\d+\.\d+([-+][0-9A-Za-z.-]+)?$")
PY_EXACT = re.compile(r"^[A-Za-z0-9_.\[\],-]+==[0-9][0-9A-Za-z.+!-]*$")
SENSITIVE_EVENTS = {"PreToolUse", "PostToolUse", "UserPromptSubmit", "PermissionRequest",
                    "UserPromptExpansion", "SessionStart"}
RISK_SCAN_MAX_BYTES = 512_000
DIR_MAX_FILES = 5_000
RISKY = [
    (re.compile(r"curl[^\n|]*\|\s*(ba|z)?sh\b"), "pipes a download into a shell"),
    (re.compile(r"wget[^\n|]*\|\s*(ba|z)?sh\b"), "pipes a download into a shell"),
    (re.compile(r"base64\s+(-d|--decode)"), "decodes base64 (often hides a payload)"),
    (re.compile(r"eval\s+\"?\$\("), "evals command output"),
    (re.compile(r"[​-‏‪-‮⁦-⁩]"), "hidden/bidi unicode (can hide instructions)"),
    (re.compile(r"(?i)ignore (all )?(previous|prior) instructions"), "prompt-injection phrasing"),
]
SEVERITY_ORDER = {"high": 0, "medium": 1, "info": 2}


def _home(home: Optional[Path]) -> Path:
    return Path(home) if home else Path.home()


def _read_json(p: Path):
    try:
        return json.loads(Path(p).read_text())
    except (OSError, ValueError, UnicodeDecodeError):
        return None


def _obj(x) -> dict:
    return x if isinstance(x, dict) else {}


# ── fingerprints (keyed: a short secret inside a hashed spec can't be brute-forced from the lock)

def _key_path(home: Optional[Path]) -> Path:
    return lock_path(home).with_name("trust-key")


def _key(home: Optional[Path], create: bool = False) -> bytes:
    p = _key_path(home)
    try:
        return bytes.fromhex(p.read_text().strip())
    except (OSError, ValueError):
        if not create:
            return b""
    p.parent.mkdir(parents=True, exist_ok=True)
    k = secrets.token_bytes(32)
    p.write_text(k.hex() + "\n")
    p.chmod(0o600)
    return k


def _fp(obj, key: bytes) -> str:
    data = json.dumps(obj, sort_keys=True, ensure_ascii=False, default=str).encode()
    return hmac.new(key or b"effi-trust", data, hashlib.sha256).hexdigest()[:16]


# ── MCP ───────────────────────────────────────────────────────────────

def _parse_runner(spec: dict) -> Optional[str]:
    """The package a runner (npx/uvx/…) will fetch, or None if not a runner.
    Option values are skipped — they are where tokens live."""
    if Path(str(spec.get("command") or "")).name not in RUNNERS:
        return None
    args = [str(a) for a in spec.get("args") or [] if isinstance(a, (str, int, float))]
    i = 0
    while i < len(args):
        a = args[i]
        if a.startswith("-"):
            name, eq, val = a.partition("=")
            if name in PACKAGE_FLAGS:
                return val if eq else (args[i + 1] if i + 1 < len(args) else "")
            if not eq and name not in BOOL_FLAGS:
                i += 1  # skip this flag's value
            i += 1
            continue
        return a
    return ""


def mcp_unpinned(spec: dict) -> Optional[str]:
    """Package string when a runner would fetch something not pinned to an
    exact version (`@latest`, ranges, majors, dist-tags, bare names)."""
    pkg = _parse_runner(_obj(spec))
    if pkg is None or pkg == "":
        return None
    if NPM_EXACT.match(pkg) or PY_EXACT.match(pkg):
        return None
    return pkg


def _redact_url(url: str) -> str:
    try:
        u = urlsplit(url)
    except ValueError:
        return "<url>"
    host = u.hostname or ""
    if u.port:
        host += f":{u.port}"
    return urlunsplit((u.scheme, host, u.path, "", ""))


def _redact_mcp(spec: dict) -> dict:
    """What the fingerprint sees: env/header *names* (adding one is a change),
    never their values — rotating a key must not read as a change."""
    out = {k: v for k, v in spec.items() if k not in ("env", "headers")}
    for k in ("env", "headers"):
        if isinstance(spec.get(k), dict):
            out[k] = sorted(spec[k])
    return out


# ── directory content ────────────────────────────────────────────────

def _walk(d: Path) -> list[Path]:
    """Files under d, following symlinks once (visited set breaks loops)."""
    seen, files = set(), []
    for top, dirs, names in os.walk(d, followlinks=True):
        real = os.path.realpath(top)
        if real in seen:
            dirs[:] = []
            continue
        seen.add(real)
        dirs[:] = sorted(x for x in dirs if x != ".git")
        for n in sorted(names):
            files.append(Path(top) / n)
            if len(files) >= DIR_MAX_FILES:
                return files
    return files


def _dir_fp(d: Path, key: bytes) -> tuple[str, list[Path]]:
    """Every file's full content goes into the hash (streamed), so a same-size
    edit anywhere is a change."""
    h = hmac.new(key or b"effi-trust", digestmod=hashlib.sha256)
    files = _walk(d)
    for p in files:
        h.update(str(p.relative_to(d)).encode())
        try:
            with open(p, "rb") as fh:
                for chunk in iter(lambda: fh.read(65536), b""):
                    h.update(chunk)
        except OSError:
            h.update(b"<unreadable>")
    return h.hexdigest()[:16], files


# ── inventory ─────────────────────────────────────────────────────────

def _hooks_from(settings: dict, scope: str, key: bytes) -> list[dict]:
    out = []
    for event, groups in _obj(settings.get("hooks")).items():
        if not isinstance(groups, list):
            continue
        for gi, g in enumerate(groups):
            g = _obj(g)
            for hi, hk in enumerate(g.get("hooks") or []):
                hk = _obj(hk)
                cmd = str(hk.get("command") or "")
                # label from the program name only — arguments can carry tokens
                first = cmd.split()[0].strip("\"'") if cmd.split() else ""
                prog = ("inline shell" if first in ("if", "case", "for", "while", "[", "[[", "{", "(")
                        else Path(first).name if first else str(hk.get("type") or "?"))
                out.append({"kind": "hook", "name": f"{event}#{gi}.{hi}", "scope": scope,
                            "event": event, "matcher": g.get("matcher") or "*", "program": prog,
                            "fp": _fp({"matcher": g.get("matcher"), "hook": hk}, key)})
    return out


def _skills_in(sdir: Path, scope: str, lock: dict, key: bytes) -> list[dict]:
    out = []
    if not sdir.is_dir():
        return out
    for entry in sorted(sdir.iterdir()):
        try:
            real = entry.resolve()
        except (OSError, RuntimeError):
            continue
        if not real.is_dir() or not (real / "SKILL.md").exists():
            continue
        fp, files = _dir_fp(real, key)
        hits, exe = [], []
        for f in files:
            try:
                st = f.stat()
            except OSError:
                continue
            if st.st_mode & stat.S_IXUSR and f.name != "SKILL.md":
                exe.append(str(f.relative_to(real)))
            if st.st_size > RISK_SCAN_MAX_BYTES:
                continue
            try:
                text = f.read_text(errors="ignore")
            except OSError:
                continue
            for rx, why in RISKY:
                m = rx.search(text)
                if m:
                    line = text.count("\n", 0, m.start()) + 1
                    hits.append(f"{f.relative_to(real)}:{line} {why}")
        src = _obj(lock.get(entry.name))
        out.append({"kind": "skill", "name": entry.name, "scope": scope,
                    "source": src.get("source") or src.get("sourceUrl"),
                    "executables": exe, "risky": hits, "fp": fp})
    return out


def inventory(home: Optional[Path] = None, project: Optional[Path] = None) -> list[dict]:
    H = _home(home)
    key = _key(home)
    items: list[dict] = []
    cj = _obj(_read_json(H / ".claude.json"))

    def add_mcp(name, spec, scope):
        spec = _obj(spec)
        pkg = _parse_runner(spec)
        target = _redact_url(str(spec["url"])) if spec.get("url") else \
            " ".join(x for x in [Path(str(spec.get("command") or "")).name, pkg or ""] if x)
        items.append({"kind": "mcp", "name": str(name), "scope": scope,
                      "transport": spec.get("type") or ("stdio" if spec.get("command") else "?"),
                      "unpinned": mcp_unpinned(spec), "target": target,
                      "fp": _fp(_redact_mcp(spec), key)})

    for n, s in _obj(cj.get("mcpServers")).items():
        add_mcp(n, s, "user")
    for proj, d in _obj(cj.get("projects")).items():
        for n, s in _obj(_obj(d).get("mcpServers")).items():
            add_mcp(n, s, f"local:{proj}")
    if project:
        for n, s in _obj(_obj(_read_json(Path(project) / ".mcp.json")).get("mcpServers")).items():
            add_mcp(n, s, f"project:{project}")

    km = _obj(_read_json(H / ".claude/plugins/known_marketplaces.json"))
    mk_repo = {}
    for name, m in km.items():
        src = _obj(_obj(m).get("source"))
        repo = str(src.get("repo") or src.get("url") or src.get("path") or "?")
        mk_repo[name] = repo
        items.append({"kind": "marketplace", "name": name, "scope": None, "repo": _redact_url(repo)
                      if "://" in repo else repo,
                      "official": any(repo.startswith(o) for o in OFFICIAL_MARKETPLACE_REPOS),
                      "fp": _fp(src, key)})

    settings = _obj(_read_json(H / ".claude/settings.json"))
    enabled = dict(_obj(settings.get("enabledPlugins")))
    if project:
        for f in ("settings.json", "settings.local.json"):
            enabled.update(_obj(_obj(_read_json(Path(project) / ".claude" / f)).get("enabledPlugins")))
    ip = _obj(_obj(_read_json(H / ".claude/plugins/installed_plugins.json")).get("plugins"))
    for pkey, entries in ip.items():
        entries = entries if isinstance(entries, list) else [entries]
        mk = pkey.rsplit("@", 1)[1] if "@" in pkey else "?"
        for e in entries:
            e = _obj(e)
            path = Path(e["installPath"]) if e.get("installPath") else None
            hj = _obj(_read_json(path / "hooks/hooks.json")) if path else {}
            events = sorted(_obj(hj.get("hooks")).keys())
            content = _dir_fp(path, key)[0] if path and path.is_dir() else None
            items.append({"kind": "plugin", "name": pkey, "scope": e.get("scope") or "user",
                          "marketplace": mk,
                          "official": any(mk_repo.get(mk, "").startswith(o) for o in OFFICIAL_MARKETPLACE_REPOS),
                          "enabled": bool(enabled.get(pkey, False)), "version": e.get("version"),
                          "sha": str(e.get("gitCommitSha") or "")[:12], "hook_events": events,
                          "fp": _fp([e.get("version"), e.get("gitCommitSha"), content], key)})

    lock = _obj(_obj(_read_json(H / ".agents/.skill-lock.json")).get("skills"))
    items += _skills_in(H / ".claude/skills", "user", lock, key)
    items += _hooks_from(settings, "user", key)
    if project:
        items += _skills_in(Path(project) / ".claude/skills", f"project:{project}", {}, key)
        for f in ("settings.json", "settings.local.json"):
            items += _hooks_from(_obj(_read_json(Path(project) / ".claude" / f)),
                                 f"project:{project}:{f}", key)
    return items


# ── findings ──────────────────────────────────────────────────────────

def _id(it: dict) -> str:
    return f"{it['kind']}:{it['name']}" + (f"@{it['scope']}" if it.get("scope") else "")


def findings(items: list[dict], lock: Optional[dict] = None) -> list[dict]:
    out = []
    nosource: list[str] = []

    def add(sev, it, msg, fix=None):
        out.append({"severity": sev, "id": _id(it), "kind": it["kind"], "msg": msg, "fix": fix})

    for it in items:
        k = it["kind"]
        if k == "mcp" and it.get("unpinned"):
            add("high", it, f"runs `{it['unpinned']}` without an exact version — every start executes "
                            "whatever was published last",
                "pin it (npm view <pkg> version → <pkg>@<x.y.z>; python: <pkg>==<x.y.z>)")
        if k == "plugin":
            sens = sorted(set(it["hook_events"]) & SENSITIVE_EVENTS)
            if it["enabled"] and not it["official"] and sens:
                add("high", it, f"enabled third-party plugin hooks {', '.join(sens)} "
                                f"({len(it['hook_events'])} events) — sees every prompt/tool call",
                    "keep only if you trust the publisher; review its hooks/hooks.json")
            elif not it["enabled"] and it["hook_events"] and not it["official"]:
                add("info", it, f"installed but disabled; registers {len(it['hook_events'])} hook events if re-enabled",
                    "uninstall if unused: /plugin uninstall")
        if k == "marketplace" and not it["official"]:
            add("medium", it, f"third-party marketplace ({it['repo']}) — Anthropic does not review these")
        if k == "skill":
            for h in it["risky"]:
                add("high", it, f"risky pattern — {h}", "read the file before the next session loads it")
            if not it.get("source") and it.get("scope") == "user":
                nosource.append(it["name"])
            if it["executables"]:
                add("info", it, f"ships {len(it['executables'])} executable file(s): "
                                + ", ".join(it["executables"][:3]))

    if nosource:
        # one line, not one per skill: thirteen identical warnings teach people to stop reading
        names = ", ".join(nosource[:6]) + (f" … +{len(nosource) - 6}" if len(nosource) > 6 else "")
        out.append({"severity": "medium", "id": f"skills without a recorded source ({len(nosource)})",
                    "kind": "skill",
                    "msg": f"can't tell where they came from or whether they changed: {names}",
                    "fix": "effi trust accept records their content hash; reinstall via "
                           "`npx skills add <repo>` to record the source"})

    if lock is None:
        out.append({"severity": "info", "id": "baseline", "kind": "baseline",
                    "msg": "no approved baseline yet — changes after approval can't be detected",
                    "fix": "effi trust accept"})
    elif lock.get("_unreadable"):
        # a corrupt lock must not read as "everything is merely new"
        out.append({"severity": "high", "id": "baseline", "kind": "baseline",
                    "msg": "approved baseline is unreadable — change detection is OFF",
                    "fix": "inspect ~/.config/effi/trust-lock.json, then effi trust accept"})
    else:
        known = lock["items"]
        for it in items:
            i = _id(it)
            if i not in known:
                add("medium", it, "new since your approved baseline", "review, then effi trust accept")
            elif known[i] != it["fp"]:
                add("high", it, "CHANGED since you approved it (update, new hook, new code)",
                    "review what changed, then effi trust accept")
        current = {_id(it) for it in items}
        scopes_seen = {it.get("scope") for it in items}
        for i in sorted(set(known) - current):
            # entries scoped to a project we didn't scan this time aren't "removed"
            sc = i.split("@", 1)[1] if "@project:" in i else None
            if sc and sc not in scopes_seen:
                continue
            out.append({"severity": "info", "id": i, "kind": i.split(":", 1)[0],
                        "msg": "removed since your approved baseline", "fix": None})
    out.sort(key=lambda f: (SEVERITY_ORDER[f["severity"]], f["kind"], f["id"]))
    return out


# ── baseline ──────────────────────────────────────────────────────────

def lock_path(home: Optional[Path] = None) -> Path:
    env = os.environ.get("EFFI_TRUST_LOCK")
    if env:
        return Path(env)
    return _home(home) / ".config/effi/trust-lock.json"


def load_lock(home: Optional[Path] = None) -> Optional[dict]:
    p = lock_path(home)
    if not p.exists():
        return None
    data = _read_json(p)
    if not isinstance(data, dict) or not isinstance(data.get("items"), dict):
        return {"_unreadable": True}
    return data


def accept(home: Optional[Path] = None, project: Optional[Path] = None) -> tuple[Path, list[dict]]:
    """Record the current state as approved. Inventories itself *after* the
    key exists, so the stored fingerprints match every later scan. Entries
    scoped to other projects are kept — accepting in repo B must not drop
    repo A's approvals."""
    p = lock_path(home)
    p.parent.mkdir(parents=True, exist_ok=True)
    _key(home, create=True)
    items = inventory(home=home, project=project)
    old = load_lock(home)
    keep = {}
    if old and not old.get("_unreadable"):
        scanned = {it.get("scope") for it in items}
        keep = {i: fp for i, fp in old["items"].items()
                if "@project:" in i and i.split("@", 1)[1] not in scanned}
    data = {"schema": 2, "accepted_at": datetime.now().isoformat(timespec="seconds"),
            "items": {**keep, **{_id(it): it["fp"] for it in items}}}
    p.write_text(json.dumps(data, indent=2, ensure_ascii=False) + "\n")
    p.chmod(0o600)
    return p, items
