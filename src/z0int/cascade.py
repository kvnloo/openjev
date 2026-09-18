"""Compile a confidence cascade that minimizes premium tokens.

This module sits between specialist training and compute placement:

    deterministic routine -> tiny specialist -> local semantic model -> frontier

It does **not** choose providers.  Kerdoios can decide where the residual model
stage should run.  z0int's cascade only decides whether a cheaper stage may
safely absorb a decision before escalation.

Thresholds are searched on ``dev`` only.  The unchanged winning policy is then
credited on ``sealed``.  The optimization target is premium/frontier tokens per
verified success subject to a non-inferiority success constraint.
"""

from __future__ import annotations

import argparse
import itertools
import json
import math
from dataclasses import asdict, dataclass, field, replace
from pathlib import Path
from typing import Any, Iterable, Literal, Sequence

Split = Literal["train", "dev", "sealed", "future"]
PolicyStatus = Literal["candidate", "credited", "promoted", "demoted"]


@dataclass(frozen=True)
class StagePolicy:
    name: str
    min_confidence: float
    terminal: bool = False

    def __post_init__(self) -> None:
        if not self.name:
            raise ValueError("stage name is required")
        if not 0.0 <= self.min_confidence <= 1.0:
            raise ValueError("min_confidence must be in [0, 1]")

    def to_dict(self) -> dict[str, Any]:
        return asdict(self)


@dataclass(frozen=True)
class CascadeMetrics:
    split: str
    n: int
    n_sessions: int
    correct: int
    success_rate: float
    premium_tokens: int
    total_tokens: int
    premium_tokens_per_success: float
    total_tokens_per_success: float
    mean_latency_ms: float
    stage_counts: dict[str, int]
    fallback_rate: float
    baseline_success_rate: float | None = None
    baseline_premium_tokens: int | None = None
    premium_token_reduction: float | None = None

    def to_dict(self) -> dict[str, Any]:
        return asdict(self)

    @classmethod
    def from_dict(cls, raw: dict[str, Any]) -> "CascadeMetrics":
        return cls(**raw)


@dataclass(frozen=True)
class CascadePolicy:
    capability_id: str
    stages: tuple[StagePolicy, ...]
    final_stage: str
    min_success_rate: float
    max_success_regression: float
    objective: str = "premium_tokens_per_verified_success"
    status: PolicyStatus = "candidate"
    dev: CascadeMetrics | None = None
    sealed: CascadeMetrics | None = None
    future: CascadeMetrics | None = None
    provenance: dict[str, Any] = field(default_factory=dict)
    schema: str = "z0int.cascade_policy.v1"

    def __post_init__(self) -> None:
        if not self.capability_id:
            raise ValueError("capability_id is required")
        if not self.stages:
            raise ValueError("at least one stage is required")
        names = [s.name for s in self.stages]
        if len(names) != len(set(names)):
            raise ValueError("stage names must be unique")
        if names[-1] != self.final_stage:
            raise ValueError("final_stage must be the last stage")
        if not self.stages[-1].terminal:
            raise ValueError("last stage must be terminal")
        if not 0.0 <= self.max_success_regression <= 1.0:
            raise ValueError("max_success_regression must be in [0, 1]")

    def to_dict(self) -> dict[str, Any]:
        return {
            "schema": self.schema,
            "capability_id": self.capability_id,
            "stages": [s.to_dict() for s in self.stages],
            "final_stage": self.final_stage,
            "min_success_rate": self.min_success_rate,
            "max_success_regression": self.max_success_regression,
            "objective": self.objective,
            "status": self.status,
            "dev": self.dev.to_dict() if self.dev else None,
            "sealed": self.sealed.to_dict() if self.sealed else None,
            "future": self.future.to_dict() if self.future else None,
            "provenance": self.provenance,
        }

    @classmethod
    def from_dict(cls, raw: dict[str, Any]) -> "CascadePolicy":
        def metric(name: str) -> CascadeMetrics | None:
            val = raw.get(name)
            return CascadeMetrics.from_dict(dict(val)) if isinstance(val, dict) else None

        return cls(
            capability_id=str(raw["capability_id"]),
            stages=tuple(StagePolicy(**x) for x in raw["stages"]),
            final_stage=str(raw["final_stage"]),
            min_success_rate=float(raw.get("min_success_rate") or 0.0),
            max_success_regression=float(raw.get("max_success_regression") or 0.0),
            objective=str(raw.get("objective") or "premium_tokens_per_verified_success"),
            status=str(raw.get("status") or "candidate"),  # type: ignore[arg-type]
            dev=metric("dev"),
            sealed=metric("sealed"),
            future=metric("future"),
            provenance=dict(raw.get("provenance") or {}),
        )


@dataclass(frozen=True)
class CascadeCompileConfig:
    max_success_regression: float = 0.0
    absolute_min_success: float = 0.0
    min_rows: int = 20
    min_sessions: int = 3
    threshold_grid: tuple[float, ...] = (0.0, 0.5, 0.7, 0.8, 0.9, 0.95, 0.99)
    require_premium_token_reduction: bool = True


@dataclass(frozen=True)
class CascadeDecision:
    stage: str
    output: Any
    confidence: float
    premium_tokens: int
    total_tokens: int
    latency_ms: float
    escalations: int

    def to_dict(self) -> dict[str, Any]:
        return asdict(self)


def _rows_for(rows: Sequence[dict[str, Any]], capability_id: str, split: str) -> list[dict[str, Any]]:
    return [r for r in rows if r.get("capability_id") == capability_id and r.get("split") == split]


def _stage_obs(row: dict[str, Any], stage: str) -> dict[str, Any] | None:
    predictions = row.get("predictions")
    if not isinstance(predictions, dict):
        return None
    obs = predictions.get(stage)
    if not isinstance(obs, dict) or obs.get("available", True) is False:
        return None
    if "output" not in obs:
        return None
    return obs


def decide_row(row: dict[str, Any], policy: CascadePolicy) -> CascadeDecision:
    for i, stage in enumerate(policy.stages):
        obs = _stage_obs(row, stage.name)
        if obs is None:
            if stage.terminal:
                raise ValueError(f"terminal stage {stage.name!r} unavailable")
            continue
        confidence = float(obs.get("confidence", 1.0 if stage.terminal else 0.0))
        if stage.terminal or confidence >= stage.min_confidence:
            return CascadeDecision(
                stage=stage.name,
                output=obs.get("output"),
                confidence=confidence,
                premium_tokens=max(0, int(obs.get("premium_tokens") or 0)),
                total_tokens=max(0, int(obs.get("total_tokens") or obs.get("premium_tokens") or 0)),
                latency_ms=max(0.0, float(obs.get("latency_ms") or 0.0)),
                escalations=i,
            )
    raise ValueError("cascade has no reachable terminal stage")


def _baseline_metrics(rows: Sequence[dict[str, Any]], final_stage: str) -> tuple[float, int]:
    if not rows:
        return 0.0, 0
    correct = 0
    premium = 0
    for row in rows:
        obs = _stage_obs(row, final_stage)
        if obs is None:
            raise ValueError(f"baseline/final stage {final_stage!r} missing")
        correct += int(obs.get("output") == row.get("target"))
        premium += max(0, int(obs.get("premium_tokens") or 0))
    return correct / len(rows), premium


def evaluate_policy(rows: Sequence[dict[str, Any]], policy: CascadePolicy, *, split: str) -> CascadeMetrics:
    scoped = _rows_for(rows, policy.capability_id, split)
    sessions = {str(r.get("session_id") or "") for r in scoped if r.get("session_id")}
    correct = 0
    premium = 0
    total = 0
    latency = 0.0
    counts: dict[str, int] = {s.name: 0 for s in policy.stages}
    fallback = 0
    for row in scoped:
        if "target" not in row:
            raise ValueError("cascade rows require target")
        d = decide_row(row, policy)
        correct += int(d.output == row.get("target"))
        premium += d.premium_tokens
        total += d.total_tokens
        latency += d.latency_ms
        counts[d.stage] = counts.get(d.stage, 0) + 1
        fallback += int(d.stage == policy.final_stage)
    n = len(scoped)
    success = correct / n if n else 0.0
    baseline_success, baseline_premium = _baseline_metrics(scoped, policy.final_stage) if scoped else (0.0, 0)
    reduction = None
    if baseline_premium > 0:
        reduction = 1.0 - premium / baseline_premium
    return CascadeMetrics(
        split=split,
        n=n,
        n_sessions=len(sessions),
        correct=correct,
        success_rate=success,
        premium_tokens=premium,
        total_tokens=total,
        premium_tokens_per_success=premium / max(correct, 1),
        total_tokens_per_success=total / max(correct, 1),
        mean_latency_ms=latency / n if n else 0.0,
        stage_counts=counts,
        fallback_rate=fallback / n if n else 0.0,
        baseline_success_rate=baseline_success,
        baseline_premium_tokens=baseline_premium,
        premium_token_reduction=reduction,
    )


def _threshold_values(rows: Sequence[dict[str, Any]], stage: str, cfg: CascadeCompileConfig) -> tuple[float, ...]:
    vals = set(cfg.threshold_grid)
    confs = []
    for row in rows:
        obs = _stage_obs(row, stage)
        if obs is not None:
            try:
                confs.append(float(obs.get("confidence", 0.0)))
            except (TypeError, ValueError):
                pass
    if confs:
        s = sorted(confs)
        for q in (0.25, 0.5, 0.75, 0.9):
            vals.add(s[min(len(s) - 1, int(round(q * (len(s) - 1))))])
    return tuple(sorted(max(0.0, min(1.0, x)) for x in vals))


def compile_cascade(
    rows: Sequence[dict[str, Any]],
    *,
    capability_id: str,
    stage_order: Sequence[str],
    final_stage: str,
    config: CascadeCompileConfig | None = None,
) -> CascadePolicy | None:
    """Search thresholds on dev only.  Sealed is not inspected."""
    cfg = config or CascadeCompileConfig()
    if not stage_order or stage_order[-1] != final_stage:
        raise ValueError("final_stage must be last in stage_order")
    dev = _rows_for(rows, capability_id, "dev")
    sessions = {str(r.get("session_id") or "") for r in dev if r.get("session_id")}
    if len(dev) < cfg.min_rows or len(sessions) < cfg.min_sessions:
        return None
    baseline_success, baseline_premium = _baseline_metrics(dev, final_stage)
    min_success = max(cfg.absolute_min_success, baseline_success - cfg.max_success_regression)
    nonfinal = list(stage_order[:-1])
    grids = [_threshold_values(dev, name, cfg) for name in nonfinal]
    best: tuple[tuple[float, float, float, float], CascadePolicy] | None = None
    for thresholds in itertools.product(*grids) if grids else [()]:
        stages = tuple(
            [StagePolicy(name, float(th), terminal=False) for name, th in zip(nonfinal, thresholds)]
            + [StagePolicy(final_stage, 0.0, terminal=True)]
        )
        policy = CascadePolicy(
            capability_id=capability_id,
            stages=stages,
            final_stage=final_stage,
            min_success_rate=min_success,
            max_success_regression=cfg.max_success_regression,
            provenance={
                "threshold_search_split": "dev",
                "sealed_visible_during_search": False,
                "baseline_dev_success": baseline_success,
                "baseline_dev_premium_tokens": baseline_premium,
            },
        )
        metrics = evaluate_policy(dev, policy, split="dev")
        if metrics.success_rate + 1e-12 < min_success:
            continue
        if cfg.require_premium_token_reduction and metrics.premium_tokens >= baseline_premium:
            continue
        # Main objective: premium tokens / verified success. Secondary: more
        # success, fewer total tokens, lower latency.
        key = (
            metrics.premium_tokens_per_success,
            -metrics.success_rate,
            metrics.total_tokens_per_success,
            metrics.mean_latency_ms,
        )
        cand = replace(policy, dev=metrics)
        if best is None or key < best[0]:
            best = (key, cand)
    return best[1] if best else None


def credit_sealed(
    rows: Sequence[dict[str, Any]],
    policy: CascadePolicy,
    *,
    config: CascadeCompileConfig | None = None,
) -> CascadePolicy:
    """Evaluate the frozen policy on sealed and return credited/candidate."""
    cfg = config or CascadeCompileConfig(max_success_regression=policy.max_success_regression)
    sealed = _rows_for(rows, policy.capability_id, "sealed")
    sessions = {str(r.get("session_id") or "") for r in sealed if r.get("session_id")}
    metrics = evaluate_policy(sealed, policy, split="sealed")
    baseline_success = metrics.baseline_success_rate or 0.0
    floor = max(cfg.absolute_min_success, baseline_success - policy.max_success_regression)
    enough = len(sealed) >= cfg.min_rows and len(sessions) >= cfg.min_sessions
    token_win = (
        not cfg.require_premium_token_reduction
        or (metrics.baseline_premium_tokens or 0) > metrics.premium_tokens
    )
    status: PolicyStatus = "credited" if enough and metrics.success_rate + 1e-12 >= floor and token_win else "candidate"
    return replace(policy, sealed=metrics, status=status)


def promote(policy: CascadePolicy) -> CascadePolicy:
    return replace(policy, status="promoted") if policy.status == "credited" else policy


def observe_future(
    rows: Sequence[dict[str, Any]],
    policy: CascadePolicy,
    *,
    min_rows: int = 20,
    min_sessions: int = 3,
) -> CascadePolicy:
    """Demote a promoted policy when future success loses non-inferiority."""
    if policy.status not in {"promoted", "demoted"}:
        return policy
    future_rows = _rows_for(rows, policy.capability_id, "future")
    sessions = {str(r.get("session_id") or "") for r in future_rows if r.get("session_id")}
    metrics = evaluate_policy(future_rows, policy, split="future")
    baseline = metrics.baseline_success_rate or 0.0
    floor = max(policy.min_success_rate, baseline - policy.max_success_regression)
    enough = len(future_rows) >= min_rows and len(sessions) >= min_sessions
    drifted = enough and metrics.success_rate + 1e-12 < floor
    return replace(policy, future=metrics, status="demoted" if drifted else policy.status)


def load_jsonl(path: Path) -> list[dict[str, Any]]:
    rows = []
    with path.open(encoding="utf-8") as fh:
        for line in fh:
            line = line.strip()
            if line:
                rows.append(json.loads(line))
    return rows


def _main(argv: list[str] | None = None) -> int:
    parser = argparse.ArgumentParser(prog="python -m z0int.cascade")
    sub = parser.add_subparsers(dest="cmd", required=True)

    comp = sub.add_parser("compile")
    comp.add_argument("--input", type=Path, required=True)
    comp.add_argument("--output", type=Path, required=True)
    comp.add_argument("--capability", required=True)
    comp.add_argument("--stages", required=True, help="comma-separated cheap->frontier order")
    comp.add_argument("--final-stage", required=True)
    comp.add_argument("--max-success-regression", type=float, default=0.0)

    apply = sub.add_parser("apply")
    apply.add_argument("--policy", type=Path, required=True)
    apply.add_argument("--row", required=True, help="JSON trace row with predictions")

    args = parser.parse_args(argv)
    if args.cmd == "compile":
        rows = load_jsonl(args.input)
        stages = tuple(x.strip() for x in args.stages.split(",") if x.strip())
        cfg = CascadeCompileConfig(max_success_regression=args.max_success_regression)
        policy = compile_cascade(
            rows,
            capability_id=args.capability,
            stage_order=stages,
            final_stage=args.final_stage,
            config=cfg,
        )
        if policy is None:
            print(json.dumps({"ok": False, "reason": "no_dev_policy_meets_constraints"}, indent=2))
            return 2
        policy = promote(credit_sealed(rows, policy, config=cfg))
        args.output.parent.mkdir(parents=True, exist_ok=True)
        args.output.write_text(json.dumps(policy.to_dict(), indent=2) + "\n", encoding="utf-8")
        print(json.dumps({"ok": True, "status": policy.status, "output": str(args.output)}, indent=2))
        return 0
    if args.cmd == "apply":
        policy = CascadePolicy.from_dict(json.loads(args.policy.read_text(encoding="utf-8")))
        row = json.loads(args.row)
        print(json.dumps(decide_row(row, policy).to_dict(), indent=2))
        return 0
    raise AssertionError(args.cmd)


if __name__ == "__main__":
    raise SystemExit(_main())
