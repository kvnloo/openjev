"""Resumable z0int onboard.

Deterministic setup lives here. Agents call ``z0int onboard``; they do not
re-invent install steps from README memory.
"""

from __future__ import annotations

import json
import os
import subprocess
import sys
import time
from pathlib import Path
from typing import Any, Callable

from . import doctor, models_mgmt, paths

ONBOARD_SCHEMA = "z0int.onboard.v1"
EVOLUTION_LAB_REPO = os.environ.get(
    "EVOLUTION_LAB_REPO", "https://github.com/kvnloo/evolution-lab.git"
)
EVOLUTION_LAB_REF = os.environ.get("EVOLUTION_LAB_REF", "nightly")


def _repo_root() -> Path:
    return Path(__file__).resolve().parents[2]


def load_state(root: Path | None = None) -> dict[str, Any]:
    p = paths.onboard_state_path(root)
    if not p.is_file():
        return {"schema": ONBOARD_SCHEMA, "steps": {}, "updated_at": None}
    try:
        data = json.loads(p.read_text(encoding="utf-8"))
    except json.JSONDecodeError:
        return {"schema": ONBOARD_SCHEMA, "steps": {}, "updated_at": None}
    data.setdefault("schema", ONBOARD_SCHEMA)
    data.setdefault("steps", {})
    return data


def save_state(state: dict[str, Any], root: Path | None = None) -> Path:
    paths.ensure_layout(root)
    p = paths.onboard_state_path(root)
    state = dict(state)
    state["schema"] = ONBOARD_SCHEMA
    state["updated_at"] = time.time()
    p.write_text(json.dumps(state, indent=2) + "\n", encoding="utf-8")
    return p


def _mark(state: dict[str, Any], step: str, status: str, detail: str | None = None) -> None:
    entry: dict[str, Any] = {"status": status, "ts": time.time()}
    if detail:
        entry["detail"] = detail
    state.setdefault("steps", {})[step] = entry


def _run(cmd: list[str], *, cwd: Path | None = None, env: dict[str, str] | None = None) -> subprocess.CompletedProcess[str]:
    return subprocess.run(
        cmd,
        cwd=str(cwd) if cwd else None,
        env=env,
        text=True,
        capture_output=True,
        check=False,
    )


StepFn = Callable[[dict[str, Any], dict[str, Any]], dict[str, Any]]


def step_layout(state: dict[str, Any], ctx: dict[str, Any]) -> dict[str, Any]:
    layout = paths.ensure_layout()
    _mark(state, "layout", "done", str(layout["root"]))
    return {"ok": True, "root": str(layout["root"])}


def step_doctor(state: dict[str, Any], ctx: dict[str, Any]) -> dict[str, Any]:
    rep = doctor.run_doctor()
    ctx["doctor"] = rep.to_dict()
    _mark(state, "doctor", "done" if rep.ok else "warn", f"ok={rep.ok}")
    return {"ok": rep.ok, "unresolved": rep.unresolved}


def step_models_plan(state: dict[str, Any], ctx: dict[str, Any]) -> dict[str, Any]:
    plan = models_mgmt.plan_models()
    ctx["models_plan"] = plan
    cfg = paths.config_path()
    cfg.parent.mkdir(parents=True, exist_ok=True)
    existing = {}
    if cfg.is_file():
        try:
            existing = json.loads(cfg.read_text(encoding="utf-8"))
        except json.JSONDecodeError:
            existing = {}
    existing["models_plan"] = plan
    cfg.write_text(json.dumps(existing, indent=2) + "\n", encoding="utf-8")
    _mark(state, "models.plan", "done", plan.get("recommendation"))
    return {"ok": True, "plan": plan}


def step_models_sync(state: dict[str, Any], ctx: dict[str, Any]) -> dict[str, Any]:
    if not ctx.get("sync_models"):
        _mark(state, "models.sync", "skipped", "pass --sync-models to download resident HF weights")
        return {"ok": True, "skipped": True}
    plan = ctx.get("models_plan") or models_mgmt.plan_models()
    result = models_mgmt.sync_models(plan=plan, which="resident", dry_run=bool(ctx.get("dry_run")))
    bad = [r for r in result.get("results") or [] if r.get("status") == "error"]
    _mark(state, "models.sync", "done" if not bad else "error", json.dumps(result)[:500])
    return {"ok": not bad, "result": result}


def step_evolution_lab(state: dict[str, Any], ctx: dict[str, Any]) -> dict[str, Any]:
    if ctx.get("skip_evolution_lab"):
        _mark(state, "evolution_lab", "skipped")
        return {"ok": True, "skipped": True}
    info = doctor.discover_evolution_lab()
    dest = Path(info["path"]) if info.get("found") and info.get("path") else _repo_root().parent / "evolution-lab"
    if ctx.get("dry_run"):
        _mark(state, "evolution_lab", "would_run", str(dest))
        return {"ok": True, "dry_run": True, "path": str(dest)}
    if not (dest / ".git").is_dir():
        dest.parent.mkdir(parents=True, exist_ok=True)
        r = _run(["git", "clone", "--branch", EVOLUTION_LAB_REF, EVOLUTION_LAB_REPO, str(dest)])
        if r.returncode != 0:
            # branch might need full clone then checkout
            r2 = _run(["git", "clone", EVOLUTION_LAB_REPO, str(dest)])
            if r2.returncode != 0:
                _mark(state, "evolution_lab", "error", r2.stderr[-400:])
                return {"ok": False, "error": r2.stderr[-400:]}
            _run(["git", "-C", str(dest), "checkout", EVOLUTION_LAB_REF])
    else:
        _run(["git", "-C", str(dest), "fetch", "origin"])
        _run(["git", "-C", str(dest), "checkout", EVOLUTION_LAB_REF])
        _run(["git", "-C", str(dest), "pull", "--ff-only", "origin", EVOLUTION_LAB_REF])
    # editable install into current env
    r = _run([sys.executable, "-m", "pip", "install", "-q", "-e", str(dest)])
    ok = r.returncode == 0
    _mark(state, "evolution_lab", "done" if ok else "error", str(dest) if ok else r.stderr[-400:])
    return {"ok": ok, "path": str(dest), "stderr": r.stderr[-400:] if not ok else None}


def step_omp_links(state: dict[str, Any], ctx: dict[str, Any]) -> dict[str, Any]:
    omp = doctor.discover_omp()
    if not omp.get("omp_home"):
        _mark(state, "omp_plugins", "skipped", "OMP not installed")
        return {"ok": True, "skipped": True, "reason": "no_omp"}
    if ctx.get("dry_run"):
        _mark(state, "omp_plugins", "would_run", json.dumps(omp.get("missing_links")))
        return {"ok": True, "dry_run": True, "missing": omp.get("missing_links")}
    ext_dir = Path(omp["extensions_dir"] or (Path.home() / ".omp" / "agent" / "extensions"))
    ext_dir.mkdir(parents=True, exist_ok=True)
    repo_ext = Path(omp["repo_extensions"] or (_repo_root() / "omp-extensions"))
    linked: list[str] = []
    for child in sorted(repo_ext.iterdir()):
        if not child.is_dir() or child.name.startswith("."):
            continue
        target = ext_dir / child.name
        if target.exists() or target.is_symlink():
            # refresh symlink if pointing elsewhere
            if target.is_symlink() and Path(os.path.realpath(target)) != child.resolve():
                target.unlink()
            else:
                linked.append(child.name)
                continue
        try:
            target.symlink_to(child.resolve(), target_is_directory=True)
            linked.append(child.name)
        except OSError as exc:
            _mark(state, "omp_plugins", "error", str(exc))
            return {"ok": False, "error": str(exc)}
    _mark(state, "omp_plugins", "done", ",".join(linked))
    return {"ok": True, "linked": linked}


def step_data_discover(state: dict[str, Any], ctx: dict[str, Any]) -> dict[str, Any]:
    data = doctor.discover_data_sources()
    ctx["data"] = data
    out = paths.home() / "sources" / "discovered.json"
    out.write_text(json.dumps({"schema": "z0int.sources_discovered.v1", "sources": data}, indent=2) + "\n")
    _mark(state, "data.discover", "done", f"n={len(data)}")
    return {"ok": True, "sources": data, "path": str(out)}


def step_privacy(state: dict[str, Any], ctx: dict[str, Any]) -> dict[str, Any]:
    """Refuse paths that would put personal weights in the repo."""
    bad = [
        _repo_root() / "data" / "next_action" / "champion.npz",
        _repo_root() / "champion.npz",
    ]
    problems = [str(p) for p in bad if p.is_file()]
    gitignore = _repo_root() / ".gitignore"
    gi = gitignore.read_text(encoding="utf-8") if gitignore.is_file() else ""
    need = [".z0int/", "data/private/"]
    missing = [n for n in need if n not in gi]
    ok = not problems
    detail = "ok" if ok else f"personal artifacts in repo: {problems}"
    if missing:
        detail += f"; gitignore missing {missing}"
    _mark(state, "privacy", "done" if ok else "error", detail)
    return {"ok": ok, "problems": problems, "gitignore_missing": missing}


def step_write_env_hints(state: dict[str, Any], ctx: dict[str, Any]) -> dict[str, Any]:
    el = (ctx.get("doctor") or {}).get("discoveries", {}).get("evolution_lab") or doctor.discover_evolution_lab()
    hints = {
        "schema": "z0int.env_hints.v1",
        "Z0INT_HOME": str(paths.home()),
        "EVOLUTION_LAB_ROOT": el.get("path"),
        "EVOLUTION_LAB_PYTHON": sys.executable,
        "note": "Export these for OMP extensions; onboard does not mutate your shell rc.",
    }
    p = paths.home() / "config" / "env_hints.json"
    p.write_text(json.dumps(hints, indent=2) + "\n")
    _mark(state, "env_hints", "done", str(p))
    return {"ok": True, "path": str(p), "hints": hints}


STEPS: list[tuple[str, StepFn]] = [
    ("layout", step_layout),
    ("doctor", step_doctor),
    ("models.plan", step_models_plan),
    ("models.sync", step_models_sync),
    ("evolution_lab", step_evolution_lab),
    ("omp_plugins", step_omp_links),
    ("data.discover", step_data_discover),
    ("privacy", step_privacy),
    ("env_hints", step_write_env_hints),
]


def run_onboard(
    *,
    auto: bool = True,
    force: bool = False,
    dry_run: bool = False,
    sync_models: bool = False,
    skip_evolution_lab: bool = False,
    only: list[str] | None = None,
) -> dict[str, Any]:
    """Run onboard steps. Idempotent: done steps skipped unless force."""
    state = load_state()
    ctx: dict[str, Any] = {
        "auto": auto,
        "dry_run": dry_run,
        "sync_models": sync_models,
        "skip_evolution_lab": skip_evolution_lab or os.environ.get("SKIP_EVOLUTION_LAB") == "1",
    }
    results: dict[str, Any] = {}
    for name, fn in STEPS:
        if only and name not in only:
            continue
        prev = (state.get("steps") or {}).get(name) or {}
        if not force and prev.get("status") == "done" and name != "doctor":
            results[name] = {"ok": True, "skipped": True, "reason": "already_done"}
            continue
        try:
            results[name] = fn(state, ctx)
        except Exception as exc:  # noqa: BLE001
            _mark(state, name, "error", str(exc))
            results[name] = {"ok": False, "error": str(exc)}
        save_state(state)
        if results[name].get("ok") is False and name in {"privacy", "layout"}:
            break
    save_state(state)
    failed = [k for k, v in results.items() if v.get("ok") is False]
    return {
        "schema": "z0int.onboard_report.v1",
        "ok": not failed,
        "failed": failed,
        "results": results,
        "state_path": str(paths.onboard_state_path()),
        "next": _next_actions(results, ctx),
    }


def _next_actions(results: dict[str, Any], ctx: dict[str, Any]) -> list[str]:
    actions: list[str] = []
    if (results.get("models.sync") or {}).get("skipped"):
        actions.append("Optional: z0int models sync  # download resident HF weights")
    data = (results.get("data.discover") or {}).get("sources") or {}
    if not data.get("hermes_state_db") and not data.get("episodes_next_action"):
        actions.append("Provide a harness history (Hermes state.db) then: z0int-compile")
    if not (results.get("evolution_lab") or {}).get("ok") and not (results.get("evolution_lab") or {}).get("skipped"):
        actions.append("Fix evolution-lab install or re-run with network: z0int onboard --force")
    if not actions:
        actions.append("z0int status")
        actions.append("z0int-compile  # if Hermes db present")
        actions.append("python -m evolution_lab capability-mine  # after episodes exist")
    return actions
