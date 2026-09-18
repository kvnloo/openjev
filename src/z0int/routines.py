"""Compile stable specialist behavior into deterministic routines.

The routine compiler is intentionally conservative:

* rules are a tiny declarative AST (no eval / generated code),
* discovery only sees ``train`` rows,
* pruning only sees ``dev`` rows,
* ``sealed`` is credit-only,
* ``future`` observations can demote an already-promoted rule,
* no match always fails open to the learned specialist.

Input rows are JSON objects with at least::

    {
      "capability_id": "coding.needs_verification",
      "split": "train",                  # train|dev|sealed|future
      "session_id": "session-123",
      "features": {"prev_family": "EDIT", "tool_ok": true},
      "target": "VERIFY",
      "evidence_level": "L2_outcome"     # optional
    }

``target`` is the bounded output the routine is judged against.  L2/L3 callers
should populate it from an outcome/verifier, not merely a teacher prediction.
"""

from __future__ import annotations

import argparse
import json
import math
from collections import Counter, defaultdict
from dataclasses import asdict, dataclass, field, replace
from pathlib import Path
from typing import Any, Iterable, Iterator, Literal, Sequence

from .credit import lift_credit

Split = Literal["train", "dev", "sealed", "future"]
Status = Literal["candidate", "credited", "promoted", "demoted"]
EvidenceLevel = Literal["L0_imitation", "L1_teacher", "L2_outcome", "L3_closed_loop"]

_ALLOWED_SPLITS = {"train", "dev", "sealed", "future"}
_ALLOWED_EVIDENCE = {"L0_imitation", "L1_teacher", "L2_outcome", "L3_closed_loop"}
_SCALAR = (str, int, float, bool, type(None))


@dataclass(frozen=True)
class LiteralPredicate:
    """One safe, auditable feature test."""

    feature: str
    op: str
    value: Any

    def __post_init__(self) -> None:
        if not self.feature or not isinstance(self.feature, str):
            raise ValueError("predicate feature must be a non-empty string")
        if self.op not in {"eq", "ge", "le"}:
            raise ValueError(f"unsupported predicate op {self.op!r}")
        if not isinstance(self.value, _SCALAR):
            raise ValueError("predicate values must be JSON scalars")
        if isinstance(self.value, float) and not math.isfinite(self.value):
            raise ValueError("predicate values must be finite JSON scalars")
        if self.op in {"ge", "le"} and (isinstance(self.value, bool) or not isinstance(self.value, (int, float))):
            raise ValueError(f"{self.op} requires a numeric value")

    def matches(self, features: dict[str, Any]) -> bool:
        if self.feature not in features:
            return False
        actual = features[self.feature]
        if self.op == "eq":
            # Typed guards must not accept 1 as a verified True flag.
            if isinstance(actual, bool) != isinstance(self.value, bool):
                return False
            if not isinstance(actual, _SCALAR) or (isinstance(actual, float) and not math.isfinite(actual)):
                return False
            return actual == self.value
        if isinstance(actual, bool) or not isinstance(actual, (int, float)) or not math.isfinite(actual):
            return False
        if self.op == "ge":
            return float(actual) >= float(self.value)
        return float(actual) <= float(self.value)

    def to_dict(self) -> dict[str, Any]:
        return asdict(self)

    @classmethod
    def from_dict(cls, raw: dict[str, Any]) -> "LiteralPredicate":
        return cls(feature=str(raw["feature"]), op=str(raw["op"]), value=raw.get("value"))


@dataclass(frozen=True)
class RoutineRule:
    """Conjunction of 1..N literals.  All literals must match."""

    all: tuple[LiteralPredicate, ...]

    def __post_init__(self) -> None:
        if not self.all:
            raise ValueError("routine rule must contain at least one literal")
        features = [p.feature for p in self.all]
        if len(features) != len(set(features)):
            raise ValueError("routine rule may test a feature at most once")

    def matches(self, features: dict[str, Any]) -> bool:
        return all(p.matches(features) for p in self.all)

    def to_dict(self) -> dict[str, Any]:
        return {"all": [p.to_dict() for p in self.all]}

    @classmethod
    def from_dict(cls, raw: dict[str, Any]) -> "RoutineRule":
        vals = raw.get("all")
        if not isinstance(vals, list):
            raise ValueError("rule.all must be a list")
        return cls(tuple(LiteralPredicate.from_dict(dict(v)) for v in vals))

    def canonical_key(self) -> str:
        bits = sorted((p.feature, p.op, json.dumps(p.value, sort_keys=True)) for p in self.all)
        return json.dumps(bits, separators=(",", ":"))


@dataclass(frozen=True)
class RoutineMetrics:
    split: Split
    n_rows: int
    n_sessions: int
    matched: int
    matched_sessions: int
    correct: int
    precision: float | None
    coverage: float
    wilson_lower_95: float | None
    global_prior_precision: float | None
    lift_vs_global_prior: float | None
    lift_z_score: float | None = None
    lift_passes_2sigma: bool = False

    def to_dict(self) -> dict[str, Any]:
        return asdict(self)

    @classmethod
    def from_dict(cls, raw: dict[str, Any]) -> "RoutineMetrics":
        return cls(**raw)


@dataclass(frozen=True)
class RoutineCandidate:
    capability_id: str
    rule: RoutineRule
    output: Any
    source_specialist: str
    source_generation: int
    evidence_level: EvidenceLevel
    precision_floor: float
    min_sessions: int
    status: Status = "candidate"
    train: RoutineMetrics | None = None
    dev: RoutineMetrics | None = None
    sealed: RoutineMetrics | None = None
    future: RoutineMetrics | None = None
    provenance: dict[str, Any] = field(default_factory=dict)
    routine_id: str = ""
    schema: str = "z0int.routine_candidate.v1"

    def __post_init__(self) -> None:
        if not self.capability_id:
            raise ValueError("capability_id is required")
        if not 0.0 < self.precision_floor <= 1.0:
            raise ValueError("precision_floor must be in (0, 1]")
        if self.min_sessions < 1:
            raise ValueError("min_sessions must be >= 1")
        if self.evidence_level not in _ALLOWED_EVIDENCE:
            raise ValueError(f"unknown evidence level {self.evidence_level!r}")
        if not self.routine_id:
            object.__setattr__(self, "routine_id", _routine_id(self.capability_id, self.rule, self.output))

    def to_dict(self) -> dict[str, Any]:
        return {
            "schema": self.schema,
            "routine_id": self.routine_id,
            "capability_id": self.capability_id,
            "rule": self.rule.to_dict(),
            "output": self.output,
            "source_specialist": self.source_specialist,
            "source_generation": self.source_generation,
            "evidence_level": self.evidence_level,
            "precision_floor": self.precision_floor,
            "min_sessions": self.min_sessions,
            "status": self.status,
            "train": self.train.to_dict() if self.train else None,
            "dev": self.dev.to_dict() if self.dev else None,
            "sealed": self.sealed.to_dict() if self.sealed else None,
            "future": self.future.to_dict() if self.future else None,
            "provenance": self.provenance,
        }

    @classmethod
    def from_dict(cls, raw: dict[str, Any]) -> "RoutineCandidate":
        def metric(name: str) -> RoutineMetrics | None:
            val = raw.get(name)
            return RoutineMetrics.from_dict(dict(val)) if isinstance(val, dict) else None

        return cls(
            capability_id=str(raw["capability_id"]),
            rule=RoutineRule.from_dict(dict(raw["rule"])),
            output=raw.get("output"),
            source_specialist=str(raw.get("source_specialist") or "unknown"),
            source_generation=int(raw.get("source_generation") or 0),
            evidence_level=str(raw.get("evidence_level") or "L0_imitation"),  # type: ignore[arg-type]
            precision_floor=float(raw.get("precision_floor") or 0.95),
            min_sessions=int(raw.get("min_sessions") or 3),
            status=str(raw.get("status") or "candidate"),  # type: ignore[arg-type]
            train=metric("train"),
            dev=metric("dev"),
            sealed=metric("sealed"),
            future=metric("future"),
            provenance=dict(raw.get("provenance") or {}),
            routine_id=str(raw.get("routine_id") or ""),
        )


@dataclass(frozen=True)
class CompileConfig:
    precision_floor: float = 0.95
    min_train_matches: int = 20
    min_dev_matches: int = 10
    min_sealed_matches: int = 10
    min_sessions: int = 3
    min_lift_vs_global_prior: float = 0.02
    max_rule_depth: int = 2
    max_feature_cardinality: int = 32
    max_numeric_thresholds: int = 5
    max_candidates: int = 128
    require_wilson_lower: bool = False
    min_credit_z: float = 1.959963984540054

    def __post_init__(self) -> None:
        if not 0.0 < self.precision_floor <= 1.0:
            raise ValueError("precision_floor must be in (0, 1]")
        if self.max_rule_depth not in {1, 2}:
            raise ValueError("max_rule_depth must be 1 or 2 in v1")


@dataclass(frozen=True)
class RoutineDecision:
    matched: bool
    output: Any = None
    routine_id: str | None = None
    capability_id: str | None = None
    reason: str = "fallback_to_specialist"

    def to_dict(self) -> dict[str, Any]:
        return asdict(self)


def _routine_id(capability_id: str, rule: RoutineRule, output: Any) -> str:
    import hashlib

    body = json.dumps(
        {"capability_id": capability_id, "rule": rule.to_dict(), "output": output},
        sort_keys=True,
        separators=(",", ":"),
    )
    return hashlib.blake2b(body.encode("utf-8"), digest_size=12).hexdigest()


def _validate_row(row: dict[str, Any]) -> None:
    if row.get("split") not in _ALLOWED_SPLITS:
        raise ValueError(f"row split must be one of {sorted(_ALLOWED_SPLITS)}")
    if not isinstance(row.get("features"), dict):
        raise ValueError("row.features must be an object")
    if "target" not in row:
        raise ValueError("row.target is required")
    ev = row.get("evidence_level")
    if ev is not None and ev not in _ALLOWED_EVIDENCE:
        raise ValueError(f"unknown evidence level {ev!r}")


def _rows_for(rows: Sequence[dict[str, Any]], capability_id: str, split: Split) -> list[dict[str, Any]]:
    out = []
    for row in rows:
        _validate_row(row)
        if row.get("capability_id") == capability_id and row.get("split") == split:
            out.append(row)
    return out


def _scalar_values(rows: Sequence[dict[str, Any]], feature: str) -> list[Any]:
    vals: list[Any] = []
    for row in rows:
        value = (row.get("features") or {}).get(feature)
        if isinstance(value, _SCALAR):
            vals.append(value)
    return vals


def _quantile_thresholds(values: list[float], limit: int) -> list[float]:
    if not values:
        return []
    vals = sorted(set(float(v) for v in values))
    if len(vals) <= limit:
        return vals
    qs = [0.1, 0.25, 0.5, 0.75, 0.9]
    qs = qs[:limit]
    out: list[float] = []
    n = len(vals)
    for q in qs:
        idx = min(n - 1, max(0, int(round(q * (n - 1)))))
        out.append(vals[idx])
    return sorted(set(out))


def _candidate_literals(rows: Sequence[dict[str, Any]], cfg: CompileConfig) -> list[LiteralPredicate]:
    features = sorted({str(k) for row in rows for k in (row.get("features") or {}).keys()})
    literals: list[LiteralPredicate] = []
    for feature in features:
        values = _scalar_values(rows, feature)
        if not values:
            continue
        non_bool_numeric = [float(v) for v in values if isinstance(v, (int, float)) and not isinstance(v, bool)]
        categorical = Counter(json.dumps(v, sort_keys=True) for v in values)
        if len(categorical) <= cfg.max_feature_cardinality:
            seen: dict[str, Any] = {}
            for v in values:
                seen.setdefault(json.dumps(v, sort_keys=True), v)
            for key, count in categorical.items():
                if count >= cfg.min_train_matches:
                    literals.append(LiteralPredicate(feature, "eq", seen[key]))
        if len(set(non_bool_numeric)) >= 4:
            for threshold in _quantile_thresholds(non_bool_numeric, cfg.max_numeric_thresholds):
                literals.append(LiteralPredicate(feature, "ge", threshold))
                literals.append(LiteralPredicate(feature, "le", threshold))
    # canonical dedupe
    uniq: dict[tuple[str, str, str], LiteralPredicate] = {}
    for p in literals:
        uniq[(p.feature, p.op, json.dumps(p.value, sort_keys=True))] = p
    return list(uniq.values())


def _iter_rules(literals: Sequence[LiteralPredicate], max_depth: int) -> Iterator[RoutineRule]:
    for literal in literals:
        yield RoutineRule((literal,))
    if max_depth < 2:
        return
    for i, a in enumerate(literals):
        for b in literals[i + 1 :]:
            if a.feature == b.feature:
                continue
            yield RoutineRule(tuple(sorted((a, b), key=lambda p: p.feature)))


def wilson_lower(successes: int, n: int, z: float = 1.959963984540054) -> float | None:
    if n <= 0:
        return None
    p = successes / n
    denom = 1.0 + (z * z) / n
    centre = p + (z * z) / (2.0 * n)
    spread = z * math.sqrt((p * (1.0 - p) + (z * z) / (4.0 * n)) / n)
    return max(0.0, (centre - spread) / denom)


def _same_output(a: Any, b: Any) -> bool:
    """Compare typed JSON outputs, including nested Boolean values."""
    try:
        return json.dumps(a, sort_keys=True, allow_nan=False) == json.dumps(b, sort_keys=True, allow_nan=False)
    except (TypeError, ValueError):
        return False


def evaluate_rule(
    rows: Sequence[dict[str, Any]],
    *,
    rule: RoutineRule,
    output: Any,
    split: Split,
) -> RoutineMetrics:
    scoped = [r for r in rows if r.get("split") == split]
    sessions = {str(r.get("session_id") or "") for r in scoped if r.get("session_id")}
    matches = [r for r in scoped if rule.matches(dict(r.get("features") or {}))]
    matched_sessions = {str(r.get("session_id") or "") for r in matches if r.get("session_id")}
    correct = sum(1 for r in matches if _same_output(r.get("target"), output))
    precision = correct / len(matches) if matches else None
    coverage = len(matches) / len(scoped) if scoped else 0.0
    global_rows = [r for r in scoped if _same_output(r.get("target"), output)]
    global_prior = len(global_rows) / len(scoped) if scoped else None
    lift = (precision - global_prior) if precision is not None and global_prior is not None else None
    credit = lift_credit(successes=correct, n=len(matches), baseline=global_prior)
    return RoutineMetrics(
        split=split,
        n_rows=len(scoped),
        n_sessions=len(sessions),
        matched=len(matches),
        matched_sessions=len(matched_sessions),
        correct=correct,
        precision=precision,
        coverage=coverage,
        wilson_lower_95=wilson_lower(correct, len(matches)),
        global_prior_precision=global_prior,
        lift_vs_global_prior=lift,
        lift_z_score=credit.z_score,
        lift_passes_2sigma=credit.passed_2sigma,
    )


def _dominates(a: RoutineCandidate, b: RoutineCandidate) -> bool:
    """True when a is no worse in precision/coverage and is simpler or equal."""
    if a.dev is None or b.dev is None or a.dev.precision is None or b.dev.precision is None:
        return False
    return (
        a.output == b.output
        and a.dev.precision >= b.dev.precision
        and a.dev.coverage >= b.dev.coverage
        and len(a.rule.all) <= len(b.rule.all)
        and (
            a.dev.precision > b.dev.precision
            or a.dev.coverage > b.dev.coverage
            or len(a.rule.all) < len(b.rule.all)
        )
    )


def _passes(metrics: RoutineMetrics | None, *, cfg: CompileConfig, min_matches: int) -> bool:
    if metrics is None or metrics.precision is None:
        return False
    if metrics.matched < min_matches or metrics.matched_sessions < cfg.min_sessions:
        return False
    if metrics.precision < cfg.precision_floor:
        return False
    if metrics.lift_vs_global_prior is None or metrics.lift_vs_global_prior < cfg.min_lift_vs_global_prior:
        return False
    if cfg.min_credit_z > 0 and (metrics.lift_z_score is None or metrics.lift_z_score < cfg.min_credit_z):
        return False
    if cfg.require_wilson_lower and (
        metrics.wilson_lower_95 is None or metrics.wilson_lower_95 < cfg.precision_floor
    ):
        return False
    return True


def mine_routines(
    rows: Sequence[dict[str, Any]],
    *,
    capability_id: str,
    source_specialist: str,
    source_generation: int = 0,
    evidence_level: EvidenceLevel = "L0_imitation",
    config: CompileConfig | None = None,
) -> list[RoutineCandidate]:
    """Discover on train, prune/filter on dev.  Sealed is never inspected here."""
    cfg = config or CompileConfig()
    train = _rows_for(rows, capability_id, "train")
    dev = _rows_for(rows, capability_id, "dev")
    if not train or not dev:
        return []
    literals = _candidate_literals(train, cfg)
    candidates: list[RoutineCandidate] = []
    seen: set[tuple[str, str]] = set()
    for rule in _iter_rules(literals, cfg.max_rule_depth):
        region = [r for r in train if rule.matches(dict(r.get("features") or {}))]
        if len(region) < cfg.min_train_matches:
            continue
        counts = Counter(json.dumps(r.get("target"), sort_keys=True) for r in region)
        if not counts:
            continue
        target_key, _ = counts.most_common(1)[0]
        output = next(r.get("target") for r in region if json.dumps(r.get("target"), sort_keys=True) == target_key)
        key = (rule.canonical_key(), target_key)
        if key in seen:
            continue
        seen.add(key)
        tr = evaluate_rule(train, rule=rule, output=output, split="train")
        if not _passes(tr, cfg=cfg, min_matches=cfg.min_train_matches):
            continue
        dv = evaluate_rule(dev, rule=rule, output=output, split="dev")
        if not _passes(dv, cfg=cfg, min_matches=cfg.min_dev_matches):
            continue
        candidates.append(
            RoutineCandidate(
                capability_id=capability_id,
                rule=rule,
                output=output,
                source_specialist=source_specialist,
                source_generation=source_generation,
                evidence_level=evidence_level,
                precision_floor=cfg.precision_floor,
                min_sessions=cfg.min_sessions,
                train=tr,
                dev=dv,
                provenance={
                    "discovery_split": "train",
                    "prune_split": "dev",
                    "sealed_visible_during_discovery": False,
                },
            )
        )
    # Remove rules that add complexity without a dev frontier gain.
    keep: list[RoutineCandidate] = []
    for cand in sorted(
        candidates,
        key=lambda c: (
            -(c.dev.precision if c.dev and c.dev.precision is not None else -1.0),
            -(c.dev.coverage if c.dev else 0.0),
            len(c.rule.all),
        ),
    ):
        if any(_dominates(k, cand) for k in keep):
            continue
        keep.append(cand)
        if len(keep) >= cfg.max_candidates:
            break
    return keep


def credit_sealed(
    rows: Sequence[dict[str, Any]],
    candidates: Sequence[RoutineCandidate],
    *,
    config: CompileConfig | None = None,
) -> list[RoutineCandidate]:
    """Credit candidates on sealed rows.  Does not discover or mutate predicates."""
    cfg = config or CompileConfig()
    credited: list[RoutineCandidate] = []
    for candidate in candidates:
        sealed_rows = _rows_for(rows, candidate.capability_id, "sealed")
        met = evaluate_rule(sealed_rows, rule=candidate.rule, output=candidate.output, split="sealed")
        status: Status = "credited" if _passes(met, cfg=cfg, min_matches=cfg.min_sealed_matches) else "candidate"
        credited.append(replace(candidate, sealed=met, status=status))
    return credited


def promote_credited(candidates: Sequence[RoutineCandidate]) -> list[RoutineCandidate]:
    """Promote only already-credited candidates; no metric computation here."""
    return [replace(c, status="promoted") if c.status == "credited" else c for c in candidates]


class RoutineRegistry:
    """Runtime first-stage router for deterministic routines.

    Registry ordering is deterministic: higher sealed precision, then coverage,
    then simpler predicates.  Non-promoted entries are ignored.
    """

    def __init__(self, candidates: Iterable[RoutineCandidate] = ()) -> None:
        self._candidates = list(candidates)

    @property
    def candidates(self) -> tuple[RoutineCandidate, ...]:
        return tuple(self._candidates)

    def decide(self, capability_id: str, features: dict[str, Any]) -> RoutineDecision:
        return self._decide(capability_id, features, shadow=False)

    def decide_shadow(self, capability_id: str, features: dict[str, Any]) -> RoutineDecision:
        """Advisory prediction only; never promotes a candidate or enables traffic."""
        return self._decide(capability_id, features, shadow=True)

    def _decide(self, capability_id: str, features: dict[str, Any], *, shadow: bool) -> RoutineDecision:
        allowed = {"candidate", "credited", "promoted"} if shadow else {"promoted"}
        active = [
            c
            for c in self._candidates
            if c.status in allowed and c.capability_id == capability_id
        ]
        active.sort(
            key=lambda c: (
                -(c.sealed.precision if c.sealed and c.sealed.precision is not None else 0.0),
                -(c.sealed.coverage if c.sealed else 0.0),
                len(c.rule.all),
                c.routine_id,
            )
        )
        for candidate in active:
            if candidate.rule.matches(features):
                return RoutineDecision(
                    matched=True,
                    output=candidate.output,
                    routine_id=candidate.routine_id,
                    capability_id=capability_id,
                    reason="shadow_routine_match" if shadow else "promoted_routine_match",
                )
        return RoutineDecision(matched=False, capability_id=capability_id)

    def observe_future(
        self,
        rows: Sequence[dict[str, Any]],
        *,
        min_future_matches: int = 10,
        min_future_sessions: int = 3,
        demote_below_precision: float | None = None,
    ) -> list[RoutineCandidate]:
        """Attach future metrics and demote drifting routines.

        A routine is only demoted after enough independent future evidence.
        Sparse early traffic therefore cannot flip a production rule.
        """
        out: list[RoutineCandidate] = []
        for candidate in self._candidates:
            if candidate.status not in {"promoted", "demoted"}:
                out.append(candidate)
                continue
            future_rows = _rows_for(rows, candidate.capability_id, "future")
            met = evaluate_rule(future_rows, rule=candidate.rule, output=candidate.output, split="future")
            floor = demote_below_precision if demote_below_precision is not None else candidate.precision_floor
            enough = met.matched >= min_future_matches and met.matched_sessions >= min_future_sessions
            drifted = enough and met.precision is not None and met.precision < floor
            status: Status = "demoted" if drifted else candidate.status
            out.append(replace(candidate, future=met, status=status))
        self._candidates = out
        return list(out)

    def to_jsonl(self, path: Path) -> None:
        path.parent.mkdir(parents=True, exist_ok=True)
        with path.open("w", encoding="utf-8") as fh:
            for candidate in self._candidates:
                fh.write(json.dumps(candidate.to_dict(), sort_keys=True) + "\n")

    @classmethod
    def from_jsonl(cls, path: Path) -> "RoutineRegistry":
        candidates: list[RoutineCandidate] = []
        if not path.exists():
            return cls()
        with path.open(encoding="utf-8") as fh:
            for line in fh:
                line = line.strip()
                if not line:
                    continue
                candidates.append(RoutineCandidate.from_dict(json.loads(line)))
        return cls(candidates)


def load_jsonl(path: Path) -> list[dict[str, Any]]:
    rows: list[dict[str, Any]] = []
    with path.open(encoding="utf-8") as fh:
        for line in fh:
            line = line.strip()
            if line:
                rows.append(json.loads(line))
    return rows


def compile_and_credit(
    rows: Sequence[dict[str, Any]],
    *,
    capability_id: str,
    source_specialist: str,
    source_generation: int = 0,
    evidence_level: EvidenceLevel = "L0_imitation",
    config: CompileConfig | None = None,
) -> list[RoutineCandidate]:
    cfg = config or CompileConfig()
    discovered = mine_routines(
        rows,
        capability_id=capability_id,
        source_specialist=source_specialist,
        source_generation=source_generation,
        evidence_level=evidence_level,
        config=cfg,
    )
    return promote_credited(credit_sealed(rows, discovered, config=cfg))


def _main(argv: list[str] | None = None) -> int:
    parser = argparse.ArgumentParser(prog="python -m z0int.routines")
    sub = parser.add_subparsers(dest="cmd", required=True)

    mine = sub.add_parser("compile", help="mine train/dev rules and credit on sealed")
    mine.add_argument("--input", type=Path, required=True)
    mine.add_argument("--output", type=Path, required=True)
    mine.add_argument("--capability", required=True)
    mine.add_argument("--specialist", required=True)
    mine.add_argument("--generation", type=int, default=0)
    mine.add_argument("--evidence", choices=sorted(_ALLOWED_EVIDENCE), default="L0_imitation")
    mine.add_argument("--precision-floor", type=float, default=0.95)
    mine.add_argument("--min-sessions", type=int, default=3)

    apply = sub.add_parser("apply", help="apply promoted registry; prints fallback on no match")
    apply.add_argument("--registry", type=Path, required=True)
    apply.add_argument("--capability", required=True)
    apply.add_argument("--features", required=True, help="JSON object")

    args = parser.parse_args(argv)
    if args.cmd == "compile":
        rows = load_jsonl(args.input)
        cfg = CompileConfig(precision_floor=args.precision_floor, min_sessions=args.min_sessions)
        routines = compile_and_credit(
            rows,
            capability_id=args.capability,
            source_specialist=args.specialist,
            source_generation=args.generation,
            evidence_level=args.evidence,
            config=cfg,
        )
        RoutineRegistry(routines).to_jsonl(args.output)
        summary = {
            "schema": "z0int.routine_compile.v1",
            "capability_id": args.capability,
            "n": len(routines),
            "promoted": sum(1 for r in routines if r.status == "promoted"),
            "output": str(args.output),
        }
        print(json.dumps(summary, indent=2))
        return 0
    if args.cmd == "apply":
        features = json.loads(args.features)
        if not isinstance(features, dict):
            raise SystemExit("--features must decode to an object")
        decision = RoutineRegistry.from_jsonl(args.registry).decide(args.capability, features)
        print(json.dumps(decision.to_dict(), indent=2))
        return 0
    raise AssertionError(args.cmd)


if __name__ == "__main__":
    raise SystemExit(_main())
