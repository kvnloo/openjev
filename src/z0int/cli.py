"""z0int root CLI.

Deterministic lifecycle. Agents should invoke these commands instead of
reproducing setup steps from README memory.
"""

from __future__ import annotations

import argparse
import json
import sys
from typing import Any


def _print(data: Any, *, as_json: bool, human: str | None = None) -> None:
    if as_json:
        print(json.dumps(data, indent=2, default=str))
    elif human is not None:
        print(human)
    else:
        print(json.dumps(data, indent=2, default=str))


def cmd_doctor(*, as_json: bool) -> int:
    from .doctor import format_human, run_doctor

    rep = run_doctor()
    _print(rep.to_dict(), as_json=as_json, human=format_human(rep))
    return 0 if rep.ok else 1


def cmd_status(*, as_json: bool) -> int:
    from .status import format_human, run_status

    st = run_status()
    _print(st, as_json=as_json, human=format_human(st))
    return 0 if st.get("ok", True) else 1


def cmd_onboard(
    *,
    auto: bool,
    force: bool,
    dry_run: bool,
    sync_models: bool,
    skip_evolution_lab: bool,
    as_json: bool,
) -> int:
    from .onboard import run_onboard

    report = run_onboard(
        auto=auto,
        force=force,
        dry_run=dry_run,
        sync_models=sync_models,
        skip_evolution_lab=skip_evolution_lab,
    )
    if as_json:
        print(json.dumps(report, indent=2, default=str))
    else:
        print("z0int onboard")
        print(f"  ok={report.get('ok')}  state={report.get('state_path')}")
        for name, res in (report.get("results") or {}).items():
            mark = "✓" if res.get("ok") and not res.get("error") else ("○" if res.get("skipped") else "✗")
            extra = " skipped" if res.get("skipped") else ""
            err = res.get("error")
            print(f"  {mark} {name}{extra}" + (f"  {err}" if err else ""))
        nxt = report.get("next") or []
        if nxt:
            print("\nNext:")
            for a in nxt:
                print(f"  - {a}")
    return 0 if report.get("ok") else 1


def cmd_models_plan(*, as_json: bool) -> int:
    from .models_mgmt import plan_models

    plan = plan_models()
    if as_json:
        print(json.dumps(plan, indent=2, default=str))
    else:
        print("z0int models plan")
        print(f"  gpu: {plan.get('gpu_name')}  vram_gb={plan.get('vram_gb')}")
        print(f"  {plan.get('recommendation')}")
        print(f"  resident: {plan.get('resident')}")
        print(f"  on_demand: {plan.get('on_demand')}")
    return 0


def cmd_models_sync(*, which: str, dry_run: bool, as_json: bool) -> int:
    from .models_mgmt import sync_models

    result = sync_models(which=which, dry_run=dry_run)
    _print(result, as_json=as_json)
    bad = [r for r in result.get("results") or [] if r.get("status") == "error"]
    return 1 if bad else 0


def cmd_data_discover(*, as_json: bool) -> int:
    from . import paths
    from .doctor import discover_data_sources

    data = discover_data_sources()
    out = {
        "schema": "z0int.sources_discovered.v1",
        "sources": data,
        "home": str(paths.home()),
    }
    paths.ensure_layout()
    dest = paths.home() / "sources" / "discovered.json"
    dest.write_text(json.dumps(out, indent=2) + "\n", encoding="utf-8")
    out["path"] = str(dest)
    _print(out, as_json=as_json)
    return 0


def build_parser() -> argparse.ArgumentParser:
    p = argparse.ArgumentParser(
        prog="z0int",
        description="Personal intelligence lifecycle — onboard, doctor, status, models",
    )
    p.add_argument("--json", action="store_true", dest="as_json", help="Machine-readable JSON output")
    sub = p.add_subparsers(dest="cmd", required=True)

    def _json_flag(sp: argparse.ArgumentParser) -> None:
        sp.add_argument("--json", action="store_true", dest="as_json", help="Machine-readable JSON output")

    d = sub.add_parser("doctor", help="Inspect hardware, deps, harnesses, data sources")
    _json_flag(d)
    s = sub.add_parser("status", help="Product status dashboard")
    _json_flag(s)

    on = sub.add_parser("onboard", help="Idempotent setup pipeline (safe defaults)")
    _json_flag(on)
    on.add_argument("--auto", action="store_true", default=True, help="Non-interactive safe path (default)")
    on.add_argument("--force", action="store_true", help="Re-run steps marked done")
    on.add_argument("--dry-run", action="store_true", help="Plan only; no clone/pip/symlink/download")
    on.add_argument("--sync-models", action="store_true", help="Download resident HF weights")
    on.add_argument("--skip-evolution-lab", action="store_true")

    ms = sub.add_parser("models", help="Model plan / sync")
    ms_sub = ms.add_subparsers(dest="models_cmd", required=True)
    mp = ms_sub.add_parser("plan", help="Hardware-aware resident / on-demand plan")
    _json_flag(mp)
    mss = ms_sub.add_parser("sync", help="Download planned HF models")
    _json_flag(mss)
    mss.add_argument("--which", choices=("resident", "on_demand", "all"), default="resident")
    mss.add_argument("--dry-run", action="store_true")

    data = sub.add_parser("data", help="Data source helpers")
    data_sub = data.add_subparsers(dest="data_cmd", required=True)
    dd = data_sub.add_parser("discover", help="Find Hermes/OMP/Codex/export paths")
    _json_flag(dd)

    return p


def main(argv: list[str] | None = None) -> int:
    argv = list(sys.argv[1:] if argv is None else argv)
    parser = build_parser()
    args = parser.parse_args(argv)
    # Parent and/or subcommand may set as_json
    as_json = bool(getattr(args, "as_json", False))

    if args.cmd == "doctor":
        return cmd_doctor(as_json=as_json)
    if args.cmd == "status":
        return cmd_status(as_json=as_json)
    if args.cmd == "onboard":
        return cmd_onboard(
            auto=bool(getattr(args, "auto", True)),
            force=bool(getattr(args, "force", False)),
            dry_run=bool(getattr(args, "dry_run", False)),
            sync_models=bool(getattr(args, "sync_models", False)),
            skip_evolution_lab=bool(getattr(args, "skip_evolution_lab", False)),
            as_json=as_json,
        )
    if args.cmd == "models":
        if args.models_cmd == "plan":
            return cmd_models_plan(as_json=as_json)
        if args.models_cmd == "sync":
            return cmd_models_sync(
                which=getattr(args, "which", "resident"),
                dry_run=bool(getattr(args, "dry_run", False)),
                as_json=as_json,
            )
    if args.cmd == "data":
        if args.data_cmd == "discover":
            return cmd_data_discover(as_json=as_json)
    parser.error(f"unknown command: {args.cmd}")
    return 2


if __name__ == "__main__":
    raise SystemExit(main())
