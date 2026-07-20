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

ROOT = Path(__file__).resolve().parent.parent
CATALOG_MODELS = ROOT / "catalog" / "models.json"
CATALOG_ROUTING = ROOT / "catalog" / "task-routing.json"
CONFIG_DIR = Path(os.path.expanduser("~/.config/effi"))
DEFAULT_CONFIG = CONFIG_DIR / "config.json"
DEFAULT_ACCOUNTS = CONFIG_DIR / "accounts.json"
DEFAULT_STATE = CONFIG_DIR / "state.json"


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
    total = int(subprocess.check_output(["sysctl", "-n", "hw.memsize"])) / 1024**3
    vm = subprocess.check_output(["vm_stat"]).decode()
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


def recommend(text: str, prefer_local: bool = False, cfg: Optional[dict] = None) -> dict:
    cfg = cfg or load_config()
    catalog = load_catalog()
    routing = load_routing()
    cls = classify_domain(text)
    d = cls["domain_spec"]

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
    return out


def format_route(rec: dict, compact: bool = False) -> str:
    lines = []
    if not compact:
        lines.append(
            f"# ROUTING  domain={rec['domain']} ({rec.get('label')})  "
            f"grade={rec['grade']}  conf={rec['confidence']}"
        )
    lines.append(
        f"primary: {rec['primary_provider']}/{rec['primary_model']}  "
        f"cost≈{rec['estimated_relative_cost']}  tier={rec['start_tier']}"
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
    return {"active_account_id": None, "history": []}


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
    """Pick active Claude account under usage threshold; rotate if needed."""
    data = load_accounts()
    accounts = [a for a in (data.get("accounts") or []) if a.get("enabled", True)]
    accounts = [a for a in accounts if a.get("provider", "claude") == "claude"]
    accounts.sort(key=lambda a: a.get("priority", 99))
    thr = get_threshold()
    state = load_state()

    if force_id:
        for a in accounts:
            if a["id"] == force_id:
                return _activate(a, state, reason="forced")
        raise KeyError(force_id)

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
    if not accounts:
        return {
            "account": None,
            "switched": False,
            "threshold": thr,
            "reason": "no_accounts",
            "hint": f"Copy config/accounts.example.json → {DEFAULT_ACCOUNTS}",
        }
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


# ── CLI helpers ──────────────────────────────────────────────────────

def print_json(data: Any) -> None:
    json.dump(data, sys.stdout, indent=2, ensure_ascii=False)
    sys.stdout.write("\n")
