"""z0int root CLI.

Deterministic lifecycle. Agents should invoke these commands instead of
reproducing setup steps from README memory.

Future compiler stack (library modules still usable via python -m):
  z0int routine|cascade|aodl|repair|abab …
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


def _bool_opt(s: str) -> bool:
    return s.lower() in ("1", "true", "yes", "y")


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


def cmd_receipt_emit(*, args: argparse.Namespace) -> int:
    from .receipt import append_receipt, build_receipt

    r = build_receipt(
        trace_id=args.trace_id,
        session_id=args.session_id,
        capability_id=args.capability_id,
        provider=args.provider,
        model=args.model,
        prediction=args.prediction,
        confidence=args.confidence,
        action_taken=args.action,
        route=args.route,
        execution=args.execution,
        latency_ms=args.latency_ms,
        input_tokens=args.input_tokens,
        output_tokens=args.output_tokens,
        baseline_input_tokens=args.baseline_in,
        baseline_output_tokens=args.baseline_out,
        estimated_frontier_tokens_avoided=args.avoided,
    )
    row = append_receipt(r)
    print(json.dumps(row, indent=2, default=str))
    return 0


def cmd_receipt_join(*, args: argparse.Namespace) -> int:
    from .receipt import Outcome, join_outcome

    def flag(name: str) -> bool | None:
        v = getattr(args, name, None)
        return v if v is not None else None

    oc = Outcome(
        execution_completed=flag("execution_completed"),
        verified_success=flag("verified_success"),
        verified=flag("verified"),
        success=flag("success"),
        tool_ok=flag("tool_ok"),
        test_pass=flag("test_pass"),
        task_done=flag("task_done"),
        user_correction=flag("user_correction"),
        reverted=flag("reverted"),
        verifier_ok=flag("verifier_ok"),
        ci_failed=flag("ci_failed"),
        pr_merged=flag("pr_merged"),
        note=args.note,
        source=args.source or "cli",
        verification_source=getattr(args, "verification_source", None),
    )
    joined = join_outcome(args.trace_id, oc)
    print(json.dumps(joined, indent=2, default=str))
    return 0 if joined else 1


def cmd_receipt_close(*, args: argparse.Namespace) -> int:
    from .receipt import Outcome, close_turn

    def flag(name: str) -> bool | None:
        v = getattr(args, name, None)
        return v if v is not None else None

    oc = None
    if any(
        flag(n) is not None
        for n in (
            "execution_completed",
            "verified_success",
            "verified",
            "success",
            "tool_ok",
            "test_pass",
            "task_done",
            "user_correction",
            "reverted",
            "verifier_ok",
            "ci_failed",
            "pr_merged",
        )
    ) or args.note:
        oc = Outcome(
            execution_completed=flag("execution_completed"),
            verified_success=flag("verified_success"),
            verified=flag("verified"),
            success=flag("success"),
            tool_ok=flag("tool_ok"),
            test_pass=flag("test_pass"),
            task_done=flag("task_done"),
            user_correction=flag("user_correction"),
            reverted=flag("reverted"),
            verifier_ok=flag("verifier_ok"),
            ci_failed=flag("ci_failed"),
            pr_merged=flag("pr_merged"),
            note=args.note,
            source=args.source or "cli",
            verification_source=getattr(args, "verification_source", None),
        )
    closed = close_turn(
        args.trace_id,
        measured_frontier_tokens=args.measured,
        input_tokens=args.input_tokens,
        output_tokens=args.output_tokens,
        cached_input_tokens=args.cached_input_tokens,
        latency_ms=args.latency_ms,
        provider=args.provider,
        model=args.model,
        outcome=oc,
        source=args.source or "cli",
    )
    print(json.dumps(closed, indent=2, default=str))
    return 0

def cmd_receipt_summary(*, as_json: bool) -> int:
    from .receipt import summarize_tokenomics

    s = summarize_tokenomics()
    _print(
        s,
        as_json=as_json,
        human=(
            "z0int receipts\n"
            f"  rows={s.get('rows')}  avoided_est={s.get('frontier_tokens_avoided_est')}\n"
            f"  with_outcome={s.get('rows_with_outcome')}  caps={s.get('by_capability')}"
        ),
    )
    return 0

def cmd_receipt_scrub(*, as_json: bool, dry_run: bool) -> int:
    from .receipt import scrub_contaminated_outcomes

    s = scrub_contaminated_outcomes(dry_run=dry_run)
    _print(
        s,
        as_json=as_json,
        human=(
            "z0int outcome scrub\n"
            f"  scanned={s.get('scanned_joins')} unique={s.get('unique_traces')}\n"
            f"  contaminated={s.get('contaminated_latest')} "
            f"rewritten={s.get('rewritten')} dry_run={s.get('dry_run')}"
        ),
    )
    return 0




def build_parser() -> argparse.ArgumentParser:
    p = argparse.ArgumentParser(
        prog="z0int",
        description=(
            "Personal intelligence lifecycle — onboard, doctor, status, models, "
            "receipt, routine/cascade/aodl/repair/abab"
        ),
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

    rc = sub.add_parser("receipt", help="Decision receipt spine (trace → outcome → tokens)")
    rc_sub = rc.add_subparsers(dest="receipt_cmd", required=True)
    re = rc_sub.add_parser("emit", help="Append a decision receipt")
    _json_flag(re)
    re.add_argument("--trace-id", dest="trace_id", default=None)
    re.add_argument("--session-id", dest="session_id", default=None)
    re.add_argument("--capability-id", dest="capability_id", default=None)
    re.add_argument("--provider", default=None)
    re.add_argument("--model", default=None)
    re.add_argument("--prediction", default=None)
    re.add_argument("--confidence", type=float, default=None)
    re.add_argument("--action", default=None)
    re.add_argument("--route", default=None)
    re.add_argument("--execution", default="log_only")
    re.add_argument("--latency-ms", dest="latency_ms", type=float, default=None)
    re.add_argument("--input-tokens", dest="input_tokens", type=int, default=None)
    re.add_argument("--output-tokens", dest="output_tokens", type=int, default=None)
    re.add_argument("--baseline-in", dest="baseline_in", type=int, default=None)
    re.add_argument("--baseline-out", dest="baseline_out", type=int, default=None)
    re.add_argument("--avoided", type=int, default=None)

    rj = rc_sub.add_parser("join", help="Join world outcome to trace_id")
    _json_flag(rj)
    rj.add_argument("trace_id")
    for name, dest in (
        ("--execution-completed", "execution_completed"),
        ("--verified-success", "verified_success"),
        ("--success", "success"),
        ("--verified", "verified"),
        ("--tool-ok", "tool_ok"),
        ("--test-pass", "test_pass"),
        ("--task-done", "task_done"),
        ("--user-correction", "user_correction"),
        ("--reverted", "reverted"),
        ("--verifier-ok", "verifier_ok"),
        ("--ci-failed", "ci_failed"),
        ("--pr-merged", "pr_merged"),
    ):
        rj.add_argument(name, dest=dest, type=_bool_opt, default=None)
    rj.add_argument("--note", default=None)
    rj.add_argument("--source", default="cli")
    rj.add_argument("--verification-source", dest="verification_source", default=None)

    rcl = rc_sub.add_parser("close", help="Post-turn: measured tokens + optional outcome join")
    _json_flag(rcl)
    rcl.add_argument("trace_id")
    rcl.add_argument("--measured", type=int, default=None, help="measured frontier tokens total")
    rcl.add_argument("--input-tokens", dest="input_tokens", type=int, default=None)
    rcl.add_argument("--output-tokens", dest="output_tokens", type=int, default=None)
    rcl.add_argument("--cached-input-tokens", dest="cached_input_tokens", type=int, default=None)
    rcl.add_argument("--latency-ms", dest="latency_ms", type=float, default=None)
    rcl.add_argument("--provider", default=None)
    rcl.add_argument("--model", default=None)
    for name, dest in (
        ("--execution-completed", "execution_completed"),
        ("--verified-success", "verified_success"),
        ("--success", "success"),
        ("--verified", "verified"),
        ("--tool-ok", "tool_ok"),
        ("--test-pass", "test_pass"),
        ("--task-done", "task_done"),
        ("--user-correction", "user_correction"),
        ("--reverted", "reverted"),
        ("--verifier-ok", "verifier_ok"),
        ("--ci-failed", "ci_failed"),
        ("--pr-merged", "pr_merged"),
    ):
        rcl.add_argument(name, dest=dest, type=_bool_opt, default=None)
    rcl.add_argument("--note", default=None)
    rcl.add_argument("--source", default="cli")
    rcl.add_argument("--verification-source", dest="verification_source", default=None)

    rs = rc_sub.add_parser("summary", help="Tokenomics rollup from receipts + bridge")
    _json_flag(rs)

    rscrub = rc_sub.add_parser(
        "scrub",
        help="Append corrections for false-gold outcomes (no verification signal)",
    )
    _json_flag(rscrub)
    rscrub.add_argument(
        "--dry-run",
        action="store_true",
        help="Report contaminated rows without rewriting",
    )


    cf = sub.add_parser(
        "counterfactual",
        help="Paired Grok reference cartography (historical mine + non-inferiority)",
    )
    cf_sub = cf.add_subparsers(dest="counterfactual_cmd", required=True)
    cfm = cf_sub.add_parser("mine", help="Mine historical Grok OMP turns into replay snapshots")
    _json_flag(cfm)
    cfm.add_argument(
        "--sessions-root",
        default=None,
        help="OMP sessions root (default: ~/.omp/agent/sessions or symlink target)",
    )
    cfm.add_argument("--limit", type=int, default=5000, help="Max assistant turns to scan")
    cfm.add_argument("--provider-substr", default="xai,grok", help="Comma substrings for reference providers/models")
    cfs = cf_sub.add_parser("summary", help="Replay grade / pair inventory")
    _json_flag(cfs)

    # Future compiler stack — remainder args forwarded to module CLIs.

    be = sub.add_parser("backends", help="DecisionBackend registry (list / doctor / eval)")
    be_sub = be.add_subparsers(dest="backends_cmd", required=True)
    bel = be_sub.add_parser("list", help="List registered backends (no model load)")
    bel.add_argument("--json", action="store_true")
    bed = be_sub.add_parser("doctor", help="Filesystem/config backend health (no load by default)")
    bed.add_argument("--json", action="store_true")
    bed.add_argument("--load", action="store_true", help="Explicitly load weights (GPU)")
    bed.add_argument("--backend", default=None, help="Single backend id/alias")
    bec = be_sub.add_parser("capabilities", help="Show capability metadata")
    bec.add_argument("name", nargs="?", default="nanojev")
    bec.add_argument("--json", action="store_true")
    bee = be_sub.add_parser("eval", help="Run a DecisionRequest JSON through a backend")
    bee.add_argument("--backend", default="nanojev")
    bee.add_argument("--input", required=True, help="Path to request JSON")
    bee.add_argument("--json", action="store_true", default=True)

    for name, help_txt in (
        ("routine", "Compile/apply specialist region routines (→ z0int.routines)"),
        ("cascade", "Optimize specialist cascades for premium tokens (→ z0int.cascade)"),
        ("aodl", "Bind routines/cascades into AODL strategy docs (→ z0int.aodl)"),
        ("repair", "Counterexample-driven routine repair (→ z0int.refinement)"),
        ("abab", "ABAB experiment archive helpers (→ z0int.abab)"),
    ):
        sp = sub.add_parser(name, help=help_txt)
        sp.add_argument(
            "module_argv",
            nargs=argparse.REMAINDER,
            help=f"Arguments for the {name} subcommand (see z0int {name} -h)",
        )

    return p



def _cmd_backends(args: argparse.Namespace) -> int:
    """backends list|doctor|capabilities|eval — never import torch on list path."""
    from z0int.backends.registry import (
        backend_status,
        create_backend,
        get_backend_spec,
        list_backend_specs,
        register_builtin_backends,
    )

    cmd = args.backends_cmd
    if cmd == "list":
        register_builtin_backends()
        rows = []
        for spec in list_backend_specs():
            # Cheap health: factory must not load GPU; health(load=False) is FS only.
            try:
                h = spec.factory().health(load=False)
                rows.append(
                    {
                        "id": spec.id,
                        "kind": spec.kind,
                        "local": spec.local,
                        "configured": h.configured,
                        "ready": h.ready,
                        "model": h.model,
                        "detail": h.detail,
                    }
                )
            except Exception as exc:  # noqa: BLE001
                rows.append(
                    {
                        "id": spec.id,
                        "kind": spec.kind,
                        "local": spec.local,
                        "configured": False,
                        "ready": False,
                        "model": None,
                        "detail": f"{type(exc).__name__}: {exc}",
                    }
                )
        payload = {"schema": "z0int.backends.v1", "backends": rows}
        if args.json:
            print(json.dumps(payload, indent=2, sort_keys=True))
        else:
            print("id                 kind              ready  model")
            for r in rows:
                print(
                    f"{r['id']:<18} {r['kind']:<16} "
                    f"{'yes' if r['ready'] else 'no':<5} {r.get('model') or '-'}"
                )
                if r.get("detail"):
                    print(f"  {r['detail']}")
        return 0

    if cmd == "doctor":
        rows = backend_status(args.backend, load=bool(args.load))
        payload = {
            "schema": "z0int.backends.doctor.v1",
            "load": bool(args.load),
            "backends": rows,
        }
        if args.json:
            print(json.dumps(payload, indent=2, default=str))
        else:
            for r in rows:
                mark = "✓" if r.get("ready") else "○"
                print(f"{mark} {r['id']}: {r.get('detail')}")
                if r.get("checkpoint"):
                    print(f"  checkpoint: {r['checkpoint']}")
        return 0

    if cmd == "capabilities":
        spec = get_backend_spec(args.name)
        from dataclasses import asdict as _asdict

        backend = spec.factory()
        caps = _asdict(backend.capabilities)
        payload = {"schema": "z0int.backends.capabilities.v1", "id": spec.id, "capabilities": caps}
        if args.json:
            print(json.dumps(payload, indent=2))
        else:
            for k, v in caps.items():
                print(f"{k}: {v}")
        return 0

    if cmd == "eval":
        from pathlib import Path

        from z0int.backends.base import request_from_mapping, result_to_dict

        path = Path(args.input).expanduser()
        raw = json.loads(path.read_text(encoding="utf-8"))
        request = request_from_mapping(raw)
        backend = create_backend(args.backend)
        result = backend.evaluate(request)
        payload = result_to_dict(result)
        print(json.dumps(payload, indent=2, default=str))
        return 0

    print(f"unknown backends command: {cmd}", file=sys.stderr)
    return 2



def main(argv: list[str] | None = None) -> int:
    argv = list(sys.argv[1:] if argv is None else argv)
    parser = build_parser()
    args = parser.parse_args(argv)
    as_json = bool(getattr(args, "as_json", False))

    if args.cmd == "doctor":
        return cmd_doctor(as_json=as_json)
    if args.cmd == "status":
        return cmd_status(as_json=as_json)
    if args.cmd == "backends":
        return _cmd_backends(args)

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
    if args.cmd == "receipt":
        if args.receipt_cmd == "emit":
            return cmd_receipt_emit(args=args)
        if args.receipt_cmd == "join":
            return cmd_receipt_join(args=args)
        if args.receipt_cmd == "close":
            return cmd_receipt_close(args=args)
        if args.receipt_cmd == "summary":
            return cmd_receipt_summary(as_json=as_json)
        if args.receipt_cmd == "scrub":
            return cmd_receipt_scrub(
                as_json=as_json,
                dry_run=bool(getattr(args, "dry_run", False)),
            )

    if args.cmd == "counterfactual":
        from .counterfactual import cmd_mine, cmd_summary

        if args.counterfactual_cmd == "mine":
            return cmd_mine(args=args, as_json=as_json)
        if args.counterfactual_cmd == "summary":
            return cmd_summary(args=args, as_json=as_json)

    if args.cmd in ("routine", "cascade", "aodl", "repair", "abab"):
        rest = list(getattr(args, "module_argv", None) or [])
        # argparse REMAINDER keeps a leading "--" when users write: z0int routine -- compile ...
        if rest and rest[0] == "--":
            rest = rest[1:]
        # bare `z0int routine` / `z0int routine -h` → module help
        if not rest or rest in (["-h"], ["--help"]):
            rest = ["--help"]
        if args.cmd == "routine":
            from .routines import _main as _mod_main
        elif args.cmd == "cascade":
            from .cascade import _main as _mod_main
        elif args.cmd == "aodl":
            from .aodl import _main as _mod_main
        elif args.cmd == "repair":
            from .refinement import main as _mod_main
        else:
            from .abab import _main as _mod_main
        return int(_mod_main(rest))

    parser.error(f"unknown command: {args.cmd}")
    return 2


if __name__ == "__main__":
    raise SystemExit(main())
