"""Shared statistical credit gates for z0int experiments.

Frontier-KB's harness-evolver contract is deliberately stronger than
"point estimate looks better": validity -> activation -> sealed credit.
This module owns the tiny dependency-free statistics used by the credit gate.

The important invariant is that *search never consumes these results*.  A
caller may inspect open/dev metrics while proposing a candidate, but sealed
credit is computed only after the candidate is frozen.
"""

from __future__ import annotations

import math
from dataclasses import asdict, dataclass
from typing import Iterable, Sequence


@dataclass(frozen=True)
class LiftCredit:
    """One-sample lift against a pre-registered Bernoulli baseline."""

    n: int
    successes: int
    observed: float | None
    baseline: float | None
    lift: float | None
    z_score: float | None
    passed_2sigma: bool

    def to_dict(self) -> dict:
        return asdict(self)


@dataclass(frozen=True)
class PairedCredit:
    """Paired A/B success delta and an approximate 95% interval.

    Differences are evaluated row-by-row on the same sealed tasks.  This is
    materially better than comparing two independent percentages because the
    shared task difficulty cancels out.  The interval is a normal 1.96-sigma
    interval over the per-row differences {-1, 0, +1}; for tiny batteries the
    caller should simply refuse credit via its independent-session/sample floor.
    """

    n: int
    candidate_success: float | None
    baseline_success: float | None
    delta: float | None
    lower_95: float | None
    upper_95: float | None
    candidate_only_wins: int
    baseline_only_wins: int
    ties: int
    noninferiority_margin: float
    passed_2sigma_noninferiority: bool

    def to_dict(self) -> dict:
        return asdict(self)


def lift_credit(*, successes: int, n: int, baseline: float | None, z_min: float = 1.959963984540054) -> LiftCredit:
    if n <= 0 or baseline is None:
        return LiftCredit(n, successes, None, baseline, None, None, False)
    observed = successes / n
    lift = observed - baseline
    p0 = min(1.0, max(0.0, float(baseline)))
    var = p0 * (1.0 - p0) / n
    if var <= 0.0:
        # A degenerate baseline can only be beaten if it is 0 and we observe
        # positive success; baseline==1 cannot be improved.
        z = math.inf if p0 == 0.0 and observed > 0.0 else (0.0 if observed == p0 else -math.inf)
    else:
        z = lift / math.sqrt(var)
    return LiftCredit(
        n=n,
        successes=successes,
        observed=observed,
        baseline=p0,
        lift=lift,
        z_score=z,
        passed_2sigma=bool(z >= z_min),
    )


def paired_credit(
    candidate_correct: Sequence[bool] | Iterable[bool],
    baseline_correct: Sequence[bool] | Iterable[bool],
    *,
    noninferiority_margin: float = 0.0,
    z: float = 1.959963984540054,
) -> PairedCredit:
    cand = [bool(x) for x in candidate_correct]
    base = [bool(x) for x in baseline_correct]
    if len(cand) != len(base):
        raise ValueError("paired credit requires equal-length candidate/baseline outcomes")
    n = len(cand)
    if n == 0:
        return PairedCredit(0, None, None, None, None, None, 0, 0, 0, noninferiority_margin, False)

    diffs = [int(c) - int(b) for c, b in zip(cand, base)]
    cand_rate = sum(cand) / n
    base_rate = sum(base) / n
    delta = sum(diffs) / n
    wins = sum(1 for d in diffs if d > 0)
    losses = sum(1 for d in diffs if d < 0)
    ties = n - wins - losses

    if n <= 1:
        se = math.inf
    else:
        mean = delta
        sample_var = sum((d - mean) ** 2 for d in diffs) / (n - 1)
        se = math.sqrt(sample_var / n)
    if math.isinf(se):
        lower = -math.inf
        upper = math.inf
    else:
        lower = delta - z * se
        upper = delta + z * se

    return PairedCredit(
        n=n,
        candidate_success=cand_rate,
        baseline_success=base_rate,
        delta=delta,
        lower_95=lower,
        upper_95=upper,
        candidate_only_wins=wins,
        baseline_only_wins=losses,
        ties=ties,
        noninferiority_margin=noninferiority_margin,
        passed_2sigma_noninferiority=bool(lower >= -float(noninferiority_margin)),
    )
