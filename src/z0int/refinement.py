"""Counterexample-driven routine repair, with prospective credit.

Search can only *narrow* a demoted parent's region. It never changes the output,
uses a negative-only exclusion rule, or receives a sealed/future example.
A private trial store enforces one proposal per evaluation cohort across restarts.
This is an offline component, not a harness hook, provider gateway, or sandbox.
"""
from __future__ import annotations

import argparse
import copy
import hashlib
import json
import math
import sqlite3
import time
from collections import defaultdict
from dataclasses import asdict, dataclass, replace
from pathlib import Path
from typing import Any, Callable, Mapping, Sequence

from .routines import (
    CompileConfig, LiteralPredicate, RoutineCandidate, RoutineMetrics,
    RoutineRegistry, RoutineRule, _candidate_literals, evaluate_rule, wilson_lower,
)

SCHEMA = "z0int.routine_repair.v1"
RECEIPT_SCHEMA = "z0int.repair_credit.v1"
_EVIDENCE = {"L2_outcome", "L3_closed_loop"}


def canonical(value: Any) -> str:
    return json.dumps(value, sort_keys=True, separators=(",", ":"), allow_nan=False)


def digest(value: Any) -> str:
    return hashlib.sha256(canonical(value).encode()).hexdigest()


def _number(value: Any, name: str) -> float:
    if isinstance(value, bool) or not isinstance(value, (int, float)) or not math.isfinite(value):
        raise ValueError(f"{name} must be a finite number")
    return float(value)


def _scalar(value: Any) -> bool:
    return value is None or type(value) in (str, bool, int) or (
        type(value) is float and math.isfinite(value)
    )


def _same(a: Any, b: Any) -> bool:
    # Unlike Python ==, the wire contract must distinguish true from 1.
    return canonical(a) == canonical(b)


@dataclass(frozen=True)
class RepairConfig:
    # Explicit pre-decision allowlist. Never discover on every receipt field.
    allowed_features: tuple[str, ...]
    precision_floor: float = 0.95
    min_train_matches: int = 20
    min_dev_matches: int = 20
    min_open_sessions: int = 3
    min_counterexamples: int = 3
    min_counterexample_sessions: int = 2
    min_eval_matches: int = 50
    min_eval_sessions: int = 20
    min_retained_coverage: float = 0.05
    max_session_share: float = 0.10
    max_guards: int = 64
    max_rule_depth: int = 4

    def __post_init__(self) -> None:
        if not self.allowed_features or any(not isinstance(x, str) or not x for x in self.allowed_features):
            raise ValueError("allowed_features must name pre-decision features")
        if len(set(self.allowed_features)) != len(self.allowed_features):
            raise ValueError("duplicate allowed feature")
        if set(self.allowed_features) & {"target", "outcome", "verified", "evidence_level", "verifier_id"}:
            raise ValueError("label/outcome fields cannot be repair features")
        for name in ("precision_floor", "min_retained_coverage", "max_session_share"):
            if not 0 < _number(getattr(self, name), name) <= 1:
                raise ValueError(f"{name} must be in (0, 1]")
        for name in ("min_train_matches", "min_dev_matches", "min_open_sessions", "min_counterexamples",
                     "min_counterexample_sessions", "min_eval_matches", "min_eval_sessions", "max_guards", "max_rule_depth"):
            if type(getattr(self, name)) is not int or getattr(self, name) < 1:
                raise ValueError(f"{name} must be a positive integer")

    @classmethod
    def from_dict(cls, value: Mapping[str, Any]) -> "RepairConfig":
        raw = dict(value)
        raw["allowed_features"] = tuple(raw["allowed_features"])
        return cls(**raw)


def _rows(rows: Sequence[dict[str, Any]], split: str, capability: str) -> list[dict[str, Any]]:
    out = copy.deepcopy(list(rows))
    seen: set[str] = set()
    starts: dict[str, float] = {}
    for row in out:
        if not isinstance(row, dict) or row.get("split") != split or row.get("capability_id") != capability:
            raise ValueError(f"expected only {split!r} rows for {capability!r}")
        for field in ("episode_id", "session_id", "verifier_id"):
            if not isinstance(row.get(field), str) or not row[field]:
                raise ValueError(f"{field} is required")
        if row["episode_id"] in seen:
            raise ValueError("duplicate episode_id; repeated rows are not new evidence")
        seen.add(row["episode_id"])
        if row.get("evidence_level") not in _EVIDENCE or row.get("verified") is not True:
            raise ValueError("repair requires explicit verified L2/L3 targets, not teacher agreement or tool-ok alone")
        if "target" not in row or not _scalar(row["target"]):
            raise ValueError("repair target must be a finite bounded JSON scalar")
        features = row.get("features")
        if not isinstance(features, dict) or any(not isinstance(k, str) or not _scalar(v) for k, v in features.items()):
            raise ValueError("features must be a mapping of finite JSON scalars")
        times = [_number(row.get(k), k) for k in ("session_started_at", "features_at", "decision_at", "observed_at")]
        if times != sorted(times):
            raise ValueError("features must exist before the decision; outcome must follow it")
        sid = row["session_id"]
        if sid in starts and starts[sid] != times[0]:
            raise ValueError("inconsistent session_started_at")
        starts[sid] = times[0]
    return out


def _manifest(rows: Sequence[dict[str, Any]]) -> dict[str, Any]:
    return {
        "digest": digest(sorted(rows, key=lambda r: r["episode_id"])),
        "n_rows": len(rows),
        "session_hashes": sorted({digest(r["session_id"]) for r in rows}),
        "episode_hashes": sorted({digest(r["episode_id"]) for r in rows}),
    }


def _metrics(rows: Sequence[dict[str, Any]], parent: RoutineCandidate, rule: RoutineRule) -> dict[str, Any]:
    region = [r for r in rows if parent.rule.matches(r["features"])]
    matched = [r for r in region if rule.matches(r["features"])]
    correct = sum(_same(r["target"], parent.output) for r in matched)
    sessions: dict[str, list[dict[str, Any]]] = defaultdict(list)
    for row in matched:
        sessions[row["session_id"]].append(row)
    perfect = sum(all(_same(r["target"], parent.output) for r in group) for group in sessions.values())
    return {
        "n_rows": len(rows), "parent_matches": len(region), "matched": len(matched),
        "matched_sessions": len(sessions), "correct": correct,
        "precision": correct / len(matched) if matched else None,
        "coverage": len(matched) / len(rows) if rows else 0.0,
        "retained_parent_coverage": len(matched) / len(region) if region else 0.0,
        "counterexamples": sum(not _same(r["target"], parent.output) for r in region),
        "blocked_counterexamples": sum(not _same(r["target"], parent.output) and not rule.matches(r["features"]) for r in region),
        "all_correct_sessions": perfect,
        "all_correct_session_lower_95": wilson_lower(perfect, len(sessions)),
        "largest_session_share": max((len(g) / len(matched) for g in sessions.values()), default=0.0),
    }


def _effective_floor(parent: RoutineCandidate, config: RepairConfig) -> float:
    return max(parent.precision_floor, config.precision_floor)


def _open_pass(m: Mapping[str, Any], cfg: RepairConfig, floor: float, minimum: int) -> bool:
    return bool(m["matched"] >= minimum and m["matched_sessions"] >= cfg.min_open_sessions
                and m["precision"] is not None and m["precision"] >= floor
                and m["retained_parent_coverage"] >= cfg.min_retained_coverage
                and m["blocked_counterexamples"] > 0)


def validate_proposal(raw: Mapping[str, Any]) -> tuple[RoutineCandidate, RoutineCandidate | None, RepairConfig]:
    p = dict(raw)
    identity = p.pop("proposal_id", None)
    if raw.get("schema") != SCHEMA or identity != digest(p):
        raise ValueError("proposal hash/schema mismatch; freeze a new proposal after any edit")
    parent = RoutineCandidate.from_dict(dict(raw["parent"]))
    cfg = RepairConfig.from_dict(raw["config"])
    child = RoutineCandidate.from_dict(dict(raw["child"])) if raw.get("child") else None
    if parent.status != "demoted" or parent.evidence_level not in _EVIDENCE:
        raise ValueError("repair parent must be an outcome-backed demoted routine")
    if child:
        old = {canonical(p.to_dict()) for p in parent.rule.all}
        new = {canonical(p.to_dict()) for p in child.rule.all}
        added = [p for p in child.rule.all if canonical(p.to_dict()) not in old]
        if not old < new or len(added) != 1 or len(child.rule.all) > cfg.max_rule_depth:
            raise ValueError("a repair must preserve every parent predicate and add exactly one guard")
        if added[0].feature not in cfg.allowed_features:
            raise ValueError("repair guard is not allowlisted")
        if child.capability_id != parent.capability_id or not _same(child.output, parent.output):
            raise ValueError("repair cannot change capability or output")
        if child.status != "candidate" or child.sealed is not None or child.future is not None:
            raise ValueError("a proposal cannot pre-claim evaluation credit")
        if child.evidence_level != "L2_outcome":
            raise ValueError("repair has bounded outcome evidence only; fresh closed-loop credit is separate")
        if child.precision_floor < _effective_floor(parent, cfg):
            raise ValueError("repair cannot weaken the parent's precision floor")
    return parent, child, cfg


def propose_repair(
    parent: RoutineCandidate, train: Sequence[dict[str, Any]], dev: Sequence[dict[str, Any]],
    *, config: RepairConfig, frozen_at: float | None = None,
) -> dict[str, Any]:
    """Choose one narrower rule on open data. No evaluation inputs are accepted."""
    if parent.status != "demoted" or parent.evidence_level not in _EVIDENCE:
        raise ValueError("demote the outcome-backed parent before proposing a repair")
    now = _number(time.time() if frozen_at is None else frozen_at, "frozen_at")
    tr = _rows(train, "train", parent.capability_id)
    dv = _rows(dev, "dev", parent.capability_id)
    mt, md = _manifest(tr), _manifest(dv)
    for field in ("session_hashes", "episode_hashes"):
        if set(mt[field]) & set(md[field]):
            raise ValueError(f"train/dev overlap in {field}")
    if any(r["observed_at"] >= now for r in tr + dv):
        raise ValueError("open evidence must precede proposal freeze")
    bad = [r for r in tr if parent.rule.matches(r["features"]) and not _same(r["target"], parent.output)]
    reasons: list[str] = []
    candidates: list[tuple[RoutineCandidate, dict[str, Any], dict[str, Any]]] = []
    tested = 0
    if len(bad) < config.min_counterexamples or len({r["session_id"] for r in bad}) < config.min_counterexample_sessions:
        reasons.append("insufficient_independent_counterexamples")
    elif len(parent.rule.all) >= config.max_rule_depth:
        reasons.append("depth_budget_exhausted")
    else:
        inherited = {p.feature for p in parent.rule.all}
        projected = [dict(r, features={k: v for k, v in r["features"].items()
                                      if k in config.allowed_features and k not in inherited}) for r in tr]
        mining = CompileConfig(min_train_matches=config.min_train_matches)
        guards = sorted(_candidate_literals(projected, mining), key=lambda p: canonical(p.to_dict()))
        for guard in guards[:config.max_guards]:
            tested += 1
            rule = RoutineRule(parent.rule.all + (guard,))
            tm = _metrics(tr, parent, rule)
            dm = _metrics(dv, parent, rule)
            floor = _effective_floor(parent, config)
            if not (_open_pass(tm, config, floor, config.min_train_matches)
                    and _open_pass(dm, config, floor, config.min_dev_matches)):
                continue
            child = RoutineCandidate(
                capability_id=parent.capability_id, rule=rule, output=copy.deepcopy(parent.output),
                source_specialist=parent.source_specialist, source_generation=parent.source_generation,
                evidence_level="L2_outcome", precision_floor=floor,
                min_sessions=max(parent.min_sessions, config.min_eval_sessions),
                provenance={"parent_routine_id": parent.routine_id, "parent_artifact_hash": digest(parent.to_dict()),
                            "repair_generation": int(parent.provenance.get("repair_generation", 0)) + 1,
                            "operation": "narrow_region", "discovery_split": "train", "selection_split": "dev",
                            "sealed_visible_during_discovery": False},
            )
            candidates.append((child, tm, dm))
        if not candidates:
            reasons.append("no_safe_narrower_region")
    # Select exactly one using DEV. Do not use credit failures to choose a runner-up.
    candidates.sort(key=lambda c: (-c[2]["retained_parent_coverage"], -c[2]["precision"], c[0].rule.canonical_key()))
    selected = candidates[0] if candidates else None
    proposal = {
        "schema": SCHEMA, "status": "candidate" if selected else "no_update", "frozen_at": now,
        "parent": parent.to_dict(), "child": selected[0].to_dict() if selected else None,
        "config": asdict(config), "open_manifests": {"train": mt, "dev": md},
        "diagnosis": {"counterexamples": len(bad), "counterexample_sessions": len({r["session_id"] for r in bad}),
                      "guards_tested": tested, "dev_qualified": len(candidates), "reasons": reasons},
        "train_metrics": selected[1] if selected else None,
        "dev_metrics": selected[2] if selected else None,
    }
    proposal["proposal_id"] = digest(proposal)
    # Round-trip once, so caller tuples and wire lists share the same interface.
    proposal = json.loads(canonical(proposal))
    validate_proposal(proposal)
    return proposal


class RepairTrialStore:
    """Private credit access journal, not a replacement runtime telemetry ledger.

    Failed trials consume their evaluation sessions too. SQLite transactions make
    idempotent retries and cohort reuse checks survive process restarts. The owner
    of the filesystem is trusted; hashes and private attributes are NOT auth.
    """
    def __init__(self, path: Path) -> None:
        self.path = Path(path)
        self.path.parent.mkdir(parents=True, exist_ok=True)
        with sqlite3.connect(self.path) as con:
            con.execute("CREATE TABLE IF NOT EXISTS trials (proposal TEXT, phase TEXT, cohort TEXT, receipt TEXT, PRIMARY KEY(proposal, phase))")
            con.execute("CREATE TABLE IF NOT EXISTS sessions (identity TEXT PRIMARY KEY, proposal TEXT, phase TEXT)")
            con.execute("CREATE TABLE IF NOT EXISTS episodes (identity TEXT PRIMARY KEY, proposal TEXT, phase TEXT)")

    def receipt(self, proposal_id: str, phase: str) -> dict[str, Any] | None:
        with sqlite3.connect(self.path) as con:
            row = con.execute("SELECT receipt FROM trials WHERE proposal=? AND phase=?", (proposal_id, phase)).fetchone()
        return json.loads(row[0]) if row else None

    def _issue(self, proposal: Mapping[str, Any], phase: str, manifest: Mapping[str, Any],
               make_receipt: Callable[[], dict[str, Any]]) -> dict[str, Any]:
        pid = str(proposal["proposal_id"])
        with sqlite3.connect(self.path, timeout=30) as con:
            con.execute("BEGIN IMMEDIATE")
            previous = con.execute("SELECT cohort, receipt FROM trials WHERE proposal=? AND phase=?", (pid, phase)).fetchone()
            if previous:
                if previous[0] != manifest["digest"]:
                    raise ValueError("proposal already evaluated on a different cohort; no adaptive retest")
                return json.loads(previous[1])
            for table, key in (("sessions", "session_hashes"), ("episodes", "episode_hashes")):
                for identity in manifest[key]:
                    if con.execute(f"SELECT 1 FROM {table} WHERE identity=?", (identity,)).fetchone():
                        raise ValueError(f"evaluation {table} already consumed; collect a fresh cohort")
                con.executemany(f"INSERT INTO {table} VALUES (?,?,?)", [(x, pid, phase) for x in manifest[key]])
            receipt = make_receipt()
            receipt["receipt_id"] = digest(receipt)
            con.execute("INSERT INTO trials VALUES (?,?,?,?)", (pid, phase, manifest["digest"], canonical(receipt)))
            return receipt


def score_repair(
    proposal: Mapping[str, Any], rows: Sequence[dict[str, Any]], *, phase: str,
    store: RepairTrialStore, evaluated_at: float | None = None,
) -> dict[str, Any]:
    """Trusted scorer entrypoint. Return aggregates, never evaluation examples."""
    parent, child, cfg = validate_proposal(proposal)
    if phase not in {"sealed", "future"} or child is None:
        raise ValueError("credit requires a frozen candidate and phase sealed/future")
    scoped = _rows(rows, phase, parent.capability_id)
    now = _number(time.time() if evaluated_at is None else evaluated_at, "evaluated_at")
    prior = store.receipt(proposal["proposal_id"], "sealed") if phase == "future" else None
    if phase == "future" and (prior is None or not prior["passed"]):
        raise ValueError("future confirmation requires an accepted sealed receipt in this store")
    cutoff = float(prior["evaluated_at"]) if prior else float(proposal["frozen_at"])
    if now <= cutoff or any(r["session_started_at"] <= cutoff or r["observed_at"] > now for r in scoped):
        raise ValueError("evaluation must use new sessions after freeze/prior credit, with outcomes already observed")
    manifest = _manifest(scoped)
    for key in ("session_hashes", "episode_hashes"):
        open_ids = set(proposal["open_manifests"]["train"][key]) | set(proposal["open_manifests"]["dev"][key])
        if open_ids & set(manifest[key]):
            raise ValueError(f"evaluation/open overlap in {key}")

    def grade() -> dict[str, Any]:
        m = _metrics(scoped, parent, child.rule)
        floor = _effective_floor(parent, cfg)
        reasons = []
        if m["matched"] < cfg.min_eval_matches:
            reasons.append("insufficient_matches")
        if m["matched_sessions"] < max(cfg.min_eval_sessions, parent.min_sessions):
            reasons.append("insufficient_sessions")
        if m["precision"] is None or m["precision"] < floor:
            reasons.append("precision_floor")
        # A single session with a million duplicate-looking steps must not
        # masquerade as a million independent successful experiments.
        if m["all_correct_session_lower_95"] is None or m["all_correct_session_lower_95"] < floor:
            reasons.append("session_uncertainty")
        if m["largest_session_share"] > cfg.max_session_share:
            reasons.append("session_concentration")
        if m["retained_parent_coverage"] < cfg.min_retained_coverage:
            reasons.append("negligible_retained_coverage")
        raw_metrics = evaluate_rule(scoped, rule=child.rule, output=child.output, split=phase)
        return {
            "schema": RECEIPT_SCHEMA, "proposal_id": proposal["proposal_id"], "phase": phase,
            "candidate_hash": digest(child.to_dict()), "config_hash": digest(proposal["config"]),
            "cohort_hash": manifest["digest"], "evaluated_at": now,
            "prior_receipt_id": prior["receipt_id"] if prior else None,
            "passed": not reasons, "reasons": reasons, "metrics": m,
            "routine_metrics": raw_metrics.to_dict(),
            "claim": "bounded_target_agreement_on_prospective_sessions",
            "whole_task_success": None, "measured_token_savings": None,
        }
    return store._issue(proposal, phase, manifest, grade)


def activate_repair(
    registry: RoutineRegistry, proposal: Mapping[str, Any], *, store: RepairTrialStore, approved: bool = False,
) -> RoutineRegistry:
    """Return a new registry after explicit local approval. Never mutate in place.

    This approves the routine artifact only. A composed cascade still needs its
    own closed-loop credit; this function does not enable any live harness.
    """
    parent, child, _cfg = validate_proposal(proposal)
    if approved is not True or child is None:
        raise ValueError("explicit approval of a candidate repair is required")
    receipts = [store.receipt(proposal["proposal_id"], phase) for phase in ("sealed", "future")]
    for phase, r in zip(("sealed", "future"), receipts):
        if not r or not r["passed"] or r["candidate_hash"] != digest(child.to_dict()) or r["config_hash"] != digest(proposal["config"]):
            raise ValueError(f"missing or mismatched {phase} repair credit")
    sealed, future = receipts
    assert sealed is not None and future is not None
    if future["prior_receipt_id"] != sealed["receipt_id"]:
        raise ValueError("future credit is not linked to the sealed decision")
    active_child = replace(
        child, status="promoted", sealed=RoutineMetrics.from_dict(sealed["routine_metrics"]),
        future=RoutineMetrics.from_dict(future["routine_metrics"]),
        provenance={**child.provenance, "repair_proposal_id": proposal["proposal_id"],
                    "sealed_receipt": sealed["receipt_id"], "future_receipt": future["receipt_id"],
                    "activation": "explicit_local_approval", "cascade_credit": "required_separately"},
    )
    entries = list(registry.candidates)
    parents = [r for r in entries if r.routine_id == parent.routine_id]
    if len(parents) != 1 or digest(parents[0].to_dict()) != digest(parent.to_dict()):
        raise ValueError("parent changed since proposal; refusing stale replacement")
    existing = [r for r in entries if r.routine_id == child.routine_id]
    if existing:
        if len(existing) == 1 and digest(existing[0].to_dict()) == digest(active_child.to_dict()):
            return RoutineRegistry(entries)
        raise ValueError("child artifact already exists with different evidence or state")
    return RoutineRegistry(entries + [active_child])


def rollback_repair(registry: RoutineRegistry, proposal_id: str) -> RoutineRegistry:
    """Disable the repaired child. Never resurrect its already-drifting parent."""
    return RoutineRegistry(replace(c, status="demoted") if c.provenance.get("repair_proposal_id") == proposal_id else c
                           for c in registry.candidates)


def export_shadow_plan(proposal: Mapping[str, Any], cascade: Any, *, config: Any = None) -> dict[str, Any]:
    from .aodl import AodlBindingConfig, compile_aodl
    _parent, child, _cfg = validate_proposal(proposal)
    if child is None:
        raise ValueError("no_update has no shadow candidate")
    cfg = config or AodlBindingConfig()
    # Inserting even a credited routine changes the composed strategy. Do not
    # inherit the old cascade's production credit for this new composition.
    return compile_aodl(
        capability_id=child.capability_id,
        cascade=replace(cascade, status="candidate", sealed=None, future=None), routines=[child],
        config=replace(cfg, allow_uncredited_shadow=True, include_candidate_routines_in_shadow=True),
    )


def _read_json(path: Path) -> Any:
    return json.loads(path.read_text(encoding="utf-8"))


def _read_rows(path: Path) -> list[dict[str, Any]]:
    return [json.loads(line) for line in path.read_text(encoding="utf-8").splitlines() if line.strip()]


def _write_new(path: Path, value: Any) -> None:
    path.parent.mkdir(parents=True, exist_ok=True)
    with path.open("x", encoding="utf-8") as f:
        f.write(json.dumps(value, indent=2, sort_keys=True, allow_nan=False) + "\n")


def main(argv: list[str] | None = None) -> int:
    parser = argparse.ArgumentParser(prog="python -m z0int.refinement")
    sub = parser.add_subparsers(dest="command", required=True)
    p = sub.add_parser("propose", help="narrow a demoted routine using open train/dev data only")
    for name in ("parent", "train", "dev", "config", "output"):
        p.add_argument(f"--{name}", type=Path, required=True)
    p = sub.add_parser("score", help="trusted scoring process for one fresh cohort")
    for name in ("proposal", "input", "store", "output"):
        p.add_argument(f"--{name}", type=Path, required=True)
    p.add_argument("--phase", choices=("sealed", "future"), required=True)
    p = sub.add_parser("activate", help="explicitly approve a twice-credited routine into a new registry")
    for name in ("proposal", "registry", "store", "output"):
        p.add_argument(f"--{name}", type=Path, required=True)
    p.add_argument("--approve", action="store_true")
    p = sub.add_parser("rollback", help="disable a repaired child without restoring the unsafe parent")
    p.add_argument("--registry", type=Path, required=True)
    p.add_argument("--proposal-id", required=True)
    p.add_argument("--output", type=Path, required=True)
    p = sub.add_parser("shadow-plan", help="export a non-traffic-eligible AODL candidate plan")
    for name in ("proposal", "cascade", "output"):
        p.add_argument(f"--{name}", type=Path, required=True)
    args = parser.parse_args(argv)
    try:
        if args.output.exists():
            raise ValueError("output exists; use a new artifact path")
        if args.command == "propose":
            result = propose_repair(RoutineCandidate.from_dict(_read_json(args.parent)), _read_rows(args.train),
                                    _read_rows(args.dev), config=RepairConfig.from_dict(_read_json(args.config)))
        elif args.command == "score":
            result = score_repair(_read_json(args.proposal), _read_rows(args.input), phase=args.phase,
                                  store=RepairTrialStore(args.store))
        elif args.command == "shadow-plan":
            from .cascade import CascadePolicy
            result = export_shadow_plan(_read_json(args.proposal), CascadePolicy.from_dict(_read_json(args.cascade)))
        else:
            if not args.registry.is_file():
                raise ValueError("input registry does not exist")
            registry = RoutineRegistry.from_jsonl(args.registry)
            if args.command == "activate":
                updated = activate_repair(registry, _read_json(args.proposal), store=RepairTrialStore(args.store), approved=args.approve)
            else:
                updated = rollback_repair(registry, args.proposal_id)
            args.output.parent.mkdir(parents=True, exist_ok=True)
            with args.output.open("x", encoding="utf-8") as f:
                for c in updated.candidates:
                    f.write(canonical(c.to_dict()) + "\n")
            print(canonical({"output": str(args.output), "entries": len(updated.candidates)}))
            return 0
        _write_new(args.output, result)
        print(canonical({"output": str(args.output), "status": result.get("status"), "passed": result.get("passed")}))
        return 0
    except (ValueError, KeyError, TypeError, OSError, sqlite3.Error) as exc:
        # Record content may contain private text. Do not echo raw values or tracebacks.
        print(canonical({"error": type(exc).__name__, "message": "Repair operation failed validation or I/O; check inputs and private gate state."}))
        return 2


if __name__ == "__main__":
    raise SystemExit(main())
