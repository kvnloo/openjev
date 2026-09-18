"""ABAB meta-loop primitives for z0int.

This module intentionally does not call an LLM.  It codifies the invariant
parts of the research loop so any harness can supply A-reasoning while the
repository owns evidence, prioritization, credit, archives and stop rules.

A: choose the highest-information uncertainty / proposal.
B: execute the experiment and record one evidence mutation.
C: when a cheap discriminating experiment is worth more than more research,
   run it and use the result to kill/split competing hypotheses.

Frontier-KB rules preserved here:
* experiments, not models, are the population;
* validity -> activation -> sealed credit;
* failures remain negative data;
* all-systems and learned-only Pareto fronts are separate;
* niche champions survive even when they are not globally best;
* cost per credited experiment is a health metric;
* repeated NO_UPDATE / collapsed information gain stops the loop.
"""

from __future__ import annotations

import json
import math
from dataclasses import asdict, dataclass, field
from pathlib import Path
from typing import Any, Iterable, Literal, Sequence

ExperimentStatus = Literal[
    "proposed",
    "invalid",
    "inactive",
    "rejected",
    "credited",
    "no_update",
    "disconfirmed",
]


@dataclass(frozen=True)
class ExperimentMetrics:
    """Comparable deployment metrics for one frozen experiment."""

    verified_success: float | None = None
    safe_coverage: float | None = None
    premium_tokens_per_success: float | None = None
    total_tokens_per_success: float | None = None
    latency_ms: float | None = None
    joules_per_success: float | None = None
    transfer_retention: float | None = None
    frontier_tokens_saved: float | None = None

    def to_dict(self) -> dict[str, Any]:
        return asdict(self)


@dataclass(frozen=True)
class CreditGates:
    validity: bool = False
    activation: bool = False
    credit: bool = False
    notes: tuple[str, ...] = ()

    @property
    def passed(self) -> bool:
        return self.validity and self.activation and self.credit

    def to_dict(self) -> dict[str, Any]:
        return asdict(self)


@dataclass(frozen=True)
class ExperimentProposal:
    id: str
    hypothesis_id: str
    niche: str
    summary: str
    expected_information_gain: float
    impact: float
    decision_change: float
    transferability: float
    estimated_cost: float
    kind: str = "B"
    lineage: str = "root"
    parents: tuple[str, ...] = ()
    learned: bool = True

    def priority(self) -> float:
        cost = max(float(self.estimated_cost), 1e-9)
        vals = (
            max(0.0, self.expected_information_gain),
            max(0.0, self.impact),
            max(0.0, self.decision_change),
            max(0.0, self.transferability),
        )
        out = math.prod(vals) / cost
        return float(out)

    def to_dict(self) -> dict[str, Any]:
        out = asdict(self)
        out["priority"] = self.priority()
        return out


@dataclass(frozen=True)
class ExperimentRecord:
    proposal: ExperimentProposal
    status: ExperimentStatus
    gates: CreditGates = CreditGates()
    metrics: ExperimentMetrics = ExperimentMetrics()
    actual_cost: float = 0.0
    failure_class: str | None = None
    evidence: tuple[str, ...] = ()
    killed_hypothesis: bool = False
    schema: str = "z0int.abab_experiment.v1"

    def to_dict(self) -> dict[str, Any]:
        return {
            "schema": self.schema,
            "proposal": self.proposal.to_dict(),
            "status": self.status,
            "gates": self.gates.to_dict(),
            "metrics": self.metrics.to_dict(),
            "actual_cost": self.actual_cost,
            "failure_class": self.failure_class,
            "evidence": list(self.evidence),
            "killed_hypothesis": self.killed_hypothesis,
        }

    @classmethod
    def from_dict(cls, raw: dict[str, Any]) -> "ExperimentRecord":
        p = dict(raw["proposal"])
        p.pop("priority", None)
        p["parents"] = tuple(p.get("parents") or ())
        gates_raw = dict(raw.get("gates") or {})
        gates_raw["notes"] = tuple(gates_raw.get("notes") or ())
        return cls(
            proposal=ExperimentProposal(**p),
            status=str(raw["status"]),  # type: ignore[arg-type]
            gates=CreditGates(**gates_raw),
            metrics=ExperimentMetrics(**dict(raw.get("metrics") or {})),
            actual_cost=float(raw.get("actual_cost") or 0.0),
            failure_class=raw.get("failure_class"),
            evidence=tuple(raw.get("evidence") or ()),
            killed_hypothesis=bool(raw.get("killed_hypothesis")),
        )


@dataclass(frozen=True)
class AbabConfig:
    min_priority: float = 0.01
    no_update_patience: int = 3
    transfer_retention_floor: float = 0.80
    cost_growth_patience: int = 3


_MAXIMIZE = ("verified_success", "safe_coverage", "transfer_retention", "frontier_tokens_saved")
_MINIMIZE = ("premium_tokens_per_success", "total_tokens_per_success", "latency_ms", "joules_per_success")


def _metric(record: ExperimentRecord, name: str) -> float | None:
    return getattr(record.metrics, name)


def pareto_dominates(a: ExperimentRecord, b: ExperimentRecord) -> bool:
    """Dominance on every metric both records actually measured."""
    comparable = 0
    strictly = False
    for name in _MAXIMIZE:
        av, bv = _metric(a, name), _metric(b, name)
        if av is None or bv is None:
            continue
        comparable += 1
        if av < bv - 1e-12:
            return False
        strictly |= av > bv + 1e-12
    for name in _MINIMIZE:
        av, bv = _metric(a, name), _metric(b, name)
        if av is None or bv is None:
            continue
        comparable += 1
        if av > bv + 1e-12:
            return False
        strictly |= av < bv - 1e-12
    return comparable > 0 and strictly


def pareto_front(records: Sequence[ExperimentRecord]) -> list[ExperimentRecord]:
    credited = [r for r in records if r.status == "credited" and r.gates.passed]
    return [r for r in credited if not any(pareto_dominates(o, r) for o in credited if o is not r)]


class ExperimentArchive:
    """Append-only evidence archive + MAP-Elites style niche fronts."""

    def __init__(self, records: Iterable[ExperimentRecord] = ()) -> None:
        self._records = list(records)

    @property
    def records(self) -> tuple[ExperimentRecord, ...]:
        return tuple(self._records)

    def append(self, record: ExperimentRecord) -> None:
        if any(r.proposal.id == record.proposal.id for r in self._records):
            raise ValueError(f"duplicate experiment id {record.proposal.id!r}")
        self._records.append(record)

    def niche_front(self, niche: str, *, learned_only: bool = False) -> list[ExperimentRecord]:
        rows = [
            r
            for r in self._records
            if r.proposal.niche == niche and (not learned_only or r.proposal.learned)
        ]
        return sorted(pareto_front(rows), key=lambda r: r.proposal.id)

    def champion_niches(self, *, learned_only: bool = False) -> dict[str, list[ExperimentRecord]]:
        niches = sorted({r.proposal.niche for r in self._records})
        return {n: self.niche_front(n, learned_only=learned_only) for n in niches if self.niche_front(n, learned_only=learned_only)}

    def cost_per_credit(self) -> float | None:
        total = sum(max(0.0, r.actual_cost) for r in self._records)
        credits = sum(1 for r in self._records if r.status == "credited" and r.gates.passed)
        return total / credits if credits else None

    def no_update_streak(self) -> int:
        streak = 0
        for r in reversed(self._records):
            if r.status in {"no_update", "rejected", "inactive", "invalid"}:
                streak += 1
            else:
                break
        return streak

    def credited_cost_series(self) -> list[float]:
        total = 0.0
        credits = 0
        out: list[float] = []
        for r in self._records:
            total += max(0.0, r.actual_cost)
            if r.status == "credited" and r.gates.passed:
                credits += 1
                out.append(total / credits)
        return out

    def stop_reason(self, proposals: Sequence[ExperimentProposal] = (), *, config: AbabConfig | None = None) -> str | None:
        cfg = config or AbabConfig()
        if self.no_update_streak() >= cfg.no_update_patience:
            return "repeated_no_update"
        if proposals and max((p.priority() for p in proposals), default=0.0) < cfg.min_priority:
            return "collapsed_information_gain"
        series = self.credited_cost_series()
        k = cfg.cost_growth_patience
        if k >= 2 and len(series) >= k:
            tail = series[-k:]
            if all(b > a + 1e-12 for a, b in zip(tail, tail[1:])):
                return "rising_cost_per_credit"
        return None

    def to_jsonl(self, path: Path) -> None:
        path.parent.mkdir(parents=True, exist_ok=True)
        with path.open("w", encoding="utf-8") as fh:
            for row in self._records:
                fh.write(json.dumps(row.to_dict(), sort_keys=True) + "\n")

    @classmethod
    def from_jsonl(cls, path: Path) -> "ExperimentArchive":
        if not path.exists():
            return cls()
        rows: list[ExperimentRecord] = []
        for line in path.read_text(encoding="utf-8").splitlines():
            line = line.strip()
            if line:
                rows.append(ExperimentRecord.from_dict(json.loads(line)))
        return cls(rows)


def choose_next(proposals: Sequence[ExperimentProposal], *, config: AbabConfig | None = None) -> ExperimentProposal | None:
    cfg = config or AbabConfig()
    eligible = [p for p in proposals if p.priority() >= cfg.min_priority]
    if not eligible:
        return None
    return max(eligible, key=lambda p: (p.priority(), p.expected_information_gain, -p.estimated_cost, p.id))


def gate_status(gates: CreditGates, *, killed_hypothesis: bool = False) -> ExperimentStatus:
    if not gates.validity:
        return "invalid"
    if not gates.activation:
        return "inactive"
    if gates.credit:
        return "credited"
    return "disconfirmed" if killed_hypothesis else "rejected"


def should_run_c(
    *,
    experiment_value: float,
    further_research_value: float,
    competing_hypotheses: int,
) -> bool:
    """C is justified only when a discriminating test dominates more research."""
    return competing_hypotheses >= 2 and experiment_value > further_research_value
