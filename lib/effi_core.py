#!/usr/bin/env python3
"""effi-code core: catalog routing, account rotation, local model pick, domain triage.

Designed for token-efficient multi-provider orchestration (Claude / OpenAI-Codex /
Gemini / Grok / local Ollama). See catalog/ and docs/why.md.
"""
from __future__ import annotations

import json
import os
import re
import subprocess
import sys
import urllib.request
from contextlib import contextmanager
from dataclasses import dataclass, asdict
from datetime import date, datetime, timedelta
from pathlib import Path
from typing import Any, Optional

ROOT = Path(__file__).resolve().parent.parent  # effi-code install (toolkit)
CATALOG_MODELS = ROOT / "catalog" / "models.json"
CATALOG_ROUTING = ROOT / "catalog" / "task-routing.json"
CATALOG_MODES = ROOT / "catalog" / "modes.json"
CONFIG_DIR = Path(os.path.expanduser("~/.config/effi"))
DEFAULT_CONFIG = CONFIG_DIR / "config.json"
DEFAULT_ACCOUNTS = CONFIG_DIR / "accounts.json"
DEFAULT_STATE = CONFIG_DIR / "state.json"
VERSION_FILE = ROOT / "VERSION"


def toolkit_root() -> Path:
    """Install location of effi-code (catalog, templates, lib)."""
    return ROOT


def project_root(start: Optional[Path] = None) -> Path:
    """User project root for tasks/, CLAUDE.md.

    Order: EFFI_PROJECT → nearest git root from cwd → cwd.
    Never defaults to the toolkit install dir unless you are inside it.
    """
    env = os.environ.get("EFFI_PROJECT")
    if env:
        return Path(os.path.expanduser(env)).resolve()
    cur = (start or Path.cwd()).resolve()
    for p in [cur, *cur.parents]:
        if (p / ".git").exists():
            return p
    return cur


def tasks_dir(project: Optional[Path] = None) -> Path:
    return (project or project_root()) / "tasks"


def version() -> str:
    try:
        return VERSION_FILE.read_text(encoding="utf-8").strip()
    except OSError:
        return "0.0.0"


def _load_json(path: Path) -> Any:
    with open(path, encoding="utf-8") as f:
        return json.load(f)


def _save_json(path: Path, data: Any) -> None:
    path.parent.mkdir(parents=True, exist_ok=True)
    tmp = path.with_suffix(path.suffix + ".tmp")
    with open(tmp, "w", encoding="utf-8") as f:
        json.dump(data, f, indent=2, ensure_ascii=False)
        f.write("\n")
    tmp.replace(path)
    try:
        os.chmod(path, 0o600)
    except OSError:
        pass


def load_catalog() -> dict:
    return _load_json(CATALOG_MODELS)


def load_routing() -> dict:
    return _load_json(CATALOG_ROUTING)


def load_modes_catalog() -> dict:
    return _load_json(CATALOG_MODES)


# ── Orchestration modes (Apex / Cruise / Sip) ───────────────────────
# Resolution: EFFI_MODE → project .effi/mode → global state → default cruise

def _modes_map() -> dict:
    return load_modes_catalog().get("modes") or {}


def project_effi_dir(project: Optional[Path] = None) -> Path:
    return (project or project_root()) / ".effi"


def project_mode_path(project: Optional[Path] = None) -> Path:
    return project_effi_dir(project) / "mode"


NO_INHERIT = "none"  # a worktree's .effi/mode with this value opts out of inheriting


def _main_worktree_root(project: Path) -> Optional[Path]:
    """The main checkout when `project` is a linked git worktree, else None.

    Uses `git worktree list --porcelain` (first entry = main; skipped when
    bare) rather than guessing from the common dir's name — that guess broke
    for submodules and separate git dirs. Paths are taken line by line, so
    spaces survive. Works on git versions without --path-format."""
    def git(*a):
        return subprocess.run(["git", "-C", str(project), *a], capture_output=True,
                              text=True, timeout=5)
    try:
        dirs = git("rev-parse", "--git-dir", "--git-common-dir")
        if dirs.returncode != 0:
            return None
        gd, cd = (dirs.stdout.splitlines() + ["", ""])[:2]
        if not gd or not cd or (Path(project) / gd).resolve() == (Path(project) / cd).resolve():
            return None  # not a linked worktree
        wl = git("worktree", "list", "--porcelain")
    except (OSError, subprocess.SubprocessError):
        return None
    if wl.returncode != 0:
        return None
    first = wl.stdout.split("\n\n", 1)[0].splitlines()
    if not first or not first[0].startswith("worktree ") or "bare" in first[1:]:
        return None
    return Path(first[0][len("worktree "):])


def read_project_mode(project: Optional[Path] = None) -> Optional[str]:
    root = project or project_root()
    path = project_mode_path(root)
    if not path.is_file():
        # legacy single-file marker
        legacy = root / ".effi-mode"
        main = _main_worktree_root(root)
        if legacy.is_file():
            path = legacy
        elif main and project_mode_path(main).is_file():
            # .effi/mode is untracked, so a linked worktree (Orca worker,
            # `claude -w`) doesn't have it — inherit the main checkout's pin
            path = project_mode_path(main)
        else:
            return None
    try:
        raw = path.read_text(encoding="utf-8").strip().splitlines()
        if not raw:
            return None
        # allow "apex" or "mode: apex"
        line = raw[0].strip()
        if line.lower() == NO_INHERIT:
            return None
        if ":" in line and not line.startswith("apex") and line.split(":")[0].lower() in (
            "mode",
            "id",
        ):
            line = line.split(":", 1)[1].strip()
        return resolve_mode_id(line)
    except (OSError, KeyError, ValueError):
        return None


def write_project_mode(mode_id: str, project: Optional[Path] = None) -> Path:
    proj = project or project_root()
    d = project_effi_dir(proj)
    d.mkdir(parents=True, exist_ok=True)
    path = d / "mode"
    path.write_text(
        f"{mode_id}\n# set by effi mode — project-local\n# global: effi mode set {mode_id} --global\n",
        encoding="utf-8",
    )
    # gitignore helper so mode pin is optional to commit
    gi = d / ".gitignore"
    if not gi.exists():
        gi.write_text("# un-ignore 'mode' if the team should share the default\n# mode\n", encoding="utf-8")
    return path


def resolve_mode_id(token: str) -> str:
    """Map user input (name, number, alias) → mode id."""
    t = (token or "").strip().lower()
    if not t:
        raise ValueError("empty mode")
    modes = _modes_map()
    if t in modes:
        return t
    for mid, m in modes.items():
        if str(m.get("number")) == t:
            return mid
        name = (m.get("name") or "").lower()
        if t == name:
            return mid
        for a in m.get("aliases") or []:
            if t == str(a).lower():
                return mid
    raise KeyError(
        f"unknown mode: {token!r} — try apex|cruise|sip (or 1|2|3)"
    )


def mode_source() -> str:
    """Where the active mode comes from: env|project|global|default."""
    if os.environ.get("EFFI_MODE"):
        return "env"
    if read_project_mode():
        return "project"
    st = load_state()
    if st.get("mode"):
        return "global"
    return "default"


def get_mode(mode_id: Optional[str] = None) -> dict:
    """Return full mode dict.

    Order: explicit mode_id → EFFI_MODE → project .effi/mode → global state → default.
    """
    modes_cat = load_modes_catalog()
    modes = modes_cat.get("modes") or {}
    mid = mode_id
    source = "explicit" if mid else None
    if not mid:
        env = os.environ.get("EFFI_MODE")
        if env:
            mid = env
            source = "env"
    if not mid:
        pm = read_project_mode()
        if pm:
            mid = pm
            source = "project"
    if not mid:
        st = load_state()
        mid = st.get("mode") or None
        if mid:
            source = "global"
    if not mid:
        mid = modes_cat.get("default") or "cruise"
        source = "default"
    if mid not in modes:
        try:
            mid = resolve_mode_id(str(mid))
        except KeyError:
            mid = "cruise"
            source = "default"
    m = dict(modes[mid])
    m["id"] = mid
    m["source"] = source or mode_source()
    return m


def set_mode(token: str, scope: str = "project") -> dict:
    """Pin mode. scope: project (default) | global | both | env (print only)."""
    mid = resolve_mode_id(token)
    scope = (scope or "project").lower()
    prev = get_mode().get("id")
    now = datetime.now().isoformat(timespec="minutes")
    paths = []

    if scope in ("project", "both", "local"):
        p = write_project_mode(mid)
        paths.append(str(p))

    if scope in ("global", "both", "user"):
        st = load_state()
        st["mode"] = mid
        st["mode_set_at"] = now
        st.setdefault("mode_history", []).append(
            {"at": now, "from": prev, "to": mid, "scope": "global"}
        )
        st["mode_history"] = st["mode_history"][-30:]
        save_state(st)
        cfg = load_config()
        cfg["mode"] = mid
        _save_json(DEFAULT_CONFIG, cfg)
        paths.append(str(DEFAULT_STATE))

    if scope == "env":
        # caller exports; we only return
        pass

    m = get_mode(mid)
    m["saved_to"] = paths
    m["scope"] = scope
    m["previous"] = prev
    return m


def mode_is_set() -> bool:
    """True if user (or project/env) pinned a mode — not bare default."""
    return mode_source() != "default"


def project_mode_is_set(project: Optional[Path] = None) -> bool:
    """True when this project has a local pin (.effi/mode)."""
    return read_project_mode(project) is not None


def clear_mode(scope: str = "project") -> dict:
    """Remove mode pin(s). scope: project | global | both."""
    scope = (scope or "project").lower()
    removed: list[str] = []
    proj = project_root()

    if scope in ("project", "both", "local"):
        p = project_mode_path(proj)
        if p.is_file():
            p.unlink()
            removed.append(str(p))
        main = _main_worktree_root(proj)
        if main and project_mode_path(main).is_file():
            # a linked worktree would silently re-inherit the main checkout's
            # pin; clearing here means "not pinned in this worktree"
            p.parent.mkdir(parents=True, exist_ok=True)
            p.write_text(NO_INHERIT + "\n", encoding="utf-8")
            removed.append(f"{p} (inherit from {main} turned off)")
        legacy = proj / ".effi-mode"
        if legacy.is_file():
            legacy.unlink()
            removed.append(str(legacy))

    if scope in ("global", "both", "user"):
        st = load_state()
        if st.get("mode") is not None:
            st.pop("mode", None)
            st.pop("mode_set_at", None)
            save_state(st)
            removed.append(str(DEFAULT_STATE))
        try:
            cfg = load_config()
            if "mode" in cfg:
                cfg.pop("mode", None)
                _save_json(DEFAULT_CONFIG, cfg)
                removed.append(str(DEFAULT_CONFIG))
        except Exception:
            pass

    m = get_mode()
    m["cleared"] = removed
    m["scope"] = scope
    return m


def list_modes() -> list[dict]:
    modes = _modes_map()
    cur = get_mode().get("id")
    out = []
    for mid in ("apex", "cruise", "sip"):
        if mid not in modes:
            continue
        m = dict(modes[mid])
        m["id"] = mid
        m["active"] = mid == cur
        out.append(m)
    return out


def print_mode_menu(stream=None, context: Optional[str] = None) -> None:
    stream = stream or sys.stderr
    cur = get_mode()
    stream.write("\n effi-code · pick orchestration mode\n")
    if context:
        stream.write(f"  context: {context}\n")
    stream.write(
        f"  current: {cur.get('emoji','')} {cur.get('name')} "
        f"({cur.get('id')}) · source={cur.get('source')}\n"
    )
    stream.write(" ─────────────────────────────────────\n")
    for m in list_modes():
        mark = "→" if m["id"] == cur.get("id") else " "
        stream.write(
            f"  {mark} [{m['number']}] {m.get('emoji','')} {m['name']:7}  — {m.get('tagline')}\n"
        )
    stream.write(" ─────────────────────────────────────\n")
    stream.write("  1/2/3 or apex/cruise/sip   Enter=keep current / Cruise\n")
    stream.write("  scope: project default · add --global when setting via CLI\n\n")


def ensure_mode(interactive: bool = True, scope: str = "project") -> dict:
    """Return active mode; on TTY ask once per project when no project pin.

    Skip ask only when:
      - EFFI_MODE is set, or
      - this project already has `.effi/mode`

    Global `~/.config/effi` mode is a soft default (Enter keeps it) — it must
    NOT silence the per-project prompt. Otherwise first `effi` after any
    global pin never asks, which feels broken.
    """
    if os.environ.get("EFFI_MODE"):
        return get_mode()
    if project_mode_is_set():
        return get_mode()

    if interactive and sys.stdin.isatty() and sys.stderr.isatty():
        cur = get_mode()  # may resolve from global → used as Enter default
        print_mode_menu(
            context=(
                f"project={project_root()} · "
                f"Enter keeps {cur.get('emoji', '')} {cur.get('name')} ({cur.get('id')})"
            )
        )
        try:
            sys.stderr.write("mode> ")
            sys.stderr.flush()
            line = sys.stdin.readline()
        except EOFError:
            line = ""
        choice = (line or "").strip() or cur.get("id") or "cruise"
        try:
            return set_mode(choice, scope=scope)
        except KeyError as e:
            fallback = cur.get("id") or "cruise"
            sys.stderr.write(f"  ! {e} — keeping {fallback}\n")
            return set_mode(fallback, scope=scope)

    # Non-TTY: honor global / default without pinning
    return get_mode()


# Importance bands for task → suggested mode
_HIGH_DOMAINS = {
    "security",
    "architecture",
    "plan",
    "implement_hard",
    "orchestrate",
}
_LOW_DOMAINS = {"bulk", "docs"}


def assess_task_importance(text: str) -> dict:
    """Classify task importance and recommend a mode."""
    cls = classify_domain(text)
    domain = cls.get("domain") or "implement"
    grade = cls.get("grade") or "M"
    # keyword boosts
    t = (text or "").lower()
    critical = bool(
        re.search(
            r"production|프로덕션|긴급|critical|outage|장애|보안|security|launch|출시",
            t,
        )
    )
    trivial = bool(
        re.search(
            r"번역|translate|docstring|typo|주석|rename|포맷|format only|간단",
            t,
        )
    )

    if critical or domain in _HIGH_DOMAINS or grade in ("L", "XL"):
        band = "high"
        suggested = "apex"
        reason = f"high stakes · domain={domain} grade={grade}"
    elif trivial or domain in _LOW_DOMAINS or grade == "S":
        band = "low"
        suggested = "sip"
        reason = f"low stakes / mechanical · domain={domain} grade={grade}"
    else:
        band = "medium"
        suggested = "cruise"
        reason = f"normal feature work · domain={domain} grade={grade}"

    if critical and band != "high":
        band, suggested, reason = "high", "apex", "critical keywords"

    return {
        "band": band,
        "suggested_mode": suggested,
        "reason": reason,
        "domain": domain,
        "grade": grade,
        "label": cls.get("label"),
        "confidence": cls.get("confidence"),
    }


def mode_fit(current_id: str, suggested_id: str, band: str) -> dict:
    """Whether current mode is OK for this importance band."""
    # ranking: sip=0, cruise=1, apex=2
    rank = {"sip": 0, "cruise": 1, "apex": 2}
    cur_r = rank.get(current_id, 1)
    sug_r = rank.get(suggested_id, 1)
    # underrun: mode too weak for task
    if cur_r < sug_r:
        return {
            "ok": False,
            "mismatch": "underpowered",
            "message": f"task is {band} importance but mode is {current_id} (suggest {suggested_id})",
        }
    # overrun: apex on trivial bulk — optional thrift prompt
    if band == "low" and current_id == "apex":
        return {
            "ok": False,
            "mismatch": "overpowered",
            "message": f"task is low stakes but mode is Apex (suggest Sip to save cost)",
        }
    return {"ok": True, "mismatch": None, "message": "mode fits task"}


def maybe_adjust_mode_for_task(
    text: str,
    interactive: bool = True,
    auto_apply: bool = False,
    scope: str = "project",
) -> dict:
    """Assess task importance; if mode mismatches, ask (or auto) to switch.

    Returns {importance, current, suggested, changed, mode, fit}.
    """
    imp = assess_task_importance(text)
    current = get_mode()
    suggested_id = imp["suggested_mode"]
    fit = mode_fit(current["id"], suggested_id, imp["band"])
    result = {
        "importance": imp,
        "current": current,
        "suggested": get_mode(suggested_id),
        "fit": fit,
        "changed": False,
        "mode": current,
        "skipped": False,
    }

    if fit.get("ok"):
        return result

    # mismatch
    if not interactive or not (sys.stdin.isatty() and sys.stderr.isatty()):
        if auto_apply:
            m = set_mode(suggested_id, scope=scope)
            result["changed"] = True
            result["mode"] = m
        else:
            result["skipped"] = True
        return result

    # interactive prompt
    sug = result["suggested"]
    sys.stderr.write("\n")
    sys.stderr.write(" ⚡ mode check for this task\n")
    sys.stderr.write(f"    task: {(text or '')[:80]}\n")
    sys.stderr.write(
        f"    importance: {imp['band'].upper()} · {imp['reason']}\n"
    )
    sys.stderr.write(
        f"    current:  {current.get('emoji')} {current.get('name')} ({current['id']}) "
        f"[{current.get('source')}]\n"
    )
    sys.stderr.write(
        f"    suggested:{sug.get('emoji')} {sug.get('name')} ({sug['id']})\n"
    )
    sys.stderr.write(f"    why: {fit.get('message')}\n")
    if fit.get("mismatch") == "underpowered":
        sys.stderr.write(
            "    → Switch for better quality? [Y=switch / n=keep / 1|2|3=pick] "
        )
    else:
        sys.stderr.write(
            "    → Switch to save cost? [Y=switch / n=keep / 1|2|3=pick] "
        )
    sys.stderr.flush()
    try:
        line = sys.stdin.readline()
    except EOFError:
        line = ""
    ans = (line or "").strip().lower()

    if ans in ("", "y", "yes", "ㅛ"):
        m = set_mode(suggested_id, scope=scope)
        result["changed"] = True
        result["mode"] = m
        sys.stderr.write(
            f"  ✓ mode → {m.get('emoji')} {m['name']} (project pin)\n\n"
        )
    elif ans in ("n", "no", "keep", "ㅜ"):
        result["skipped"] = True
        sys.stderr.write("  · keeping current mode\n\n")
    else:
        try:
            m = set_mode(ans, scope=scope)
            result["changed"] = True
            result["mode"] = m
            sys.stderr.write(
                f"  ✓ mode → {m.get('emoji')} {m['name']}\n\n"
            )
        except (KeyError, ValueError) as e:
            sys.stderr.write(f"  ! {e} — keeping current\n\n")
            result["skipped"] = True
    return result


def apply_mode_policy(rec: dict, mode: Optional[dict] = None, cfg: Optional[dict] = None) -> dict:
    """Adjust a base recommendation according to Apex/Cruise/Sip policy."""
    mode = mode or get_mode()
    cfg = cfg or load_config()
    mid = mode.get("id") or "cruise"
    pol = mode.get("policy") or {}
    domain = rec.get("domain") or "implement"
    grade = rec.get("grade") or "M"
    why_extra = []

    if mid == "apex":
        # Never local as primary
        if rec.get("primary_provider") == "local" or not pol.get("allow_local_primary", True):
            if domain in ("bulk", "docs"):
                rec["primary_provider"] = pol.get("bulk_cloud_provider", "claude")
                rec["primary_model"] = pol.get("bulk_cloud_model", "claude-sonnet-5")
                why_extra.append("Apex: cloud over local for bulk")
            else:
                rec["primary_provider"] = pol.get("default_coding_provider", "claude")
                rec["primary_model"] = pol.get("default_coding_model", "claude-opus-4-8")
                why_extra.append("Apex: top coding model")
        if pol.get("prefer_top_for_coding") and domain in (
            "implement",
            "implement_hard",
            "debug",
            "refactor",
            "test",
            "deploy",
            "orchestrate",
            "plan",
            "architecture",
            "security",
            "review",
        ):
            if domain in ("architecture", "plan", "security", "implement_hard", "orchestrate"):
                rec["primary_provider"] = "claude"
                rec["primary_model"] = pol.get("architecture_model", "claude-opus-4-8")
            elif domain != "design":  # design may stay gemini
                if rec.get("primary_provider") in ("claude", "openai", "grok", "local"):
                    rec["primary_provider"] = pol.get("default_coding_provider", "claude")
                    rec["primary_model"] = pol.get("default_coding_model", "claude-opus-4-8")
            why_extra.append("Apex: performance-first routing")
        # floor review
        min_rev = pol.get("min_review") or "clean_context"
        if rec.get("review") == "none" or (
            min_rev == "clean_context"
            and rec.get("review") == "none"
        ):
            rec["review"] = "clean_context"
        if grade in ("L", "XL") or domain in ("security", "architecture"):
            rec["review"] = "clean_context+integration"
        rec["start_tier"] = "top"
        # demote local to non-suggestion in apex (still show if present but mark avoided)
        if rec.get("local"):
            rec["local"]["suggestion_only"] = True
            rec["local"]["apex_discouraged"] = True
        rec["estimated_relative_cost"] = "high"
        rec["cascade"] = pol.get("cascade", "top_first")

    elif mid == "sip":
        prefer_domains = set(pol.get("prefer_local_for_domains") or [])
        prefer_grades = set(pol.get("prefer_local_for_grades") or [])
        force_local = (
            domain in prefer_domains
            or grade in prefer_grades
            or (pol.get("force_local_bulk") and domain == "bulk")
        )
        # security still needs real cloud judgment
        if domain == "security":
            rec["primary_provider"] = "claude"
            rec["primary_model"] = "claude-sonnet-5"
            rec["start_tier"] = "mid"
            why_extra.append("Sip: security floor = sonnet (not local)")
        elif domain in ("architecture", "plan") and grade in ("L", "XL"):
            rec["primary_provider"] = pol.get("coding_ceiling_provider", "claude")
            rec["primary_model"] = pol.get("coding_ceiling_model", "claude-sonnet-5")
            rec["start_tier"] = "mid"
            why_extra.append("Sip: hard design stays mid ceiling, not opus")
        elif force_local and pol.get("allow_local_primary", True):
            roles = ["boilerplate", "translate", "docstring", "format"]
            if domain in ("test", "refactor", "implement"):
                roles = ["implement_narrow", "scaffold", "boilerplate"]
            loc = pick_local(roles, cfg)
            rec["primary_provider"] = "local"
            rec["primary_model"] = loc["model"]
            rec["local"] = loc
            rec["start_tier"] = "local"
            rec["estimated_relative_cost"] = "free"
            why_extra.append("Sip: local-first cost save")
        else:
            # cheap cloud ceiling
            if rec.get("primary_provider") == "claude" and "opus" in (
                rec.get("primary_model") or ""
            ):
                rec["primary_model"] = pol.get(
                    "coding_ceiling_model", "claude-sonnet-5"
                )
                why_extra.append("Sip: cap at sonnet")
            if grade == "S" and domain not in ("security",):
                rec["primary_provider"] = pol.get("cheap_cloud_provider", "claude")
                rec["primary_model"] = pol.get("cheap_cloud_model", "claude-haiku-4-5")
                rec["start_tier"] = "cheap"
                why_extra.append("Sip: haiku for simple work")
            elif rec.get("start_tier") == "top":
                rec["start_tier"] = "mid"
        rec["cascade"] = pol.get("cascade", "local_first")
        # lighter review for S
        if grade == "S" and domain not in ("security",):
            rec["review"] = "none"

    else:
        # cruise — base matrix already applied
        rec["cascade"] = pol.get("cascade", "cheap_first")
        why_extra.append("Cruise: balanced matrix default")

    # annotate
    rec["mode"] = mid
    rec["mode_name"] = mode.get("name")
    rec["mode_emoji"] = mode.get("emoji")
    if why_extra:
        base_why = rec.get("why") or ""
        rec["why"] = (base_why + " · " if base_why else "") + "; ".join(why_extra)

    # recompute cost label if provider/model changed
    try:
        catalog = load_catalog()
        tier = _tier_of(rec["primary_provider"], rec["primary_model"], catalog)
        if rec["primary_provider"] == "local":
            rec["estimated_relative_cost"] = "free"
            rec["start_tier"] = rec.get("start_tier") or "local"
        else:
            rec["estimated_relative_cost"] = _relative_cost(tier)
            if mid == "apex":
                rec["start_tier"] = "top"
            elif mid != "sip":
                rec["start_tier"] = {
                    "cheap": "cheap",
                    "mid": "mid",
                    "top": "top",
                    "ultra": "top",
                }.get(tier, rec.get("start_tier") or "mid")
    except Exception:
        pass

    return rec


def load_config() -> dict:
    cfg = {
        "switch_threshold_percent": 80,
        "prefer_providers": ["claude", "openai", "gemini", "grok", "local"],
        "main_thread_provider": "claude",
        "main_thread_model": "claude-sonnet-5",
        "escalate_model": "claude-opus-4-8",
        "catalog_auto_remind_days": 14,
        "local": {
            "enabled": True,
            "ollama_url": os.environ.get("OLLAMA_URL", "http://localhost:11434"),
            "ram_margin_gb": 2.0,
            "ram_cap_ratio": 0.6,
        },
    }
    if DEFAULT_CONFIG.exists():
        user = _load_json(DEFAULT_CONFIG)
        cfg.update({k: v for k, v in user.items() if k != "local"})
        if "local" in user:
            cfg["local"].update(user["local"])
    # accounts file may override threshold
    if DEFAULT_ACCOUNTS.exists():
        acc = _load_json(DEFAULT_ACCOUNTS)
        if "switch_threshold_percent" in acc:
            cfg["switch_threshold_percent"] = acc["switch_threshold_percent"]
    return cfg


# ── Memory / local pick ──────────────────────────────────────────────

def memory_stats() -> dict:
    """Best-effort free/total RAM. macOS via sysctl/vm_stat; Linux via /proc."""
    # macOS
    try:
        total = int(subprocess.check_output(["sysctl", "-n", "hw.memsize"], stderr=subprocess.DEVNULL)) / 1024**3
        vm = subprocess.check_output(["vm_stat"], stderr=subprocess.DEVNULL).decode()
        m = re.search(r"page size of (\d+) bytes", vm)
        page = int(m.group(1)) if m else 16384

        def pg(name: str) -> int:
            mm = re.search(re.escape(name) + r":\s+(\d+)", vm)
            return int(mm.group(1)) if mm else 0

        avail = (
            pg("Pages free")
            + pg("Pages inactive")
            + pg("Pages speculative")
            + pg("Pages purgeable")
        ) * page / 1024**3
        return {"total_gb": total, "avail_gb": avail}
    except (FileNotFoundError, subprocess.CalledProcessError, OSError):
        pass

    # Linux
    try:
        meminfo = Path("/proc/meminfo").read_text(encoding="utf-8")
        def kB(key: str) -> float:
            mm = re.search(rf"^{key}:\s+(\d+)", meminfo, re.M)
            return (int(mm.group(1)) / 1024 / 1024) if mm else 0.0
        total = kB("MemTotal")
        avail = kB("MemAvailable") or (kB("MemFree") + kB("Buffers") + kB("Cached"))
        if total > 0:
            return {"total_gb": total, "avail_gb": avail}
    except OSError:
        pass

    # unknown platform — conservative tiny budget
    return {"total_gb": 8.0, "avail_gb": 2.0}


def ollama_loaded_gb(url: str) -> float:
    try:
        with urllib.request.urlopen(url.rstrip("/") + "/api/ps", timeout=3) as r:
            data = json.load(r)
        return sum(m.get("size", 0) for m in data.get("models") or []) / 1024**3
    except Exception:
        return 0.0


def local_budget(cfg: Optional[dict] = None) -> float:
    cfg = cfg or load_config()
    loc = cfg["local"]
    mem = memory_stats()
    loaded = ollama_loaded_gb(loc["ollama_url"])
    budget = mem["avail_gb"] + loaded - float(loc["ram_margin_gb"])
    cap = mem["total_gb"] * float(loc["ram_cap_ratio"])
    return max(0.0, min(budget, cap))


def pick_local(
    roles: Optional[list[str]] = None,
    cfg: Optional[dict] = None,
) -> dict:
    """Pick best local model for roles that fits RAM budget."""
    cfg = cfg or load_config()
    cat = load_catalog()
    models = cat["providers"]["local"]["models"]
    budget = local_budget(cfg)
    mem = memory_stats()
    roles = roles or []

    candidates = []
    for name, meta in models.items():
        ram = float(meta.get("ram_gb") or 99)
        if ram > budget:
            continue
        score = 0
        # Prefer stronger tiers when they fit
        tier = meta.get("tier", "")
        score += {"local_strong": 40, "local_mid": 25, "local_fast": 10, "local_micro": 1}.get(
            tier, 5
        )
        mroles = set(meta.get("role") or [])
        for r in roles:
            if r in mroles:
                score += 15
        # Prefer smaller when roles are mechanical only
        if roles and set(roles).issubset({"boilerplate", "translate", "docstring", "format", "tiny_transform"}):
            score += max(0, 20 - ram)  # bias smaller/faster
        candidates.append((score, -ram, name, meta, ram))

    if not candidates:
        # absolute fallback
        name = "qwen2.5-coder:1.5b"
        meta = models.get(name, {"ram_gb": 1.8})
        return {
            "model": name,
            "ram_gb": meta.get("ram_gb"),
            "budget_gb": round(budget, 2),
            "total_gb": round(mem["total_gb"], 1),
            "avail_gb": round(mem["avail_gb"], 1),
            "fit": False,
            "reason": "no model fits budget; using micro fallback",
        }

    candidates.sort(reverse=True)
    score, _, name, meta, ram = candidates[0]
    return {
        "model": name,
        "ram_gb": ram,
        "budget_gb": round(budget, 2),
        "total_gb": round(mem["total_gb"], 1),
        "avail_gb": round(mem["avail_gb"], 1),
        "fit": True,
        "tier": meta.get("tier"),
        "roles": meta.get("role"),
        "reason": f"score={score} roles={roles or 'any'}",
    }


def local_driver_check(pick: dict, cat: Optional[dict] = None) -> dict:
    """Can this local pick DRIVE Claude Code interactively (agent loop + tool
    calls), or is it only a one-shot mechanical worker?

    Small models can't follow Claude Code's large system prompt + tool schema —
    they emit raw tool-call JSON as plain text (the classic "why is it printing
    {\"name\":...}" failure). The catalog marks genuine drivers with the
    `agent_local` role (local_strong tier); everything else is worker-only.
    Returns {ok, model, tier, reason}.
    """
    name = pick.get("model", "")
    cat = cat or load_catalog()
    meta = (cat.get("providers", {}).get("local", {}) or {}).get("models", {}).get(name) or {}
    tier = pick.get("tier") or meta.get("tier")
    roles = set(pick.get("roles") or meta.get("role") or [])
    leak = " — Claude Code 대화형 툴콜을 못 하고 응답이 날 JSON으로 새어나옴"

    # A genuine agent driver (agent_local role) is viable regardless of RAM: if
    # the user pinned it ollama will still load it (slowly) and tool-calls work.
    # Checked first so a pinned large agent model is never RAM-warned.
    if "agent_local" in roles:
        return {"ok": True, "model": name, "tier": tier,
                "reason": "agent_local — Claude Code 대화형 구동 가능"}

    # Not a driver — say why as specifically as the data allows. `fit` isn't
    # passed on the real CLI path (only a bare model name is), so reconstruct
    # the RAM-fallback situation from the catalog RAM vs the live budget.
    if pick.get("fit") is False:
        why = "RAM 예산 초과 → 마이크로 폴백"
    elif not meta:
        return {"ok": False, "model": name, "tier": tier,
                "reason": "카탈로그 미등록 모델 — 에이전트 구동 가능 여부 불명"
                          f"{leak}. 문제 시 큰 로컬(agent_local) 또는 클라우드(effi) 사용"}
    else:
        ram = meta.get("ram_gb")
        if ram is not None and float(ram) > local_budget():
            why = "RAM 예산 초과 → 마이크로 폴백"
        else:
            why = f"{tier or '워커'} 모델(agent_local 아님) — 기계적 워커 전용"
    return {"ok": False, "model": name, "tier": tier, "reason": why + leak}


# ── Domain classification ────────────────────────────────────────────

DOMAIN_ORDER = [
    "security",
    "architecture",
    "plan",
    "deploy",
    "research",
    "design",
    "implement_hard",
    "debug",
    "review",
    "test",
    "refactor",
    "docs",
    "bulk",
    "implement",
    "orchestrate",
]


def classify_domain(text: str) -> dict:
    routing = load_routing()
    domains = routing["domains"]
    t = text.lower()
    scores: dict[str, int] = {}
    for dname, d in domains.items():
        s = 0
        for kw in d.get("keywords") or []:
            if kw.lower() in t:
                s += 2 if len(kw) > 4 else 1
        scores[dname] = s

    # heuristic boosts
    if re.search(r"보안|security|owasp|xss|injection", t):
        scores["security"] = scores.get("security", 0) + 5
    if re.search(r"배포|deploy|terraform|k8s|ci/cd", t):
        scores["deploy"] = scores.get("deploy", 0) + 3
    if re.search(r"번역|docstring|boilerplate|대량|i18n", t):
        scores["bulk"] = scores.get("bulk", 0) + 4
    if re.search(r"아키텍처|architect|trade-?off", t):
        scores["architecture"] = scores.get("architecture", 0) + 4
    if re.search(r"버그|debug|stacktrace|regression", t):
        scores["debug"] = scores.get("debug", 0) + 3
    # Building something (+ optional tests) is implement, not pure test work
    building = bool(re.search(
        r"implement|구현|middleware|미들웨어|feature|endpoint|엔드포인트|handler|module|모듈|api\b",
        t,
    ))
    if building and scores.get("test", 0) > 0:
        scores["implement"] = scores.get("implement", 0) + 5
        scores["test"] = max(0, scores.get("test", 0) - 2)
    # pure test authoring
    if re.search(r"tests? only|only tests?|테스트만|unit tests? only|write (unit )?tests?\b", t) and not building:
        scores["test"] = scores.get("test", 0) + 4

    best = max(DOMAIN_ORDER, key=lambda d: (scores.get(d, 0), -DOMAIN_ORDER.index(d)))
    if scores.get(best, 0) <= 0:
        # default implement
        best = "implement"
        conf = "low"
    elif scores[best] >= 4:
        conf = "high"
    else:
        conf = "medium"

    d = domains[best]
    grade = d.get("grade_default", "M")
    return {
        "domain": best,
        "label": d.get("label"),
        "grade": grade,
        "confidence": conf,
        "scores": {k: v for k, v in scores.items() if v > 0},
        "domain_spec": d,
    }


# ── Routing recommendation ───────────────────────────────────────────

@dataclass
class RouteRec:
    domain: str
    grade: str
    confidence: str
    primary_provider: str
    primary_model: str
    why: str
    alternates: list
    local: Optional[dict]
    start_tier: str
    review: str
    verify: str
    token_tips: list
    main_thread_lock: str
    estimated_relative_cost: str  # low|mid|high|ultra
    catalog_version: str
    catalog_stale: bool


def _tier_of(provider: str, model: str, catalog: dict) -> str:
    try:
        return catalog["providers"][provider]["models"][model].get("tier", "mid")
    except KeyError:
        return "mid"


def _relative_cost(tier: str) -> str:
    return {
        "local_micro": "free",
        "local_fast": "free",
        "local_mid": "free",
        "local_strong": "free",
        "cheap": "low",
        "mid": "mid",
        "top": "high",
        "ultra": "ultra",
        "special": "mid",
    }.get(tier, "mid")


def recommend(
    text: str,
    prefer_local: bool = False,
    cfg: Optional[dict] = None,
    mode: Optional[str] = None,
    adjust_mode: bool = False,
    interactive: bool = True,
) -> dict:
    cfg = cfg or load_config()
    catalog = load_catalog()
    routing = load_routing()
    cls = classify_domain(text)
    d = cls["domain_spec"]
    mode_adjust = None
    if adjust_mode and not mode:
        mode_adjust = maybe_adjust_mode_for_task(
            text, interactive=interactive, scope="project"
        )
    mode_obj = get_mode(mode) if mode else get_mode()

    primary = dict(d.get("primary") or {})
    if prefer_local or primary.get("provider") == "local":
        roles = d.get("local_roles") or ["implement_narrow"]
        loc = pick_local(roles, cfg)
        primary = {
            "provider": "local",
            "model": loc["model"],
            "why": primary.get("why") or "local preferred / bulk",
        }
        local_info = loc
    else:
        local_info = None
        if d.get("local_roles") and cfg["local"]["enabled"]:
            # always compute a local option for mechanical offload suggestion
            local_info = pick_local(d.get("local_roles"), cfg)
            local_info["suggestion_only"] = True

    # resolve AUTO
    if primary.get("model") == "AUTO":
        loc = pick_local(d.get("local_roles") or ["boilerplate"], cfg)
        primary["model"] = loc["model"]
        local_info = loc

    alts = list(d.get("alternates") or [])
    # filter by prefer_providers order for display
    prefer = cfg.get("prefer_providers") or []
    alts_sorted = sorted(
        alts,
        key=lambda a: prefer.index(a["provider"]) if a.get("provider") in prefer else 99,
    )

    tier = _tier_of(primary["provider"], primary["model"], catalog)
    if primary["provider"] == "local":
        start_tier = "local"
    else:
        start_tier = {"cheap": "cheap", "mid": "mid", "top": "top", "ultra": "top"}.get(
            tier, "mid"
        )

    grade = cls["grade"]
    if grade == "S":
        review = "none"
    elif grade == "M":
        review = "clean_context"
    else:
        review = "clean_context+integration"

    stale = catalog_is_stale(catalog, cfg)

    rec = RouteRec(
        domain=cls["domain"],
        grade=grade,
        confidence=cls["confidence"],
        primary_provider=primary["provider"],
        primary_model=primary["model"],
        why=primary.get("why") or "",
        alternates=alts_sorted,
        local=local_info,
        start_tier=start_tier,
        review=review,
        verify=d.get("verify") or "tests",
        token_tips=d.get("token_tips") or routing.get("philosophy", [])[:2],
        main_thread_lock=routing.get("cost_guards", {}).get(
            "main_thread_provider_lock", "claude"
        ),
        estimated_relative_cost=_relative_cost(tier),
        catalog_version=catalog.get("catalog_version", "?"),
        catalog_stale=stale,
    )
    out = asdict(rec)
    out["label"] = cls.get("label")
    out["scores"] = cls.get("scores")
    out["escalate"] = d.get("escalate")
    out["parallel"] = d.get("parallel")
    out["approval"] = d.get("approval")
    out["rules"] = d.get("rules") or []
    # Mode policy last (Apex / Cruise / Sip)
    out = apply_mode_policy(out, mode=mode_obj, cfg=cfg)
    out["mode_source"] = mode_obj.get("source")
    if mode_adjust:
        out["mode_adjust"] = {
            "changed": mode_adjust.get("changed"),
            "importance": mode_adjust.get("importance"),
            "fit": mode_adjust.get("fit"),
            "skipped": mode_adjust.get("skipped"),
        }
    else:
        imp = assess_task_importance(text)
        out["importance"] = imp
    return out


def format_route(rec: dict, compact: bool = False) -> str:
    lines = []
    mode_bit = ""
    if rec.get("mode"):
        src = rec.get("mode_source") or ""
        mode_bit = (
            f"  mode={rec.get('mode_emoji','')}{rec.get('mode_name') or rec['mode']}"
            + (f"@{src}" if src else "")
        )
    imp = rec.get("importance") or (rec.get("mode_adjust") or {}).get("importance")
    if not compact:
        lines.append(
            f"# ROUTING  domain={rec['domain']} ({rec.get('label')})  "
            f"grade={rec['grade']}  conf={rec['confidence']}{mode_bit}"
        )
        if imp:
            lines.append(
                f"importance: {imp.get('band')} → suggest {imp.get('suggested_mode')} "
                f"({imp.get('reason')})"
            )
    lines.append(
        f"primary: {rec['primary_provider']}/{rec['primary_model']}  "
        f"cost≈{rec['estimated_relative_cost']}  tier={rec['start_tier']}{mode_bit if compact else ''}"
    )
    if rec.get("why"):
        lines.append(f"why: {rec['why']}")
    if rec.get("alternates"):
        alt = ", ".join(
            f"{a['provider']}/{a['model']}" for a in rec["alternates"][:3]
        )
        lines.append(f"alternates: {alt}")
    if rec.get("local"):
        loc = rec["local"]
        tag = " (suggestion)" if loc.get("suggestion_only") else ""
        lines.append(
            f"local{tag}: {loc['model']}  "
            f"budget={loc.get('budget_gb')}GB / avail={loc.get('avail_gb')}GB"
        )
    lines.append(f"review: {rec['review']}  verify: {rec['verify']}")
    lines.append(f"main_thread_lock: {rec['main_thread_lock']}")
    if rec.get("catalog_stale"):
        lines.append(
            f"⚠️ catalog stale (v{rec['catalog_version']}) — run: effi catalog update"
        )
    if compact:
        return (
            f"domain={rec['domain']} grade={rec['grade']} "
            f"model={rec['primary_provider']}/{rec['primary_model']} "
            f"cost={rec['estimated_relative_cost']} review={rec['review']}"
        )
    return "\n".join(lines)


# ── Catalog freshness ────────────────────────────────────────────────

def catalog_is_stale(catalog: Optional[dict] = None, cfg: Optional[dict] = None) -> bool:
    catalog = catalog or load_catalog()
    cfg = cfg or load_config()
    days = int(cfg.get("catalog_auto_remind_days") or 14)
    due = catalog.get("next_review_due")
    if due:
        try:
            return date.today() > date.fromisoformat(due)
        except ValueError:
            pass
    updated = catalog.get("updated_at")
    if updated:
        try:
            return date.today() > date.fromisoformat(updated) + timedelta(days=days)
        except ValueError:
            pass
    return True


def catalog_status() -> dict:
    cat = load_catalog()
    cfg = load_config()
    return {
        "catalog_version": cat.get("catalog_version"),
        "updated_at": cat.get("updated_at"),
        "last_verified_at": cat.get("last_verified_at") or cat.get("updated_at"),
        "next_review_due": cat.get("next_review_due"),
        "stale": catalog_is_stale(cat, cfg),
        "sources": cat.get("sources"),
        "path": str(CATALOG_MODELS),
    }


def bump_catalog_dates() -> dict:
    """Mark catalog as reviewed today; next review +14d. Does not invent new models."""
    cat = load_catalog()
    today = date.today()
    today_s = today.isoformat()
    cat["updated_at"] = today_s
    cat["last_verified_at"] = today_s
    cat["next_review_due"] = (today + timedelta(days=14)).isoformat()
    # patch version date stamp
    cat["catalog_version"] = today.strftime("%Y.%m.%d")
    _save_json(CATALOG_MODELS, cat)
    # keep routing timestamp in sync
    routing = load_routing()
    routing["updated_at"] = today_s
    routing["last_verified_at"] = today_s
    _save_json(CATALOG_ROUTING, routing)
    return catalog_status()


# ── Account rotation ─────────────────────────────────────────────────

def load_accounts() -> dict:
    if not DEFAULT_ACCOUNTS.exists():
        return {
            "schema_version": 1,
            "switch_threshold_percent": load_config().get("switch_threshold_percent", 80),
            "rotation_policy": "next_available",
            "accounts": [],
        }
    return _load_json(DEFAULT_ACCOUNTS)


def save_accounts(data: dict) -> None:
    _save_json(DEFAULT_ACCOUNTS, data)


def load_state() -> dict:
    if DEFAULT_STATE.exists():
        return _load_json(DEFAULT_STATE)
    return {"active_account_id": None, "history": [], "mode": None}


def save_state(state: dict) -> None:
    _save_json(DEFAULT_STATE, state)


def list_accounts() -> list[dict]:
    data = load_accounts()
    return data.get("accounts") or []


def get_threshold() -> float:
    data = load_accounts()
    return float(
        data.get("switch_threshold_percent")
        or load_config().get("switch_threshold_percent")
        or 80
    )


def set_threshold(percent: float) -> float:
    if not 1 <= percent <= 100:
        raise ValueError("threshold must be 1..100")
    data = load_accounts()
    data["switch_threshold_percent"] = percent
    save_accounts(data)
    cfg_path = DEFAULT_CONFIG
    cfg = load_config()
    cfg["switch_threshold_percent"] = percent
    _save_json(cfg_path, cfg)
    return percent


def set_meter(account_id: str, percent: float) -> dict:
    if not 0 <= percent <= 100:
        raise ValueError("usage_percent must be 0..100")
    data = load_accounts()
    found = None
    for a in data.get("accounts") or []:
        if a["id"] == account_id:
            a["usage_percent"] = percent
            a["metered_at"] = datetime.now().isoformat(timespec="minutes")
            found = a
            break
    if not found:
        raise KeyError(f"unknown account: {account_id}")
    save_accounts(data)
    return found


def select_account(force_id: Optional[str] = None) -> dict:
    """Pick active Claude account under usage threshold; rotate if needed.

    Apex mode ignores usage threshold (performance over quota).
    """
    data = load_accounts()
    accounts = [a for a in (data.get("accounts") or []) if a.get("enabled", True)]
    accounts = [a for a in accounts if a.get("provider", "claude") == "claude"]
    accounts.sort(key=lambda a: a.get("priority", 99))
    thr = get_threshold()
    state = load_state()
    mode = get_mode()
    ignore_thr = (mode.get("policy") or {}).get("account_rotation") == "ignore_threshold"

    if force_id:
        for a in accounts:
            if a["id"] == force_id:
                return _activate(a, state, reason="forced")
        raise KeyError(force_id)

    if not accounts:
        return {
            "account": None,
            "switched": False,
            "threshold": thr,
            "reason": "no_accounts",
            "hint": f"Copy config/accounts.example.json → {DEFAULT_ACCOUNTS}",
        }

    # Automatic rotation moves between API keys only. Cycling subscription
    # logins to get past usage limits is what Anthropic's terms bar for
    # third-party tools; a profile is used only when named (force_id above).
    accounts = [a for a in accounts if a.get("type") != "oauth_profile"]
    if not accounts:
        return {
            "account": None,
            "switched": False,
            "threshold": thr,
            "reason": "no_rotatable_accounts",
            "hint": "subscription profiles are not rotated — `effi accounts select --id <id>`, "
                    "or add api_key accounts for rotation",
        }

    # Apex: always highest-priority account (quota is not the gate)
    if ignore_thr:
        a = accounts[0]
        return _activate(a, state, reason="apex_ignore_threshold")

    # Prefer current if under threshold
    cur = state.get("active_account_id")
    if cur:
        for a in accounts:
            if a["id"] == cur and float(a.get("usage_percent") or 0) < thr:
                return {"account": a, "switched": False, "threshold": thr, "reason": "active_ok"}

    # First under threshold by priority
    for a in accounts:
        if float(a.get("usage_percent") or 0) < thr:
            switched = a["id"] != cur
            return _activate(a, state, reason="under_threshold" if switched else "active_ok")

    # All over — pick lowest usage
    a = min(accounts, key=lambda x: float(x.get("usage_percent") or 0))
    return _activate(a, state, reason="all_over_threshold_lowest")

def _activate(account: dict, state: dict, reason: str) -> dict:
    prev = state.get("active_account_id")
    switched = prev != account["id"]
    state["active_account_id"] = account["id"]
    state.setdefault("history", []).append(
        {
            "at": datetime.now().isoformat(timespec="minutes"),
            "account_id": account["id"],
            "reason": reason,
            "usage_percent": account.get("usage_percent"),
        }
    )
    state["history"] = state["history"][-50:]
    save_state(state)
    return {
        "account": account,
        "switched": switched,
        "previous": prev,
        "threshold": get_threshold(),
        "reason": reason,
    }


def env_for_account(account: dict) -> dict:
    """Environment exports to apply for this account (caller sets os.environ)."""
    env = {}
    if not account:
        return env
    t = account.get("type")
    if t == "api_key":
        key = None
        if account.get("api_key_env"):
            key = os.environ.get(account["api_key_env"])
        if account.get("api_key"):  # discouraged but supported
            key = account["api_key"]
        if key:
            env["ANTHROPIC_API_KEY"] = key
        # ensure cloud (not local ollama base)
        env["ANTHROPIC_BASE_URL"] = account.get("base_url") or "https://api.anthropic.com"
    elif t == "oauth_profile":
        cdir = os.path.expanduser(account.get("config_dir") or "")
        if cdir:
            env["CLAUDE_CONFIG_DIR"] = cdir
    return env


def apply_account_env(account: dict) -> dict:
    env = env_for_account(account)
    for k, v in env.items():
        os.environ[k] = v
    return env


# ── Project init ─────────────────────────────────────────────────────

def init_project(project: Optional[Path] = None, force: bool = False) -> dict:
    """Scaffold CLAUDE.md link + tasks/ + .effi/ in the user project."""
    proj = project or project_root()
    actions = []
    tasks = proj / "tasks"
    if not tasks.exists():
        tasks.mkdir(parents=True)
        (tasks / ".gitkeep").write_text("", encoding="utf-8")
        actions.append(f"created {tasks}")
    else:
        actions.append(f"exists {tasks}")

    effi_dir = project_effi_dir(proj)
    if not effi_dir.exists():
        effi_dir.mkdir(parents=True)
        (effi_dir / ".gitignore").write_text(
            "# commit 'mode' if the team shares a default orchestration mode\nmode\n",
            encoding="utf-8",
        )
        actions.append(f"created {effi_dir} (project mode lives here)")
    else:
        actions.append(f"exists {effi_dir}")

    claude_dst = proj / "CLAUDE.md"
    claude_src = ROOT / "CLAUDE.md"
    if claude_dst.exists() or claude_dst.is_symlink():
        if force:
            claude_dst.unlink()
            claude_dst.symlink_to(claude_src)
            actions.append(f"relinked CLAUDE.md → {claude_src}")
        else:
            actions.append("CLAUDE.md already present (use --force to relink)")
    else:
        try:
            claude_dst.symlink_to(claude_src)
            actions.append(f"linked CLAUDE.md → {claude_src}")
        except OSError:
            # fallback copy
            claude_dst.write_text(claude_src.read_text(encoding="utf-8"), encoding="utf-8")
            actions.append("copied CLAUDE.md (symlink failed)")

    # optional pointer to full rules
    pointer = proj / ".effi-root"
    pointer.write_text(str(ROOT) + "\n", encoding="utf-8")
    actions.append(f"wrote .effi-root → {ROOT}")

    return {"project": str(proj), "toolkit": str(ROOT), "actions": actions}


# ── Doctor ───────────────────────────────────────────────────────────

def doctor() -> dict:
    """Health check for toolkit, project, accounts, local runtime."""
    checks = []
    ok = True

    def add(name: str, passed: bool, detail: str) -> None:
        nonlocal ok
        if not passed:
            ok = False
        checks.append({"name": name, "ok": passed, "detail": detail})

    ver = version()
    add("version", bool(ver), ver)
    add("catalog", CATALOG_MODELS.is_file(), str(CATALOG_MODELS))
    add("routing", CATALOG_ROUTING.is_file(), str(CATALOG_ROUTING))
    add("modes", CATALOG_MODES.is_file(), str(CATALOG_MODES))
    add("templates", (ROOT / "templates" / "task.md").is_file(), str(ROOT / "templates"))
    try:
        m = get_mode()
        add(
            "mode",
            True,
            f"{m.get('emoji','')} {m.get('name')} ({m.get('id')})"
            + ("" if mode_is_set() else " [default]"),
        )
    except Exception as e:
        add("mode", False, str(e))

    cat = load_catalog()
    stale = catalog_is_stale(cat)
    add("catalog_fresh", not stale, f"next_review_due={cat.get('next_review_due')} stale={stale}")

    proj = project_root()
    add("project_root", True, str(proj))
    add("tasks_dir", True, str(tasks_dir(proj)))
    add(
        "project_claude",
        (proj / "CLAUDE.md").exists(),
        "ok" if (proj / "CLAUDE.md").exists() else "run: effi init",
    )

    # CLIs
    def which(cmd: str) -> Optional[str]:
        from shutil import which as w
        return w(cmd)

    for cmd in ("claude", "ollama"):
        p = which(cmd)
        add(f"cli:{cmd}", p is not None, p or "not found")

    for cmd in ("codex", "gemini", "grok"):
        p = which(cmd)
        detail = p or "optional — not found"
        if cmd == "gemini":
            # The CLI is alive; its individual Google login is not. Checking for
            # ~/.gemini/oauth_creds.json would pass on a credential the server
            # refuses — the key is the only state worth reporting.
            if not p:
                detail = "optional — install: npm i -g @google/gemini-cli"
            elif not os.environ.get("GEMINI_API_KEY"):
                detail = f"{p} (개인용 OAuth 폐기 — export GEMINI_API_KEY=… 필요)"
        checks.append({"name": f"cli:{cmd}", "ok": True, "detail": detail})

    # Ollama (optional unless you rely on local / effi local)
    cfg = load_config()
    url = cfg["local"]["ollama_url"]
    try:
        with urllib.request.urlopen(url.rstrip("/") + "/api/tags", timeout=3) as r:
            tags = json.load(r)
        n = len(tags.get("models") or [])
        checks.append({"name": "ollama", "ok": True, "detail": f"{url} models={n}"})
    except Exception as e:
        checks.append(
            {
                "name": "ollama",
                "ok": True,  # soft — cloud-only users are fine
                "detail": f"off ({e.__class__.__name__}) — needed for effi local / run / edit",
            }
        )

    # Accounts
    if DEFAULT_ACCOUNTS.exists():
        accs = list_accounts()
        thr = get_threshold()
        enabled = [a for a in accs if a.get("enabled", True)]
        under = [a for a in enabled if float(a.get("usage_percent") or 0) < thr]
        # keys present?
        keyed = 0
        for a in enabled:
            if a.get("type") == "api_key" and a.get("api_key_env") and os.environ.get(a["api_key_env"]):
                keyed += 1
            if a.get("type") == "oauth_profile" and a.get("config_dir"):
                if Path(os.path.expanduser(a["config_dir"])).exists():
                    keyed += 1
        detail = (
            f"{len(enabled)} enabled, {len(under)} under {thr}%, "
            f"{keyed} credentials resolvable"
        )
        if enabled and keyed == 0:
            detail += " — set api_key_env exports (see docs/accounts.md)"
        add("accounts", len(enabled) > 0, detail)
        if enabled and not under:
            checks.append(
                {
                    "name": "accounts_capacity",
                    "ok": False,
                    "detail": f"all accounts ≥ {thr}% — meter/reset or raise threshold",
                }
            )
            ok = False
        if enabled and keyed == 0:
            checks.append(
                {
                    "name": "accounts_credentials",
                    "ok": True,  # soft: cloud may use default login
                    "detail": "no api_key_env resolved — export keys or use oauth_profile (docs/accounts.md)",
                }
            )
    else:
        checks.append(
            {
                "name": "accounts",
                "ok": True,
                "detail": "not configured (optional) — effi accounts init · docs/accounts.md",
            }
        )

    # Local pick
    try:
        pick = pick_local()
        add("local_pick", True, f"{pick['model']} budget={pick.get('budget_gb')}GB")
    except Exception as e:
        add("local_pick", False, str(e))

    soft = {
        "cli:codex",
        "cli:gemini",
        "cli:grok",
        "ollama",
        "accounts",  # optional
        "project_claude",  # fixed by effi init
    }
    hard_ok = all(c["ok"] for c in checks if c["name"] not in soft)
    return {
        "ok": ok and hard_ok,
        "version": ver,
        "toolkit": str(ROOT),
        "project": str(proj),
        "checks": checks,
    }


# ── Providers & preflight (Layer 1: connection + credit advisor) ─────
# See docs/02-design/model-transparency-advisor.md
# Connection = live probe (real). Credit = LOCAL USD ESTIMATE (never faked
# as real-time balance — provider APIs mostly can't return remaining credit).

CATALOG_PROVIDERS = ROOT / "catalog" / "providers.example.json"
USER_PROVIDERS = CONFIG_DIR / "providers.json"
USAGE_LEDGER = CONFIG_DIR / "usage-ledger.ndjson"

_PROVIDER_BEST_FOR = {
    "claude": "plan/impl/review",
    "openai": "bulk/refactor",
    "gemini": "design/research",
    "antigravity": "design/research(구독·기동~40s)",
    "grok": "research/realtime",
    "local": "bulk/mechanical",
}
_CONN_ICON = {"connected": "🟢", "partial": "🟡", "down": "🔴"}


def _which(cmd: Optional[str]) -> Optional[str]:
    if not cmd:
        return None
    from shutil import which as w
    return w(cmd)


def oauth_creds_path(spec: dict) -> Optional[str]:
    """Path of an existing, non-empty subscription/OAuth credential file, if the
    provider declares one (`oauth_creds`: str or list of candidate paths).

    This is the honest signal for "the user is logged in with their subscription
    account" — the CLI binary merely being on PATH proves nothing. Providers that
    keep credentials outside the filesystem (Claude Code → macOS Keychain) simply
    omit the field and keep the CLI-presence heuristic.
    """
    raw = spec.get("oauth_creds")
    if not raw:
        return None
    for cand in ([raw] if isinstance(raw, str) else list(raw)):
        p = Path(os.path.expanduser(str(cand)))
        try:
            if p.is_file() and p.stat().st_size > 0:
                return str(p)
        except OSError:
            continue
    return None


def oauth_retirement(spec: dict) -> Optional[str]:
    """The retirement marker for this provider's OAuth client, or None if it
    still has a live path.

    A retirement is rarely total. Google stopped serving *Gemini Code Assist for
    individuals* — free, Google AI Pro **and** AI Ultra — on 2026-06-18, while
    leaving Code Assist **Standard/Enterprise** untouched; those run against a
    licensed GCP project. `oauth_retired_unless_env` names the env var that
    signals the still-live path (`GOOGLE_CLOUD_PROJECT`): when it is set we fall
    back to normal credential judgement instead of declaring the login dead.
    Being wrong in that direction would be the same bug in mirror image.
    """
    retired = spec.get("oauth_retired")
    if not retired:
        return None
    escape = spec.get("oauth_retired_unless_env")
    if escape and os.environ.get(escape):
        return None
    return retired


def load_providers() -> dict:
    """User providers.json if present, else bundled example (works zero-config)."""
    if USER_PROVIDERS.exists():
        return _load_json(USER_PROVIDERS)
    if CATALOG_PROVIDERS.exists():
        return _load_json(CATALOG_PROVIDERS)
    return {"schema_version": 1, "providers": {}}


def model_price(models_provider: str, model_id: str) -> Optional[tuple]:
    """(cost_in, cost_out) USD per 1M tokens from models.json, or None."""
    cat = load_catalog()
    prov = (cat.get("providers") or {}).get(models_provider) or {}
    m = (prov.get("models") or {}).get(model_id)
    if not m or m.get("cost_in") is None or m.get("cost_out") is None:
        return None
    return (float(m["cost_in"]), float(m["cost_out"]))


def usage_summary(provider: Optional[str] = None, since: Optional[str] = None) -> dict:
    """Read-only aggregate of the USD usage ledger.

    Returns {provider: {"in","out","usd","events"}}. P1 ships the read side;
    record_usage() (write side) lands in P2.
    """
    out: dict = {}
    if not USAGE_LEDGER.exists():
        return out
    with open(USAGE_LEDGER, encoding="utf-8") as f:
        for line in f:
            line = line.strip()
            if not line:
                continue
            try:
                ev = json.loads(line)
                p = ev.get("provider")
                if not p:
                    continue
                if provider and p != provider:
                    continue
                if since and (ev.get("at") or "") < since:
                    continue
                agg = out.setdefault(p, {"in": 0, "out": 0, "usd": 0.0, "events": 0})
                agg["in"] += int(ev.get("in") or 0)
                agg["out"] += int(ev.get("out") or 0)
                agg["usd"] += float(ev.get("est_usd") or 0)
                agg["events"] += 1
            except Exception:
                # skip any malformed line (bad JSON OR non-numeric fields) —
                # a corrupt ledger must never crash preflight/SessionStart
                continue
    return out


def estimate_headroom(pid: str, spec: dict, summary: Optional[dict] = None) -> dict:
    """USD-unified estimate. remaining_pct=None when unbudgeted or subscription."""
    summ = summary if summary is not None else usage_summary()
    used = float((summ.get(pid) or {}).get("usd") or 0.0)
    budget = float(spec.get("budget_usd") or 0)
    if spec.get("free"):
        return {"kind": "free", "used_usd": round(used, 4), "budget_usd": None,
                "remaining_usd": None, "remaining_pct": None}
    if budget <= 0:
        kind = "subscription" if spec.get("subscription") else "unbudgeted"
        return {"kind": kind, "used_usd": round(used, 4), "budget_usd": None,
                "remaining_usd": None, "remaining_pct": None}
    remaining = max(0.0, budget - used)
    return {"kind": "usd", "used_usd": round(used, 4), "budget_usd": budget,
            "remaining_usd": round(remaining, 2),
            "remaining_pct": round(100 * remaining / budget, 1)}


def _probe_api_call(url: str, key_env: str, pid: str, timeout: float = 4.0) -> bool:
    """Lightweight live reachability check (models list). Provider-specific auth."""
    key = os.environ.get(key_env)
    if not key:
        return False
    headers: dict = {}
    target = url
    if pid == "claude":
        headers = {"x-api-key": key, "anthropic-version": "2023-06-01"}
    elif pid == "gemini":
        sep = "&" if "?" in target else "?"
        target = f"{target}{sep}key={key}"
    else:  # codex/openai, grok/xai — bearer
        headers = {"Authorization": f"Bearer {key}"}
    try:
        req = urllib.request.Request(target, headers=headers)
        with urllib.request.urlopen(req, timeout=timeout) as r:
            return 200 <= getattr(r, "status", 200) < 300
    except Exception:
        return False


def probe_provider(pid: str, spec: dict, do_call: bool = False) -> dict:
    """Connection status: existence check + optional live API call.

    🟢 connected: credential present (key, or subscription OAuth login) and
                  reachable/unchecked
    🟡 partial:   credential present but CLI missing / api-only / not logged in
                  yet / call failed / the provider retired the OAuth client the
                  credential belongs to (`oauth_retired`)
    🔴 down:      no credential and no CLI
    """
    key_env = spec.get("api_key_env")
    has_key = bool(key_env and os.environ.get(key_env))
    cli, cli_legacy = spec.get("cli"), spec.get("cli_legacy")
    cli_path = _which(cli)
    legacy_path = _which(cli_legacy)
    oauth = spec.get("cli_auth") in ("subscription_oauth", "google_oauth")
    creds_file = oauth_creds_path(spec) if oauth else None
    # A credential file proves a login *happened* — not that the backend still
    # honours it. When a provider retires its OAuth client (`oauth_retired`),
    # the login keeps succeeding and keeps writing creds, while every actual
    # call is refused; the stale file would otherwise read as a green
    # connection. Gemini is the worked example: Login with Google still fills
    # ~/.gemini/oauth_creds.json, then the API answers IneligibleTierError
    # (UNSUPPORTED_CLIENT, "Gemini Code Assist for individuals").
    retired = oauth_retirement(spec)
    if retired:
        has_oauth = False
    elif spec.get("oauth_creds"):
        # declares a creds path ⇒ must actually show a login on disk
        has_oauth = bool(creds_file)
    else:
        # keeps credentials off-filesystem ⇒ "CLI on PATH ⇒ assume logged in"
        has_oauth = bool(oauth and (cli_path or legacy_path))

    parts = []
    if has_key:
        parts.append("key")
    if has_oauth and spec.get("oauth_creds"):
        parts.append("oauth")
    if cli_path:
        parts.append(f"cli:{cli}")
    elif legacy_path:
        parts.append(f"cli:{cli_legacy}(legacy)")

    has_cred = has_key or has_oauth

    api_ok = None
    if do_call and has_key and spec.get("probe_api"):
        api_ok = _probe_api_call(spec["probe_api"], key_env, pid)
        parts.append("api ok" if api_ok else "api fail")

    if not has_cred and not cli_path and not legacy_path:
        conn = "down"
    elif api_ok is False:
        conn = "partial"
    elif has_cred:
        conn = "connected"
    else:
        conn = "partial"

    # free local backend (Ollama): needs no credential — CLI present ⇒ usable
    if spec.get("free") and (cli_path or legacy_path):
        conn = "connected"

    # An API key alone is a real credential — the provider is routable (🟢);
    # note that it's the metered path and no login CLI is around.
    if has_key and not has_oauth and not cli_path and not legacy_path:
        parts.append(f"api-only ({cli} 미설치)" if cli else "api-only")
    # Retired OAuth client: the login CLI is installed and may even hold creds,
    # but that road is closed permanently. Never say "미로그인" here — the user
    # very likely *did* log in; the key is the only way back to 🟢.
    elif retired and (cli_path or legacy_path):
        if not has_key:
            # Cause → action, short enough to survive the status column; the
            # full story lives in `effi connect <p>`. Credentials on disk mean
            # the user already tried the login, so name the retirement; without
            # them, just ask for the key.
            # insert(0): the status column truncates, so the one thing the user
            # must act on has to come before "cli:…" — not after it.
            parts.insert(0, "oauth 폐기 → 키 필요" if creds_file
                         else (f"키 필요({key_env})" if key_env else "키 필요"))
            conn = "partial"
    # Login CLI installed but the subscription login was never done. Without a
    # key there is nothing to route with (🟡) — with one, the key carries it.
    elif (cli_path or legacy_path) and spec.get("oauth_creds") and not has_oauth:
        parts.append("미로그인(키 사용)" if has_key else "미로그인")
        if not has_key:
            conn = "partial"

    return {"id": pid, "label": spec.get("label", pid), "connection": conn,
            "detail": ", ".join(parts) or "no credential",
            "cli": cli, "has_key": has_key, "has_oauth": has_oauth,
            "oauth_creds": creds_file, "api_ok": api_ok}


def preflight(probe: bool = False, task_hint: Optional[str] = None) -> dict:
    """Layer 1: check all providers + recommend a mode. Safe to run at SessionStart."""
    provs = (load_providers().get("providers") or {})
    summ = usage_summary()
    results = []
    for pid, spec in provs.items():
        pr = probe_provider(pid, spec, do_call=probe)
        pr["headroom"] = estimate_headroom(pid, spec, summ)
        # Prefer a provider-specific label, then the model family it bills as:
        # antigravity prices like gemini but is a very different thing to run.
        pr["best_for"] = (_PROVIDER_BEST_FOR.get(pid)
                          or _PROVIDER_BEST_FOR.get(spec.get("models_provider", pid), "—"))
        results.append(pr)
    band = assess_task_importance(task_hint).get("band") if task_hint else None
    return {"at": datetime.now().isoformat(timespec="minutes"),
            "providers": results,
            "mode_recommendation": recommend_mode(results, band),
            "current_mode": get_mode()}


def recommend_mode(providers: list, importance_band: Optional[str] = None) -> dict:
    """Suggest a mode from connection health + estimated headroom + task band."""
    by = {p["id"]: p for p in providers}
    claude_up = (by.get("claude") or {}).get("connection") == "connected"
    others_up = sum(1 for p in providers
                    if p["id"] != "claude" and p.get("connection") != "down")
    tight = any((p.get("headroom") or {}).get("remaining_pct") is not None
                and p["headroom"]["remaining_pct"] < 15 for p in providers)
    high = importance_band == "high"

    if not claude_up and not others_up:
        return {"mode": "sip", "reason": "연결된 클라우드 프로바이더 없음 → 로컬/저가 우선(Sip)"}
    if tight:
        return {"mode": "sip", "reason": "예산 여유 부족(추정 <15%) → 비용 최소 Sip 권장"}
    if high and claude_up:
        return {"mode": "apex", "reason": "고위험/고난도 작업 + Claude 여유 → 최고 성능 Apex"}
    if importance_band == "low" and (claude_up or others_up):
        return {"mode": "sip", "reason": "저위험/기계적 작업 → 비용 최소 Sip이 효율적"}
    if claude_up:
        return {"mode": "cruise", "reason": "Claude 연결 양호 + 보조 프로바이더 가용 → 균형 Cruise"}
    return {"mode": "cruise", "reason": "기본 균형 운용"}


def _dpad(s: str, width: int) -> str:
    """ljust by terminal display width — CJK/emoji count 2 (see _dwidth).

    Status columns carry Korean labels ('미로그인', '무료(로컬)'), so padding by
    len() drifts by one cell per wide char and shears the table.
    """
    return s + " " * max(0, width - _dwidth(s))


def _id_col_width(providers: list, minimum: int = 9) -> int:
    """Provider-id column width: the longest id plus a gap, never below the
    historical 9 so short-list output keeps its familiar shape."""
    longest = max((len(str(p.get("id") or "")) for p in providers), default=0)
    return max(minimum, longest + 1)


def format_preflight(pf: dict) -> str:
    lines = [f"effi preflight — {pf.get('at','')}", ""]
    # Width comes from the data, not a constant: a hard-coded :<9 silently ate
    # the gap for any id longer than 9 chars ("antigravitycli:agy").
    idw = _id_col_width(pf["providers"])
    lines.append(f"  {'Provider':<{idw + 2}}{_dpad('Connection', 24)}{_dpad('Usage (~추정)', 16)}Best for")
    lines.append("  " + "─" * idw + "  " + "─" * 22 + "  " + "─" * 14 + "  " + "─" * 16)
    for p in pf["providers"]:
        icon = _CONN_ICON.get(p["connection"], "·")
        hz = p.get("headroom") or {}
        if hz.get("remaining_pct") is not None:
            usage = f"~${hz['remaining_usd']:.2f}/${hz['budget_usd']:.0f}"
        elif hz.get("kind") == "free":
            usage = "무료(로컬)"
        elif hz.get("kind") == "subscription":
            usage = "~여유(구독)"
        else:
            usage = "~예산미설정"
        lines.append(f"  {icon} {p['id']:<{idw}}{_dpad(p['detail'], 24)}{_dpad(usage, 16)}{p.get('best_for','—')}")
    rec = pf["mode_recommendation"]
    cur = pf.get("current_mode") or {}
    modes = {m["id"]: m for m in list_modes()}
    rm = modes.get(rec["mode"], {})
    lines += [
        "",
        f"  추천 모드: {rm.get('emoji','')} {rm.get('name', rec['mode'])}",
        f"  근거: {rec['reason']}",
        f"  현재: {cur.get('emoji','')} {cur.get('name','?')}   |   전환: effi mode set {rec['mode']}   |   상세: effi providers",
    ]
    return "\n".join(lines)


# ── Onboarding & guided connect (Layer 0: welcome + connect) ─────────
# See docs/02-design/onboarding-connect.md. `effi connect` explains what
# effi-code is, shows which providers are connected, and guides the user
# through each provider's OWN first-party login. It NEVER proxies subscription
# OAuth through a router (ToS hard-no) — it only points to / runs the provider's
# native auth (codex login, gemini, claude, …) or an API-key env var.

def onboarding_intro() -> str:
    """One-paragraph effi-code explainer — single source of truth reused by the
    SessionStart hook, the launcher, and `effi connect --intro`."""
    return (
        "effi-code — 비용 인지형 멀티-프로바이더 코딩 오케스트레이터.\n"
        "  한 대화(메인 스레드는 Claude 유지)에서 작업을 분류해 Claude·Codex·"
        "Gemini·Grok·로컬(Ollama) 중 가장 적합·경제적인 모델로 라우팅합니다.\n"
        "  성능↔비용은 3모드로 조절: 🚀 Apex(최고성능) · 🛣 Cruise(균형) · "
        "☕ Sip(최소비용). 연결은 실측하고, 크레딧은 로컬 추정 원장으로 정직하게 표시합니다."
    )


# How to connect each provider — each entry is the provider's OWN first-party
# login (or an API-key env). `login` is the human hint; `cmd` is what
# `effi connect <pid>` execs interactively in a TTY.
_CONNECT_LOGIN = {
    "claude": "claude  (구독 로그인)  또는  export ANTHROPIC_API_KEY=…",
    "codex":  "codex login  (ChatGPT 구독)  또는  export OPENAI_API_KEY=…",
    "gemini": "export GEMINI_API_KEY=…  (aistudio.google.com/apikey)  — 개인용 Google 로그인은 폐기됨",
    "antigravity": "agy  (Antigravity IDE 로그인을 OS 키체인에서 공유 — AI Pro/Ultra 구독 사용)",
    "grok":   "grok  (로그인)  또는  export XAI_API_KEY=…",
    "local":  "ollama serve  (설치: https://ollama.com)",
}
_CONNECT_CMD = {
    "claude": ["claude"],
    "codex":  ["codex", "login"],
    "gemini": ["gemini"],
    "antigravity": ["agy"],
    "grok":   ["grok"],
    "local":  ["ollama", "serve"],
}


def connect_hint(pid: str, spec: dict) -> dict:
    """How to connect one provider: first-party login + api-key env. Registry-
    driven, with a generic fallback for providers not in the built-in map."""
    cli = spec.get("cli")
    login = _CONNECT_LOGIN.get(pid)
    cmd = _CONNECT_CMD.get(pid) or ([cli] if cli else None)
    if not login:
        env = spec.get("api_key_env")
        bits = []
        if cli:
            bits.append(f"{cli} 로그인")
        if env:
            bits.append(f"export {env}=…")
        login = "  또는  ".join(bits) or "연결법 미정"
    # Registry-driven honesty: if the login CLI isn't installed, the actionable
    # step is the install command — not "run the login you don't have".
    install = spec.get("install")
    retired = oauth_retirement(spec)
    if retired:
        # Never route the user into a retired login: it authenticates, writes
        # credentials, and *then* the provider refuses to serve. The key is the
        # only remaining way in, so lead with it — installing the CLI or
        # re-running the login would both be dead ends.
        env, url = spec.get("api_key_env"), spec.get("api_key_url")
        login = f"export {env}=…" if env else "API 키 필요"
        if url:
            login += f"  ({url})"
        login += f"  ·  개인용(무료·AI Pro·Ultra) 로그인은 {retired} 폐기"
    elif install and not _which(cli) and not _which(spec.get("cli_legacy")):
        env = spec.get("api_key_env")
        login = f"{install}  →  effi connect {pid}  (구독 로그인)"
        if env:
            login += f"  ·  또는  export {env}=… (종량제 라우팅)"
    return {
        "id": pid,
        "label": spec.get("label", pid),
        "login": login,
        "cli": cli,
        "cli_legacy": spec.get("cli_legacy"),
        "api_key_env": spec.get("api_key_env"),
        "cmd": cmd,
        "install": install,
        "oauth_creds": oauth_creds_path(spec),
        "note": spec.get("note"),
    }


def _retired_guide(pid: str, spec: dict, retired: str) -> str:
    """What to do when a provider has retired the login effi used to run.

    Built from the registry so a future retirement needs no code change: set
    `oauth_retired` (+ optional `oauth_retired_note`, `api_key_url`) and the
    guidance follows.
    """
    lines = [f"· {pid} 개인용 로그인(무료·유료 AI Pro/Ultra 모두)은 {retired}에 "
             "폐기됐습니다 — 로그인 자체는 성공하지만 이후 호출이 거부됩니다."]
    note = spec.get("oauth_retired_note")
    if note:
        lines.append(f"  {note}")
    lines.append("  연결법:")
    step = 1
    url = spec.get("api_key_url")
    if url:
        lines.append(f"    {step}) 키 발급: {url}")
        step += 1
    env_name = spec.get("api_key_env")
    if env_name:
        lines.append(f"    {step}) export {env_name}=…   (셸 프로필에 추가해 세션마다 유지)")
        step += 1
    lines.append(f"    {step}) 확인: effi connect")
    return "\n".join(lines)


def connect_command(pid: str, spec: dict) -> dict:
    """Resolve the interactive login command for `effi connect <pid>`.
    available=True only when the login binary is on PATH (else guide install).

    `env` carries provider hints that preselect the *subscription* login in the
    CLI's own auth picker (e.g. Gemini's oauth-personal = Login with Google), so
    the user lands on the right choice instead of an API-key prompt. It only
    seeds that CLI's native flow — no OAuth is ever proxied through effi.
    """
    h = connect_hint(pid, spec)
    cmd = list(h.get("cmd") or [])
    binary = cmd[0] if cmd else None
    # primary login CLI missing but the legacy/alternate one is installed → use it
    if binary and not _which(binary):
        legacy = spec.get("cli_legacy")
        if legacy and _which(legacy):
            cmd[0] = binary = legacy

    # A retired OAuth client has no interactive login left to run — seeding its
    # auth picker would walk the user straight into the refusal. Hand back a
    # guide instead of a command, and drop the auth-type seed entirely.
    retired = oauth_retirement(spec)
    guide = _retired_guide(pid, spec, retired) if retired else None

    env = {}
    auth_type = spec.get("oauth_auth_type")
    if auth_type and pid == "gemini" and not retired:
        env["GEMINI_DEFAULT_AUTH_TYPE"] = auth_type

    return {
        "id": pid,
        "cmd": cmd,
        "binary": binary,
        "available": bool(binary and _which(binary)) and not retired,
        "login": h.get("login"),
        "install": spec.get("install"),
        "env": env,
        # Credentials may still sit on disk after the client was retired, but
        # they buy nothing — reporting them as a login would be a lie.
        "logged_in": bool(oauth_creds_path(spec)) and not retired,
        "oauth_retired": retired,
        "guide": guide,
        "api_key_env": h.get("api_key_env"),
    }


def connect_report(probe: bool = False) -> dict:
    """Preflight augmented with per-provider connect hints. Basis for
    `effi connect`, the launcher welcome, and the SessionStart hook."""
    provs = (load_providers().get("providers") or {})
    rep = preflight(probe=probe)
    for p in rep["providers"]:
        spec = provs.get(p["id"], {})
        p["hint"] = connect_hint(p["id"], spec)
        # can `effi connect <p>` actually launch a login now? (login CLI on PATH)
        p["login_available"] = connect_command(p["id"], spec)["available"]
    rep["missing"] = [p["id"] for p in rep["providers"] if p["connection"] == "down"]
    rep["partial"] = [p["id"] for p in rep["providers"] if p["connection"] == "partial"]
    return rep


def format_connect(rep: dict, intro: bool = False, action: bool = False,
                   table: bool = True) -> str:
    """Human-readable onboarding view: (optional intro) + preflight table +
    how-to-connect for anything not fully connected + (optional) an
    [effi:action] block that asks the assistant to drive the flow.

    `table=False` drops the preflight table for callers that already showed the
    same provider status (the launch splash) — don't print it twice.
    """
    lines: list[str] = []
    if intro:
        lines += [onboarding_intro(), ""]
    if table:
        lines.append(format_preflight(rep))
    todo = [p for p in rep["providers"]
            if p["connection"] in ("down", "partial")]
    if todo:
        lines += ["", "  연결하기 (미연결/부분):"]
        for p in todo:
            icon = _CONN_ICON.get(p["connection"], "·")
            h = p.get("hint") or {}
            lines.append(f"   {icon} {p['id']:<7} {h.get('login','—')}")
            # only offer "바로 실행" when the login CLI is present — otherwise
            # `effi connect <p>` just re-prints install guidance (noise). The
            # honest login hint above is the actionable step in that case.
            if p["id"] != "local" and p.get("login_available"):
                lines.append(f"       ↳ 바로 실행: effi connect {p['id']}")
    else:
        lines += ["", "  ✅ 모든 프로바이더 연결됨."]
    if action:
        lines += ["", onboarding_action()]
    return "\n".join(lines)


def onboarding_action() -> str:
    """The [effi:action] block that asks the assistant to drive onboarding.

    Split out because it is addressed to the *assistant*, not the user: the
    SessionStart hook routes it to `additionalContext` while the human-facing
    intro/connect text goes to `systemMessage`.
    """
    return "\n".join([
        "[effi:action] 온보딩 — 아래 순서로 진행하세요:",
        "  1) effi-code가 뭔지 위 intro를 바탕으로 한두 줄로 소개합니다.",
        "  2) 미연결(🔴)·부분(🟡) 프로바이더가 있으면 연결법을 안내하고, 사용자가",
        "     원하면 `! effi connect <provider>`(또는 `! codex login` 등)로 로그인하도록",
        "     도운 뒤 `effi connect --json`으로 재검사합니다.",
        "  3) 이번 세션에 무슨 작업을 할지 물어 `effi mode suggest \"<답변>\"`로 추천받고,",
        "     사용자가 고른 모드를 `effi mode set <apex|cruise|sip>`로 고정합니다.",
    ])


# ── Launch splash (Layer 0.5: the face of effi-code) ─────────────────
# `effi` / `effi local` print one screen before handing off to Claude Code:
# what am I running, on what model, what's connected, what can I type next.
# splash_data() gathers, format_splash() lays out — both pure enough to unit
# test without a terminal (no probing unless asked).

# ANSI Shadow block font. Rendered above the panel when the terminal is wide
# enough; narrower terminals fall back to a plain title line.
EFFI_WORDMARK = [
    "███████╗███████╗███████╗██╗        ██████╗ ██████╗ ██████╗ ███████╗",
    "██╔════╝██╔════╝██╔════╝██║       ██╔════╝██╔═══██╗██╔══██╗██╔════╝",
    "█████╗  █████╗  █████╗  ██║ █████╗██║     ██║   ██║██║  ██║█████╗  ",
    "██╔══╝  ██╔══╝  ██╔══╝  ██║ ╚════╝██║     ██║   ██║██║  ██║██╔══╝  ",
    "███████╗██║     ██║     ██║       ╚██████╗╚██████╔╝██████╔╝███████╗",
    "╚══════╝╚═╝     ╚═╝     ╚═╝        ╚═════╝ ╚═════╝ ╚═════╝ ╚══════╝",
]

# Road-to-horizon mark (all single-width box chars — safe to align).
EFFI_MARK = [
    "╲                ╱",
    " ╲   ▁▂▃▄▅▆▇   ╱ ",
    "  ╲  ████████  ╱  ",
    "   ╲ ██▔▔▔▔██ ╱   ",
    "    ╲████████╱    ",
    "     ╲██▔▔██╱     ",
    "      ╲████╱      ",
    "       ╲██╱       ",
]

# Command surface, grouped. Single source of truth for the panel *and* the
# command count — add a subcommand here when you add it to bin/effi.
COMMAND_GROUPS: list[tuple[str, list[str]]] = [
    ("세션", ["cloud", "local", "status", "doctor", "init", "splash"]),
    ("연결·비용", ["connect", "preflight", "providers", "accounts",
                   "hooks", "statusline"]),
    ("라우팅", ["mode", "route", "use", "pick", "classify"]),
    ("작업", ["new", "run", "edit", "review", "log", "catalog"]),
]

SPLASH_TIPS = [
    "effi route \"작업 설명\" — 도메인·등급을 보고 최적 모델을 고릅니다.",
    "effi mode set apex — 이 프로젝트를 최고 성능으로 고정합니다 (.effi/mode).",
    "effi mode set sip — 로컬/저가 우선. 번역·docstring 같은 기계적 작업에.",
    "effi edit <file> \"지시\" — 로컬 모델이 사이드카에 쓰고, 검토 후 반영합니다.",
    "effi providers budget claude 20 — 예산을 넣으면 잔여 추정이 정확해집니다.",
    "effi preflight --probe — 연결을 실제 API 호출로 확인합니다.",
    "EFFI_NO_SPLASH=1 effi — 이 시작 화면을 건너뜁니다.",
]

_SPLASH_MIN_W, _SPLASH_MAX_W = 72, 118
_SPLASH_TWO_COL_W = 92   # below this, the panel stacks into one column

_ANSI = {
    "reset": "\x1b[0m", "bold": "\x1b[1m", "dim": "\x1b[2m",
    "cyan": "\x1b[36m", "yellow": "\x1b[33m",
}


def _dwidth(s: str) -> int:
    """Terminal display width: CJK/emoji count 2, combining marks 0.

    east_asian_width covers Hangul and most emoji; the explicit ≥U+1F300 test
    catches pictographs (🛣, ⚠︎-style) that Unicode still calls Neutral but
    every modern terminal renders double-wide.
    """
    import unicodedata

    w = 0
    for ch in s:
        if unicodedata.combining(ch) or ch in "️︎‍":
            continue
        cp = ord(ch)
        if unicodedata.east_asian_width(ch) in ("W", "F") or 0x1F300 <= cp <= 0x1FAFF:
            w += 2
        else:
            w += 1
    return w


def _dtrim(s: str, width: int) -> str:
    """Truncate to a display width, ellipsising when it doesn't fit."""
    if _dwidth(s) <= width:
        return s
    out, w = "", 0
    for ch in s:
        cw = _dwidth(ch)
        if w + cw > max(0, width - 1):
            break
        out += ch
        w += cw
    return out + "…"


def _dpad(s: str, width: int) -> str:
    s = _dtrim(s, width)
    return s + " " * max(0, width - _dwidth(s))


def _color(s: str, style: Optional[str], enabled: bool) -> str:
    if not enabled or not style:
        return s
    codes = "".join(_ANSI.get(p, "") for p in style.split())
    return f"{codes}{s}{_ANSI['reset']}" if codes else s


def mode_headline_model(mode: Optional[dict] = None) -> dict:
    """The model this mode leads with for coding work — what the splash shows.

    Apex pins a top model, Sip pins a ceiling (local runs below it), Cruise
    has no pin and inherits the routing table's `implement` primary.
    """
    m = mode or get_mode()
    pol = m.get("policy") or {}
    prov = pol.get("default_coding_provider")
    model = pol.get("default_coding_model")
    prefix = ""
    if not model and pol.get("coding_ceiling_model"):
        model = pol["coding_ceiling_model"]
        prov = pol.get("coding_ceiling_provider")
        prefix = "≤ "
    if not model:
        prim = ((load_routing().get("domains") or {}).get("implement") or {}).get(
            "primary"
        ) or {}
        prov, model = prim.get("provider"), prim.get("model")
    return {"provider": prov, "model": model, "prefix": prefix,
            "label": f"{prefix}{model}" if model else "—",
            "local_first": bool(pol.get("cascade") == "local_first")}


def new_session_id(now: Optional[datetime] = None) -> str:
    """Timestamped launch id — shown in the splash, useful in bug reports."""
    now = now or datetime.now()
    return now.strftime("%Y%m%d_%H%M%S") + "_" + os.urandom(2).hex()


def _wrap_cell(text: str, width: int, indent: int = 0) -> list[str]:
    """Word-wrap to a display width, hanging-indenting continuation lines.
    Long unbreakable tokens are trimmed rather than allowed to overflow."""
    if _dwidth(text) <= width:
        return [text]
    # keep the caller's own leading indent on the first line — splitting on " "
    # would otherwise drop it as empty words and unalign the row
    stripped = text.lstrip(" ")
    lead = " " * (len(text) - len(stripped))
    pad = " " * indent
    lines: list[str] = []
    cur = ""
    for word in (w for w in stripped.split(" ") if w):
        if not cur:
            cur = (lead if not lines else pad) + word
            continue
        cand = cur + " " + word
        if _dwidth(cand) <= width:
            cur = cand
        else:
            lines.append(cur)
            cur = pad + word
    if cur:
        lines.append(cur)
    return [_dtrim(x, width) for x in lines]


def _abbrev_path(p: str) -> str:
    home = os.path.expanduser("~")
    return "~" + p[len(home):] if p.startswith(home) else p


def splash_data(
    probe: bool = False,
    runtime: str = "cloud",
    model: Optional[str] = None,
    session: Optional[str] = None,
    now: Optional[datetime] = None,
    tip_index: Optional[int] = None,
    pf: Optional[dict] = None,
) -> dict:
    """Everything the launch screen shows. `runtime` is cloud | local.

    Pass `pf` to reuse a preflight()/connect_report() the caller already ran.
    """
    now = now or datetime.now()
    pf = pf if pf is not None else preflight(probe=probe)
    mode = pf.get("current_mode") or get_mode()
    cat = catalog_status()
    head = mode_headline_model(mode)
    if model:
        head = dict(head, model=model, prefix="", label=model)

    provs = []
    for p in pf["providers"]:
        hz = p.get("headroom") or {}
        if hz.get("remaining_pct") is not None:
            usage = f"~${hz['remaining_usd']:.2f}/${hz['budget_usd']:.0f}"
        elif hz.get("kind") == "free":
            usage = "무료"
        elif hz.get("kind") == "subscription":
            usage = "구독"
        else:
            usage = "예산미설정"
        provs.append({
            "id": p["id"], "connection": p["connection"],
            "icon": _CONN_ICON.get(p["connection"], "·"),
            "detail": p.get("detail") or "", "usage": usage,
            "best_for": p.get("best_for") or "—",
        })

    warnings, notes = [], []
    if cat.get("stale"):
        warnings.append("카탈로그 재검토 기한 지남 — effi catalog research → bump")
    else:
        notes.append(f"카탈로그 재검토 예정 {cat.get('next_review_due') or '—'}")
    down = [p["id"] for p in provs if p["connection"] == "down"]
    if down:
        warnings.append(f"미연결: {', '.join(down)} — effi connect {down[0]}")
    if not project_mode_is_set():
        warnings.append("이 프로젝트 모드 미고정 — effi mode set cruise")

    tips = SPLASH_TIPS
    idx = tip_index if tip_index is not None else now.timetuple().tm_yday
    ncmds = sum(len(c) for _, c in COMMAND_GROUPS)

    return {
        "at": now.isoformat(timespec="seconds"),
        "version": version(),
        "catalog": cat,
        "mode": mode,
        "runtime": runtime,
        "headline": head,
        "project": str(project_root()),
        "cwd": os.getcwd(),
        "session": session or new_session_id(now),
        "providers": provs,
        "groups": [{"name": n, "commands": c} for n, c in COMMAND_GROUPS],
        "counts": {
            "commands": ncmds,
            "providers": len(provs),
            "connected": sum(1 for p in provs if p["connection"] == "connected"),
            "modes": len(list_modes()),
        },
        "warnings": warnings,
        "notes": notes,
        "tip": tips[idx % len(tips)] if tips else "",
        "recommendation": pf.get("mode_recommendation") or {},
    }


def _splash_left(d: dict, width: int) -> list[tuple[str, Optional[str]]]:
    mode = d["mode"]
    rt = "LOCAL · Ollama" if d["runtime"] == "local" else "CLOUD · Claude Code"
    rows: list[tuple[str, Optional[str]]] = []
    if width >= 20:
        pad = " " * max(0, (width - 18) // 2)
        rows += [(pad + line, "cyan dim") for line in EFFI_MARK]
        rows.append(("", None))
    rows += [
        (f"{mode.get('emoji','')} {mode.get('name','?')} · {d['headline']['label']}",
         "bold"),
        (rt, "dim"),
        (_abbrev_path(d["project"]), "dim"),
        (f"Session: {d['session']}", "dim"),
    ]
    return rows


def _splash_right(d: dict, width: int) -> list[tuple[str, Optional[str]]]:
    rows: list[tuple[str, Optional[str]]] = [("Providers", "bold")]
    for p in d["providers"]:
        detail = f"{p['detail']} · {p['usage']}"
        rows.append((f"{p['icon']} {p['id']:<8}{_dtrim(detail, max(8, width - 11))}",
                     None))
    rows.append(("", None))
    rows.append(("Commands", "bold"))
    for g in d["groups"]:
        line = f"  {g['name']}: {', '.join(g['commands'])}"
        rows += [(x, None) for x in _wrap_cell(line, width, indent=4)]
    rows.append(("", None))
    rows.append((
        "  " + " · ".join(f"{m.get('emoji','')} {m['name']}" for m in list_modes())
        + f"   (현재 {d['mode'].get('name','?')})", "dim"))
    c = d["counts"]
    rows.append((
        f"  {c['commands']} commands · {c['connected']}/{c['providers']} providers "
        f"connected · effi help", "dim"))
    for w in d["warnings"]:
        rows += [(x, "yellow") for x in _wrap_cell(f"  ⚠ {w}", width, indent=4)]
    for n in d["notes"]:
        rows += [(x, "dim") for x in _wrap_cell(f"  · {n}", width, indent=4)]
    return rows


def format_splash(d: dict, width: Optional[int] = None, color: bool = True,
                  wordmark: bool = True) -> str:
    """Render the launch screen. Width is clamped to a readable range so the
    panel looks the same in a narrow pane and a maximised terminal."""
    if width is None:
        try:
            import shutil

            width = shutil.get_terminal_size((100, 24)).columns
        except Exception:
            width = 100
    width = max(_SPLASH_MIN_W, min(_SPLASH_MAX_W, int(width)))
    inner = width - 4  # "│ " + content + " │"

    out: list[str] = []
    mark = _dwidth(EFFI_WORDMARK[0])
    if not wordmark:
        pass
    elif width >= mark + 2:
        pad = " " * ((width - mark) // 2)
        out += [_color(pad + line, "cyan", color) for line in EFFI_WORDMARK]
        out.append("")
    else:
        out += [_color("effi-code", "bold cyan", color), ""]

    title = (f" effi-code v{d['version']} · catalog {d['catalog'].get('catalog_version','?')}"
             f" · {d['mode'].get('emoji','')} {d['mode'].get('name','?')} ")
    title = _dtrim(title, inner)
    bar = "─" * max(0, width - 2 - 1 - _dwidth(title))
    out.append(_color(f"╭─{title}{bar}╮", "cyan", color))

    if width >= _SPLASH_TWO_COL_W:
        left_w = min(32, max(24, inner - 46))
        gap = 3
        right_w = inner - left_w - gap
        left = _splash_left(d, left_w)
        right = _splash_right(d, right_w)
        for i in range(max(len(left), len(right))):
            lt, ls = left[i] if i < len(left) else ("", None)
            rt, rs = right[i] if i < len(right) else ("", None)
            cell = (_color(_dpad(lt, left_w), ls, color) + " " * gap
                    + _color(_dpad(rt, right_w), rs, color))
            out.append(_color("│ ", "cyan", color) + cell + _color(" │", "cyan", color))
    else:
        stacked = _splash_left(d, 0) + [("", None)] + _splash_right(d, inner)
        for txt, style in stacked:
            out.append(_color("│ ", "cyan", color)
                       + _color(_dpad(txt, inner), style, color)
                       + _color(" │", "cyan", color))

    out.append(_color("╰" + "─" * (width - 2) + "╯", "cyan", color))
    out.append("")
    out.append("작업을 말하면 도메인·등급을 보고 최적 모델로 라우팅합니다. "
               "규칙: ORCHESTRATION.md")
    if d.get("tip"):
        out.append(_color(f"✦ Tip: {d['tip']}", "dim", color))
    return "\n".join(out)


def splash_line(d: dict) -> str:
    """One-line status — used where the full panel would be noise (resume,
    compaction), and cheap enough to show every time."""
    c = d["counts"]
    bits = [f"{d['mode'].get('emoji','')} {d['mode'].get('name','?')}",
            d["headline"]["label"],
            f"{c['connected']}/{c['providers']} providers"]
    line = "effi · " + " · ".join(b for b in bits if b)
    if d.get("warnings"):
        # trimmed: warnings come from dates and probes, so an uncapped one
        # breaks the one-line promise on whatever day it first appears
        line = _dtrim(line + f"   ⚠ {d['warnings'][0]}", SPLASH_LINE_MAX)
    return line


SPLASH_LINE_MAX = 100


# Sources that re-enter an existing conversation: the user already saw the
# screen, so re-rendering it is noise (and re-paid context). Everything else —
# startup, clear, fork, and any source a future Claude Code adds — is treated
# as a fresh start and gets the screen; failing toward visible is the point.
QUIET_SOURCES = ("resume", "compact")


def hook_session_start_output(
    source: str = "startup",
    width: int = 72,
    wordmark: bool = True,
    muted: bool = False,
    probe: bool = False,
) -> dict:
    """Build the SessionStart hook's JSON reply.

    Claude Code renders the two channels very differently, and the split
    matters more than the layout does:

      systemMessage  → shown to the user in the transcript ("… says: …")
      additionalContext → added to Claude's context, never displayed

    Plain stdout lands in context too, but as a *meta* attachment the user
    never sees — which is why the screen has to go through systemMessage.
    So: the panel and the human-facing connect guidance go to the user, the
    compact table and the [effi:action] instructions go to the model.
    """
    rep = connect_report(probe=probe)
    onboarded = project_mode_is_set()
    full = (source or "startup") not in QUIET_SOURCES and not muted
    d = splash_data(pf=rep)

    shown = [format_splash(d, width=width, color=False, wordmark=wordmark)
             if full else splash_line(d)]
    context = [format_preflight(rep)]

    if not onboarded:
        # intro + how to connect anything missing → the user, but only on a
        # fresh start — resume/compact promise one line;
        # the action block (addressed to the assistant) → the model, always
        if full:
            shown.append(format_connect(rep, intro=True, action=False, table=False))
        context.append(onboarding_action())

    return {
        "systemMessage": "\n" + "\n".join(x for x in shown if x),
        "hookSpecificOutput": {
            "hookEventName": "SessionStart",
            "additionalContext": "\n".join(context),
        },
    }


# ── Usage ledger write side + provider management (Layer 2) ──────────

def _models_provider_for(pid: str) -> str:
    """Map a providers-registry key (codex/gemini/…) to its models.json
    pricing provider (openai/gemini/…). Falls back to the key itself."""
    spec = (load_providers().get("providers") or {}).get(pid) or {}
    return spec.get("models_provider", pid)


def record_usage(
    provider: str,
    model: str,
    tokens_in: int = 0,
    tokens_out: int = 0,
    task: Optional[str] = None,
    session: Optional[str] = None,
    est_usd: Optional[float] = None,
) -> dict:
    """Append one usage event to the USD ledger (append-only NDJSON).

    est_usd is computed from models.json price (USD per 1M tokens) when not
    given. Local/free models price to 0. This is the write side that makes
    estimate_headroom() reflect real accumulated usage.
    """
    if est_usd is None:
        price = model_price(_models_provider_for(provider), model)
        if price:
            ci, co = price
            est_usd = (int(tokens_in or 0) / 1_000_000) * ci + (int(tokens_out or 0) / 1_000_000) * co
        else:
            est_usd = 0.0
    ev = {
        "at": datetime.now().isoformat(timespec="seconds"),
        "provider": provider,
        "model": model,
        "in": int(tokens_in or 0),
        "out": int(tokens_out or 0),
        "est_usd": round(float(est_usd), 6),
    }
    if task:
        ev["task"] = task
    if session:
        ev["session"] = session
    USAGE_LEDGER.parent.mkdir(parents=True, exist_ok=True)
    with open(USAGE_LEDGER, "a", encoding="utf-8") as f:
        f.write(json.dumps(ev, ensure_ascii=False) + "\n")
    return ev


def reset_ledger(archive: bool = True) -> Optional[Path]:
    """Clear the usage ledger (optionally archiving it alongside). Use when a
    budget window resets. Returns the archive path, or None if nothing to clear."""
    if not USAGE_LEDGER.exists():
        return None
    if archive:
        stamp = datetime.now().strftime("%Y%m%d-%H%M%S")
        dest = USAGE_LEDGER.with_name(f"usage-ledger.{stamp}.ndjson")
        n = 1
        while dest.exists():  # never clobber an archive from the same second
            dest = USAGE_LEDGER.with_name(f"usage-ledger.{stamp}.{n}.ndjson")
            n += 1
        USAGE_LEDGER.rename(dest)
        return dest
    USAGE_LEDGER.unlink()
    return None


def set_provider_budget(pid: str, usd: float) -> dict:
    """Set budget_usd for a provider, persisting to the USER providers.json
    (seeded from the bundled example on first write)."""
    if usd < 0:
        raise ValueError("budget must be >= 0")
    data = load_providers()
    provs = data.setdefault("providers", {})
    if pid not in provs:
        raise KeyError(f"unknown provider: {pid} (known: {', '.join(provs) or 'none'})")
    provs[pid]["budget_usd"] = float(usd)
    USER_PROVIDERS.parent.mkdir(parents=True, exist_ok=True)
    _save_json(USER_PROVIDERS, data)
    return provs[pid]


def providers_detail(probe: bool = False) -> dict:
    """Full per-provider view: connection + budget + used + estimated remaining."""
    provs = (load_providers().get("providers") or {})
    summ = usage_summary()
    rows = []
    for pid, spec in provs.items():
        pr = probe_provider(pid, spec, do_call=probe)
        rows.append({
            **pr,
            "headroom": estimate_headroom(pid, spec, summ),
            "usage": summ.get(pid) or {"in": 0, "out": 0, "usd": 0.0, "events": 0},
            "budget_usd": spec.get("budget_usd"),
            "models_provider": spec.get("models_provider", pid),
        })
    total = round(sum(float((v or {}).get("usd") or 0) for v in summ.values()), 4)
    return {"at": datetime.now().isoformat(timespec="minutes"),
            "providers": rows, "total_usd": total,
            "ledger": str(USAGE_LEDGER)}


def format_providers(detail: dict) -> str:
    lines = [f"effi providers — {detail.get('at','')}", ""]
    idw = _id_col_width(detail["providers"])
    lines.append(f"  {'Provider':<{idw + 2}}{_dpad('Connection', 22)}{'Used':<12}{'Budget':<10}Remaining (~추정)")
    lines.append("  " + "─" * idw + "  " + "─" * 20 + "  " + "─" * 10 + "  " + "─" * 8 + "  " + "─" * 16)
    for p in detail["providers"]:
        icon = _CONN_ICON.get(p["connection"], "·")
        hz = p.get("headroom") or {}
        used = f"${float((p.get('usage') or {}).get('usd') or 0):.2f}"
        budget = f"${p['budget_usd']:.0f}" if p.get("budget_usd") else "—"
        if hz.get("remaining_pct") is not None:
            remaining = f"~${hz['remaining_usd']:.2f} ({hz['remaining_pct']:.0f}%)"
        elif hz.get("kind") == "free":
            remaining = "무료(로컬)"
        elif hz.get("kind") == "subscription":
            remaining = "~여유(구독)"
        else:
            remaining = "~예산미설정"
        ev = (p.get("usage") or {}).get("events") or 0
        lines.append(f"  {icon} {p['id']:<{idw}}{_dpad(p['detail'], 22)}{used:<12}{budget:<10}{remaining}  · {ev}건")
    lines += [
        "",
        f"  누적 추정: ${detail.get('total_usd', 0):.2f}   |   원장: {detail.get('ledger')}",
        "  예산 설정: effi providers budget <id> <usd>   |   초기화: effi providers reset",
    ]
    return "\n".join(lines)


# ── Live advisor: statusline + mode nudge (Layer 3) ──────────────────

def statusline_text(model: Optional[str] = None, cwd: Optional[str] = None) -> str:
    """One-line status for a Claude Code statusLine: mode · active model ·
    tightest provider headroom. `model` overrides the mode's default (the
    statusLine payload knows the real active model)."""
    m = get_mode()
    parts = [f"{m.get('emoji','')} {m.get('id','?')}".strip()]
    if not model:
        pol = m.get("policy") or {}
        model = pol.get("default_coding_model") or pol.get("coding_ceiling_model")
    if model:
        parts.append(model)
    try:
        provs = load_providers().get("providers") or {}
        summ = usage_summary()
        flags = []
        for pid, spec in provs.items():
            hz = estimate_headroom(pid, spec, summ)
            if hz.get("remaining_pct") is not None:
                tag = f"{pid} ~${hz['remaining_usd']:.0f}/{hz['budget_usd']:.0f}"
                if hz["remaining_pct"] < 15:
                    tag = "⚠ " + tag
                flags.append(tag)
        if flags:
            parts.append(" ".join(flags[:2]))
    except Exception:
        pass  # statusline must never fail a session
    return " · ".join(parts)


@contextmanager
def _state_lock():
    """Exclusive lock around a state.json read-modify-write. statusLine is the
    highest-frequency writer in the toolkit, so unsynchronized load→mutate→save
    would let concurrent windows lose each other's updates. Best-effort: on a
    platform without fcntl the body still runs (no lock)."""
    lock_path = DEFAULT_STATE.with_name(DEFAULT_STATE.name + ".lock")
    try:
        lock_path.parent.mkdir(parents=True, exist_ok=True)
        import fcntl
        f = open(lock_path, "w")
    except Exception:
        yield  # locking unavailable — degrade to unlocked (still correct single-proc)
        return
    try:
        fcntl.flock(f, fcntl.LOCK_EX)
        yield
    finally:
        try:
            fcntl.flock(f, fcntl.LOCK_UN)
        finally:
            f.close()


def record_session_cost(session_id: Optional[str], total_cost_usd, model: Optional[str] = None) -> dict:
    """Capture real Claude session spend into the ledger from the statusLine
    payload's ``cost.total_cost_usd`` — a STABLE official field (unlike the
    transcript, which the docs say may change format between versions). Dedupes
    via a per-session high-water mark so repeated statusLine refreshes only
    record the increment.
    """
    try:
        total = float(total_cost_usd)
    except (TypeError, ValueError):
        return {"recorded": False, "reason": "no cost"}
    with _state_lock():
        st = load_state()
        marks = st.setdefault("session_cost", {})
        sess = session_id or "default"
        last = float(marks.get(sess) or 0.0)
        delta = total - last
        if delta <= 1e-6:  # no new spend since last refresh (or a reset/decrease)
            return {"recorded": False, "delta": 0.0}
        record_usage("claude", model or "claude", est_usd=round(delta, 6),
                     task="session", session=sess)
        marks.pop(sess, None)  # re-insert at end so dict order == recency
        marks[sess] = total
        if len(marks) > 20:  # bound growth: keep the 20 most-recently-touched
            for k in list(marks)[:-20]:
                marks.pop(k, None)
        st["session_cost"] = marks
        save_state(st)
    return {"recorded": True, "delta": round(delta, 6)}


def statusline_from_payload(payload: dict) -> str:
    """Render a Claude Code statusLine from its stdin payload AND capture the real
    Claude session cost into the ledger. Everything is best-effort — a statusLine
    must never fail the session.
    """
    model_id = None
    cost = payload.get("cost") or {}
    try:
        mo = payload.get("model") or {}
        model_id = mo.get("id") or mo.get("display_name")
        if cost.get("total_cost_usd") is not None:
            record_session_cost(payload.get("session_id"), cost.get("total_cost_usd"), model_id)
    except Exception:
        pass

    parts = []
    m = get_mode()
    parts.append(f"{m.get('emoji','')} {m.get('id','?')}".strip())
    if model_id:
        parts.append(model_id)
    try:
        if cost.get("total_cost_usd") is not None:
            parts.append(f"${float(cost['total_cost_usd']):.2f}")
    except Exception:
        pass
    try:  # real Claude subscription headroom (5h window), if surfaced
        rl = (payload.get("rate_limits") or {}).get("five_hour") or {}
        if rl.get("used_percentage") is not None:
            parts.append(f"claude {max(0, 100 - float(rl['used_percentage'])):.0f}%↑5h")
    except Exception:
        pass
    try:  # one non-claude budgeted provider, tightest first
        provs = load_providers().get("providers") or {}
        summ = usage_summary()
        cand = []
        for pid, spec in provs.items():
            if pid == "claude":
                continue
            hz = estimate_headroom(pid, spec, summ)
            if hz.get("remaining_pct") is not None:
                cand.append((hz["remaining_pct"], pid, hz))
        if cand:
            _, pid, hz = min(cand)
            tag = f"{pid} ~${hz['remaining_usd']:.0f}/{hz['budget_usd']:.0f}"
            if hz["remaining_pct"] < 15:
                tag = "⚠ " + tag
            parts.append(tag)
    except Exception:
        pass
    return " · ".join(parts)


def nudge(task_hint: str, session: Optional[str] = None, min_turn_gap: int = 5) -> dict:
    """Per-turn mode advisor. Suggests a mode switch when the task's importance
    band doesn't fit the active mode, throttled to at most once per `min_turn_gap`
    turns per session. State persists in ~/.config/effi/state.json under "nudge".
    """
    imp = assess_task_importance(task_hint or "")
    cur = get_mode()
    fit = mode_fit(cur["id"], imp["suggested_mode"], imp["band"])

    with _state_lock():
        st = load_state()
        nd = st.setdefault("nudge", {})
        sess = session or "default"
        ss = nd.get(sess) or {"turns": 0, "last_nudge_turn": -(10 ** 9)}
        ss["turns"] += 1
        turn = ss["turns"]

        suggestion = None
        if not fit.get("ok") and (turn - ss["last_nudge_turn"]) >= min_turn_gap:
            suggestion = {
                "suggest_mode": imp["suggested_mode"],
                "current_mode": cur["id"],
                "band": imp["band"],
                "mismatch": fit.get("mismatch"),
                "reason": imp["reason"],
            }
            ss["last_nudge_turn"] = turn

        nd.pop(sess, None)  # re-insert at end so dict order == recency
        nd[sess] = ss
        if len(nd) > 8:  # keep the 8 most-recently-touched sessions
            for k in list(nd)[:-8]:
                nd.pop(k, None)
        st["nudge"] = nd
        save_state(st)
    return {"turn": turn, "suggestion": suggestion, "fit": fit,
            "importance": imp, "current_mode": cur}


def format_nudge(result: dict) -> str:
    """Render a nudge suggestion as a short context line (empty if none)."""
    s = result.get("suggestion")
    if not s:
        return ""
    modes = {m["id"]: m for m in list_modes()}
    tgt = modes.get(s["suggest_mode"], {})
    curm = modes.get(s["current_mode"], {})
    arrow = "⬆" if s.get("mismatch") == "underpowered" else "⬇"
    return (
        f"[effi] {arrow} 이 작업({s['band']})엔 "
        f"{curm.get('emoji','')} {s['current_mode']}보다 "
        f"{tgt.get('emoji','')} {s['suggest_mode']}가 적합 — {s['reason']}. "
        f"전환: effi mode set {s['suggest_mode']}"
    )


def format_mode_suggestion(task_text: str, probe: bool = False) -> str:
    """Given what the user is about to work on, recommend a mode — combining task
    importance (band/domain/grade) with live provider connection + estimated
    headroom (via preflight) — and lay out all three choices so the user can
    accept the pick or override it."""
    pf = preflight(probe=probe, task_hint=task_text)
    imp = assess_task_importance(task_text or "")
    rec = pf["mode_recommendation"]
    cur = pf["current_mode"]
    modes = {m["id"]: m for m in list_modes()}
    rm = modes.get(rec["mode"], {})
    lines = [
        f"작업: {task_text}",
        f"  분석: 중요도 {imp['band']} · 영역 {imp['domain']} · 등급 {imp['grade']}",
        "",
        f"  추천 모드 → {rm.get('emoji','')} {rm.get('name', rec['mode'])} ({rec['mode']})",
        f"  근거: {rec['reason']}",
        f"  현재: {cur.get('emoji','')} {cur.get('name')} ({cur.get('id')}) · source={cur.get('source')}",
        "",
        "  선택지:",
    ]
    for m in list_modes():
        star = "→" if m["id"] == rec["mode"] else " "
        lines.append(f"   {star} [{m['number']}] {m.get('emoji','')} {m['name']:7} {m.get('tagline')}")
    lines += ["", f"  고정: effi mode set <apex|cruise|sip>   (추천: effi mode set {rec['mode']})"]
    return "\n".join(lines)


def hooks_snippet() -> dict:
    """The Claude Code settings.json fragment that wires effi's statusLine + hooks
    (absolute paths to this install's bin/)."""
    b = str(ROOT / "bin")
    return {
        "statusLine": {"type": "command", "command": f"{b}/effi-statusline"},
        "hooks": {
            "SessionStart": [
                {"hooks": [{"type": "command", "command": f"{b}/effi-hook-session-start"}]}
            ],
            "UserPromptSubmit": [
                {"hooks": [{"type": "command", "command": f"{b}/effi-hook-prompt"}]}
            ],
        },
    }


def hooks_installed(settings_paths: Optional[list] = None) -> dict:
    """Is effi's SessionStart hook wired into Claude Code?

    The launch screen renders from that hook (Claude Code clears the terminal
    on start, so anything the launcher prints beforehand is never seen). If the
    hook is missing the user gets no screen at all — the launcher says so.
    """
    if settings_paths is None:
        proj = project_root()
        settings_paths = [
            Path(os.path.expanduser("~/.claude/settings.json")),
            proj / ".claude" / "settings.json",
            proj / ".claude" / "settings.local.json",
        ]
    found = []
    for p in settings_paths:
        try:
            text = Path(p).read_text(encoding="utf-8")
        except Exception:
            continue
        if "effi-hook-session-start" in text:
            found.append(str(p))
    return {"session_start": bool(found), "paths": found}


def install_hooks(settings_path: Optional[str] = None) -> dict:
    """Merge effi's statusLine + hooks into a Claude Code settings.json without
    clobbering existing keys. Backs up the original first. Idempotent — running
    twice adds nothing the second time.
    """
    sp = Path(settings_path or os.path.expanduser("~/.claude/settings.json"))
    original_text = sp.read_text(encoding="utf-8") if sp.exists() else None
    try:
        data = json.loads(original_text) if original_text else {}
        if not isinstance(data, dict):
            raise ValueError
    except Exception:
        return {"ok": False, "error": f"{sp} is not valid JSON — fix or move it first",
                "path": str(sp)}

    snip = hooks_snippet()
    added = []

    if not data.get("statusLine"):
        data["statusLine"] = snip["statusLine"]
        added.append("statusLine")

    if not isinstance(data.get("hooks"), dict):  # tolerate null / [] / wrong type
        data["hooks"] = {}
    hooks = data["hooks"]
    for ev, entries in snip["hooks"].items():
        cmd = entries[0]["hooks"][0]["command"]
        cur = hooks.setdefault(ev, [])
        if not any(cmd in json.dumps(x, ensure_ascii=False) for x in cur):
            cur.extend(entries)
            added.append(ev)

    if not added:
        return {"ok": True, "added": [], "path": str(sp), "note": "already wired"}

    sp.parent.mkdir(parents=True, exist_ok=True)
    backup = None
    if original_text is not None:
        backup = sp.with_name(sp.name + ".effi-bak")
        n = 1
        while backup.exists():
            backup = sp.with_name(sp.name + f".effi-bak{n}")
            n += 1
        backup.write_text(original_text, encoding="utf-8")
    # atomic write: never leave a half-written settings.json on a crash
    tmp = sp.with_name(sp.name + ".effi-tmp")
    tmp.write_text(json.dumps(data, indent=2, ensure_ascii=False) + "\n", encoding="utf-8")
    tmp.replace(sp)
    return {"ok": True, "added": added, "path": str(sp),
            "backup": str(backup) if backup else None}


# ── CLI helpers ──────────────────────────────────────────────────────

def launch_plan(rec: dict, task_text: str = "") -> dict:
    """How to actually start work for a route recommendation.

    Keeps main-thread cache discipline: Claude lead stays default for coding
    sessions; other providers are for isolated subtasks / bulk.
    """
    prov = rec.get("primary_provider")
    model = rec.get("primary_model")
    domain = rec.get("domain")
    steps = []
    env = {}
    exec_cmd = None
    warning = None

    if prov == "local":
        hint = task_text or domain or "bulk"
        hint_q = hint.replace('"', "'")
        exec_cmd = f'effi run -t "{hint_q}" '
        steps = [
            f"Local model: {model} (RAM-aware pick may differ at runtime)",
            f'Run: effi run -t "{hint_q}" "<your bulk prompt>"',
            "Verify output before applying to the repo",
            "Main Claude session stays open for orchestration (cache)",
        ]
        warning = "Confirm with user before bulk local runs (ORCHESTRATION rule)"
    elif prov == "claude":
        exec_cmd = "effi cloud"
        steps = [
            f"Primary: Claude · {model}",
            "Start main session: effi   # or: effi cloud",
            f"Log TRIAGE: domain={domain} model=claude/{model}",
            "Keep this thread for writes (single-writer)",
        ]
        if rec.get("review") and rec["review"] != "none":
            steps.append("After edits: effi review -o tasks/<job>/workers/review")
    elif prov == "openai":
        exec_cmd = "codex"
        steps = [
            f"Isolated subtask on OpenAI/Codex · {model}",
            "Prefer: keep main session on Claude; run Codex in another pane for this slice",
            f"If using Codex CLI: codex  (set model to {model} in Codex config if needed)",
            "Return summary + file paths only to the Claude lead",
        ]
        warning = "Do not move the main Claude conversation mid-session (cache)"
    elif prov == "gemini":
        exec_cmd = f'gemini -m {model} -p "<your prompt>"'
        steps = [
            f"Isolated subtask on Gemini · {model}",
            "Use for design/multimodal/research slices",
            f'Headless: gemini -m {model} -p "…"   (GEMINI_API_KEY 사용, -o json 가능)',
            "키 없으면 즉시 실패 — 발급/설정: effi connect gemini",
            "Save artifacts under tasks/<job>/workers/<role>/",
            "Summarize back to Claude lead",
        ]
        warning = ("GEMINI_API_KEY(종량제)가 유일한 경로 — 개인용 Login with Google은 "
                   "2026-07 폐기(IneligibleTierError). 구독은 Antigravity IDE 전용")
    elif prov == "grok":
        exec_cmd = "grok"
        steps = [
            f"Isolated subtask on Grok · {model}",
            "Good for realtime research (enable search tools) or value coding",
            "Return paths + short summary to Claude lead",
        ]
    else:
        steps = [f"Unknown provider {prov} — use effi route --json for details"]

    # always suggest alternates briefly
    alts = rec.get("alternates") or []
    if alts:
        steps.append(
            "Alternates: "
            + ", ".join(f"{a.get('provider')}/{a.get('model')}" for a in alts[:3])
        )

    return {
        "provider": prov,
        "model": model,
        "domain": domain,
        "exec_hint": exec_cmd,
        "steps": steps,
        "warning": warning,
        "main_thread": rec.get("main_thread_lock", "claude"),
        "env": env,
    }


def append_task_log(
    task_name: str,
    tag: str,
    message: str,
    project: Optional[Path] = None,
) -> Path:
    """Append one line to tasks/<name>/log.md (creates file if missing)."""
    proj = project or project_root()
    log_path = tasks_dir(proj) / task_name / "log.md"
    log_path.parent.mkdir(parents=True, exist_ok=True)
    now = datetime.now().strftime("%Y-%m-%d %H:%M")
    tag = tag.upper().replace(" ", "_")
    line = f"[{now}] [{tag}] {message}\n"
    if not log_path.exists():
        log_path.write_text(f"# log — {task_name}\n\n<!-- append-only -->\n\n", encoding="utf-8")
    with open(log_path, "a", encoding="utf-8") as f:
        f.write(line)
    return log_path


# ── File-edit delegation (effi-edit) ────────────────────────────────
# Local-tier full-file rewrite with non-destructive sidecar.
# Safety: refuse files larger than DEFAULT_EDIT_MAX_CHARS so small models
# never silently truncate large sources.

DEFAULT_EDIT_MAX_CHARS = int(os.environ.get("EFFI_EDIT_MAX_CHARS", "8000"))


def sidecar_path(path: "Path | str") -> Path:
    """Return <file>.effi-new next to the original."""
    p = Path(path)
    return p.with_name(p.name + ".effi-new")


def strip_code_fences(text: str) -> str:
    """Remove leading/trailing markdown code fences from model output."""
    s = (text or "").strip()
    if not s:
        return s
    # Whole response is one fenced block
    m = re.match(r"^```[\w.+-]*\s*\n([\s\S]*?)\n```\s*$", s)
    if m:
        return m.group(1)
    lines = s.splitlines()
    if lines and lines[0].startswith("```"):
        lines = lines[1:]
        if lines and lines[-1].strip() == "```":
            lines = lines[:-1]
        return "\n".join(lines)
    return s


def check_edit_size(content: str, max_chars: Optional[int] = None) -> dict:
    """Guard against quiet truncation on large files."""
    max_c = DEFAULT_EDIT_MAX_CHARS if max_chars is None else int(max_chars)
    n = len(content or "")
    ok = n <= max_c
    return {
        "ok": ok,
        "chars": n,
        "max_chars": max_c,
        "reason": (
            None
            if ok
            else (
                f"file too large for safe local rewrite ({n} > {max_c} chars); "
                "split the task, raise EFFI_EDIT_MAX_CHARS / --max-chars, or use a cloud mid tier"
            )
        ),
    }


def build_edit_messages(path: "Path | str", content: str, instruction: str) -> list:
    """System+user messages for a full-file rewrite."""
    path_s = str(path)
    system = (
        "You are a careful local coding worker. Rewrite the entire file according to the instruction. "
        "Output ONLY the full new file contents — no markdown fences, no commentary, no preamble."
    )
    user = (
        f"## File path\n{path_s}\n\n"
        f"## Current file contents\n"
        f"{content}\n\n"
        f"## Instruction\n{instruction}\n\n"
        f"Return the complete revised file only."
    )
    return [
        {"role": "system", "content": system},
        {"role": "user", "content": user},
    ]


def unified_diff(
    old: str,
    new: str,
    fromfile: str = "a",
    tofile: str = "b",
) -> str:
    import difflib

    old_lines = (old or "").splitlines(keepends=True)
    new_lines = (new or "").splitlines(keepends=True)
    if old_lines and not old_lines[-1].endswith("\n"):
        old_lines[-1] += "\n"
    if new_lines and not new_lines[-1].endswith("\n"):
        new_lines[-1] += "\n"
    return "".join(
        difflib.unified_diff(old_lines, new_lines, fromfile=fromfile, tofile=tofile)
    )


def write_sidecar(path: "Path | str", new_content: str) -> Path:
    sc = sidecar_path(path)
    sc.write_text(new_content, encoding="utf-8")
    return sc


def apply_sidecar(path: "Path | str") -> dict:
    """Overwrite original with existing <file>.effi-new."""
    p = Path(path)
    sc = sidecar_path(p)
    if not sc.is_file():
        raise FileNotFoundError(f"no sidecar: {sc}")
    if not p.is_file():
        raise FileNotFoundError(f"no original: {p}")
    new = sc.read_text(encoding="utf-8")
    p.write_text(new, encoding="utf-8")
    return {"path": str(p.resolve()), "sidecar": str(sc.resolve()), "chars": len(new)}


def ollama_chat(
    model: str,
    messages: list,
    url: Optional[str] = None,
    temperature: float = 0.2,
    timeout: int = 600,
    record: Optional[dict] = None,
) -> str:
    """Call Ollama /api/chat; return assistant content text.

    If ``record`` is given (e.g. {"provider": "local", "task": "effi-edit"}),
    append a usage event with the token counts Ollama reports. Local models are
    free, so est_usd is 0 — this captures token *volume* for transparency. This
    is the one cloud-vs-local call site effi runs in-process; cloud CLI sessions
    are exec'd away and are captured via P3 hooks instead.
    """
    if url is None:
        try:
            url = (load_config().get("local") or {}).get("ollama_url")
        except Exception:
            url = None
    base = (url or "http://localhost:11434").rstrip("/")
    body = {
        "model": model,
        "messages": messages,
        "stream": False,
        "keep_alive": "30m",
        "options": {"temperature": temperature},
    }
    req = urllib.request.Request(
        base + "/api/chat",
        data=json.dumps(body).encode(),
        headers={"content-type": "application/json"},
    )
    with urllib.request.urlopen(req, timeout=timeout) as r:
        d = json.load(r)
    if record:
        try:
            record_usage(
                provider=record.get("provider", "local"),
                model=record.get("model", model),
                tokens_in=int(d.get("prompt_eval_count") or 0),
                tokens_out=int(d.get("eval_count") or 0),
                task=record.get("task"),
                session=record.get("session"),
            )
        except Exception:
            pass  # usage recording must never break the actual edit
    msg = d.get("message") or {}
    return (msg.get("content") or d.get("response") or "").rstrip()


def print_json(data: Any) -> None:
    json.dump(data, sys.stdout, indent=2, ensure_ascii=False)
    sys.stdout.write("\n")


def print_doctor(report: dict) -> None:
    print(f"effi-code v{report.get('version')}  doctor")
    print(f"toolkit: {report.get('toolkit')}")
    print(f"project: {report.get('project')}")
    print()
    for c in report.get("checks") or []:
        mark = "✅" if c.get("ok") else "❌"
        print(f"{mark} {c['name']}: {c.get('detail')}")
    print()
    print("overall:", "OK" if report.get("ok") else "ISSUES — see ❌ above")
