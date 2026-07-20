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


def read_project_mode(project: Optional[Path] = None) -> Optional[str]:
    path = project_mode_path(project)
    if not path.is_file():
        # legacy single-file marker
        legacy = (project or project_root()) / ".effi-mode"
        if legacy.is_file():
            path = legacy
        else:
            return None
    try:
        raw = path.read_text(encoding="utf-8").strip().splitlines()
        if not raw:
            return None
        # allow "apex" or "mode: apex"
        line = raw[0].strip()
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
        "next_review_due": cat.get("next_review_due"),
        "stale": catalog_is_stale(cat, cfg),
        "sources": cat.get("sources"),
        "path": str(CATALOG_MODELS),
    }


def bump_catalog_dates() -> dict:
    """Mark catalog as reviewed today; next review +14d. Does not invent new models."""
    cat = load_catalog()
    today = date.today()
    cat["updated_at"] = today.isoformat()
    cat["next_review_due"] = (today + timedelta(days=14)).isoformat()
    # patch version date stamp
    cat["catalog_version"] = today.strftime("%Y.%m.%d")
    _save_json(CATALOG_MODELS, cat)
    # keep routing timestamp in sync
    routing = load_routing()
    routing["updated_at"] = today.isoformat()
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
        checks.append({"name": f"cli:{cmd}", "ok": True, "detail": p or "optional — not found"})

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
        exec_cmd = "gemini"
        steps = [
            f"Isolated subtask on Gemini · {model}",
            "Use for design/multimodal/research slices",
            "Save artifacts under tasks/<job>/workers/<role>/",
            "Summarize back to Claude lead",
        ]
        warning = "Optional CLI; API/AI Studio also fine"
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
) -> str:
    """Call Ollama /api/chat; return assistant content text."""
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
