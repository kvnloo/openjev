"""AODL/HOTL 0.2 binding for z0int specialist cascades.

AODL owns intent, topology, policies, budgets and authority.  z0int owns the
implementation that satisfies that contract.  This module intentionally does
*not* invent ``fly``, ``mushroomBody``, ``routine`` or ``cascade`` node kinds.
It compiles z0int's runtime into the existing HOTL 0.2 vocabulary:

    task -> executor -> service/model -> verifier -> stateStore
                          ^
                       artifact

Implementation-specific model/checkpoint/provider bindings live in ``plan``;
the intent graph remains portable.  Runtime route/outcome receipts can be
encoded as AODL ``eventLog`` entries so the desired graph and observed graph
stay distinct.
"""

from __future__ import annotations

import argparse
import hashlib
import json
import re
from dataclasses import asdict, dataclass, field
from pathlib import Path
from typing import Any, Iterable, Mapping, Sequence

from .cascade import CascadePolicy
from .routines import RoutineCandidate

SPEC_VERSION = "0.2"
AODL_NODE_KINDS = frozenset(
    {
        "task",
        "executor",
        "model",
        "tool",
        "service",
        "memory",
        "stateStore",
        "humanGate",
        "environment",
        "artifact",
        "verifier",
    }
)
AODL_EDGE_RELATIONS = frozenset(
    {
        "dependency",
        "data",
        "message",
        "delegation",
        "critique",
        "verification",
        "allocation",
        "control",
        "observation",
        "artifact",
    }
)
_ID_RE = re.compile(r"^[A-Za-z][A-Za-z0-9_.:-]{0,127}$")


@dataclass(frozen=True)
class AodlBudgets:
    """Declared Gamma budgets, never observed spend.

    ``premium_tokens`` is z0int/Kerdoios' scarce-frontier budget.  ``tokens``
    remains the portable HOTL token budget.  Extra dimensions are legal in
    HOTL 0.2's open ``constraints.budgets`` object.
    """

    tokens: int | None = None
    premium_tokens: int | None = None
    latency_ms: float | None = None
    usd: float | None = None
    joules: float | None = None

    def to_dict(self) -> dict[str, Any]:
        out: dict[str, Any] = {}
        for key, value in asdict(self).items():
            if value is not None:
                out[key] = value
        return out


@dataclass(frozen=True)
class AodlBindingConfig:
    harness_id: str = "omp"
    executor_id: str = "z0int-runtime"
    verifier_id: str = "outcome-verifier"
    receipt_store_id: str = "decision-receipts"
    routine_service_id: str = "routine-service"
    routine_artifact_id: str = "routine-registry"
    revision: int = 0
    precision_floor: float = 0.95
    budgets: AodlBudgets = field(default_factory=AodlBudgets)
    stage_bindings: Mapping[str, Mapping[str, Any]] = field(default_factory=dict)
    stage_roles: Mapping[str, str] = field(default_factory=dict)
    include_routine_slot: bool = True
    source: str = "z0int"

    def __post_init__(self) -> None:
        for value in (
            self.executor_id,
            self.verifier_id,
            self.receipt_store_id,
            self.routine_service_id,
            self.routine_artifact_id,
        ):
            _require_id(value)
        if not self.harness_id:
            raise ValueError("harness_id is required")
        if self.revision < 0:
            raise ValueError("revision must be >= 0")
        if not 0.0 < self.precision_floor <= 1.0:
            raise ValueError("precision_floor must be in (0, 1]")


def _require_id(value: str) -> str:
    if not _ID_RE.match(value):
        raise ValueError(f"invalid AODL id {value!r}")
    return value


def _safe_id(prefix: str, value: str) -> str:
    raw = re.sub(r"[^A-Za-z0-9_.:-]+", "-", value).strip("-._:") or "node"
    if not raw[0].isalpha():
        raw = f"n-{raw}"
    return _require_id(f"{prefix}{raw}"[:128])


def _canonical(value: Any) -> str:
    return json.dumps(value, sort_keys=True, separators=(",", ":"), ensure_ascii=True)


def source_hash(value: Any) -> str:
    """Canonical sha256 used for AODL provenance and runtime event linkage."""

    return hashlib.sha256(_canonical(value).encode("utf-8")).hexdigest()


def _port(pid: str, direction: str, schema: str, *, classification: str = "sanitized") -> dict[str, Any]:
    return {
        "id": pid,
        "direction": direction,
        "schema": schema,
        "classification": classification,
    }


def _node(
    node_id: str,
    kind: str,
    ports: Sequence[dict[str, Any]],
    capabilities: Sequence[str],
    *,
    authority: Sequence[str] | None = None,
) -> dict[str, Any]:
    _require_id(node_id)
    if kind not in AODL_NODE_KINDS:
        raise ValueError(f"unknown AODL node kind {kind!r}")
    return {
        "id": node_id,
        "kind": kind,
        "ports": list(ports),
        "capabilities": list(capabilities),
        "authorityCeiling": list(authority if authority is not None else capabilities),
        "lifecycle": "declared",
    }


def _edge(
    eid: str,
    relation: str,
    frm: str,
    to: str,
    from_port: str,
    to_port: str,
    schema_hash: str,
    *,
    grant: Sequence[str] = (),
) -> dict[str, Any]:
    if relation not in AODL_EDGE_RELATIONS:
        raise ValueError(f"unknown AODL edge relation {relation!r}")
    return {
        "id": eid,
        "relation": relation,
        "from": frm,
        "to": to,
        "fromPort": from_port,
        "toPort": to_port,
        "delivery": {
            "order": "ordered",
            "idempotent": True,
            "timeoutMs": 60_000,
            "maxRetries": 0,
        },
        "authority": {"grant": list(grant), "delegationDepth": 0},
        "provenance": {"sourceHash": schema_hash},
    }


def _promoted_routines(routines: Iterable[RoutineCandidate]) -> list[RoutineCandidate]:
    return [r for r in routines if r.status == "promoted"]


def _stage_node_id(stage: str) -> str:
    return _safe_id("stage-", stage)


def _stage_binding(stage: str, config: AodlBindingConfig) -> dict[str, Any]:
    explicit = config.stage_bindings.get(stage)
    if explicit is not None:
        return dict(explicit)
    # Provider/model choice is intentionally left unbound here.  Kerdoios or a
    # harness compiler may resolve this residual model stage later.
    return {
        "binding": "unbound",
        "selector": {"stage": stage},
    }


def _logical_stage(stage: str, config: AodlBindingConfig) -> str:
    role = str(config.stage_roles.get(stage, stage))
    return role


@dataclass(frozen=True)
class AodlRuntimeContract:
    """Runtime-facing view of a compiled AODL document.

    This is the inverse edge of the bridge: harness/z0int code can consume the
    AODL contract without learning the rest of the AODL schema.
    """

    graph_id: str
    revision: int
    capability_id: str
    harness_id: str
    route_order: tuple[str, ...]
    bindings: dict[str, Any]
    budgets: dict[str, Any]
    acceptance: dict[str, Any]
    source_hash: str


def compile_aodl(
    *,
    capability_id: str,
    cascade: CascadePolicy,
    routines: Sequence[RoutineCandidate] = (),
    config: AodlBindingConfig | None = None,
) -> dict[str, Any]:
    """Compile z0int runtime intent into a HOTL/AODL 0.2 document.

    The compiled document is intentionally split:

    * ``intentGraph`` / ``policies`` / ``constraints`` describe portable
      intent and Gamma budgets;
    * ``plan.bindings`` points those abstract nodes at z0int artifacts/runtime
      selectors without changing the IR ontology;
    * observed route/outcome activity belongs in ``eventLog`` and can be
      appended with :func:`route_event` / :func:`outcome_event`.
    """

    cfg = config or AodlBindingConfig()
    if cascade.capability_id != capability_id:
        raise ValueError("cascade capability_id does not match")

    promoted = _promoted_routines(routines)
    material = {
        "compiler": "z0int.aodl.v1",
        "capability_id": capability_id,
        "cascade": cascade.to_dict(),
        "routine_ids": sorted(r.routine_id for r in promoted),
        "harness_id": cfg.harness_id,
        "revision": cfg.revision,
        "budgets": cfg.budgets.to_dict(),
        "precision_floor": cfg.precision_floor,
        "stage_bindings": {k: dict(v) for k, v in sorted(cfg.stage_bindings.items())},
        "stage_roles": dict(sorted(cfg.stage_roles.items())),
        "include_routine_slot": cfg.include_routine_slot,
    }
    sh = source_hash(material)

    task_id = _safe_id("capability-", capability_id)
    nodes: list[dict[str, Any]] = [
        _node(
            task_id,
            "task",
            [_port("out", "out", "DecisionRequest")],
            ["request"],
            authority=["request"],
        ),
        _node(
            cfg.executor_id,
            "executor",
            [
                _port("in", "in", "DecisionRequest"),
                _port("route", "out", "StageRequest"),
                _port("decision", "out", "DecisionResult"),
            ],
            ["route", "evaluate"],
            authority=["route", "evaluate"],
        ),
        _node(
            cfg.verifier_id,
            "verifier",
            [
                _port("in", "in", "DecisionResult"),
                _port("out", "out", "VerifiedOutcome"),
            ],
            ["verify"],
            authority=["verify"],
        ),
        _node(
            cfg.receipt_store_id,
            "stateStore",
            [_port("in", "in", "VerifiedOutcome", classification="confidential")],
            ["store"],
            authority=["store"],
        ),
    ]
    edges: list[dict[str, Any]] = [
        _edge(
            "e-task-runtime",
            "dependency",
            task_id,
            cfg.executor_id,
            "out",
            "in",
            sh,
            grant=["route"],
        ),
        _edge(
            "e-runtime-verify",
            "verification",
            cfg.executor_id,
            cfg.verifier_id,
            "decision",
            "in",
            sh,
            grant=["verify"],
        ),
        _edge(
            "e-verify-receipts",
            "observation",
            cfg.verifier_id,
            cfg.receipt_store_id,
            "out",
            "in",
            sh,
            grant=["store"],
        ),
    ]

    bindings: dict[str, Any] = {
        cfg.executor_id: {
            "runtime": "z0int",
            "entrypoint": "preflight",
            "harnessId": cfg.harness_id,
        },
        cfg.verifier_id: {
            "runtime": "z0int",
            "entrypoint": "outcome",
        },
        cfg.receipt_store_id: {
            "runtime": "z0int",
            "uri": "z0int://receipts",
        },
    }

    route_order: list[str] = []
    if cfg.include_routine_slot:
        nodes.extend(
            [
                _node(
                    cfg.routine_artifact_id,
                    "artifact",
                    [_port("out", "out", "RoutineRegistry", classification="local_only")],
                    ["provide"],
                    authority=["provide"],
                ),
                _node(
                    cfg.routine_service_id,
                    "service",
                    [
                        _port("in", "in", "StageRequest"),
                        _port("registry", "in", "RoutineRegistry", classification="local_only"),
                        _port("out", "out", "DecisionResult"),
                    ],
                    ["evaluate"],
                    authority=["evaluate"],
                ),
            ]
        )
        edges.extend(
            [
                _edge(
                    "e-routine-artifact",
                    "artifact",
                    cfg.routine_artifact_id,
                    cfg.routine_service_id,
                    "out",
                    "registry",
                    sh,
                ),
                _edge(
                    "e-runtime-routine",
                    "allocation",
                    cfg.executor_id,
                    cfg.routine_service_id,
                    "route",
                    "in",
                    sh,
                    grant=["evaluate"],
                ),
                _edge(
                    "e-routine-verify",
                    "verification",
                    cfg.routine_service_id,
                    cfg.verifier_id,
                    "out",
                    "in",
                    sh,
                    grant=["verify"],
                ),
            ]
        )
        bindings[cfg.routine_artifact_id] = {
            "artifactType": "z0int.routine_registry.v1",
            "uri": "z0int://routines",
            "routineIds": sorted(r.routine_id for r in promoted),
            "enabled": bool(promoted),
        }
        bindings[cfg.routine_service_id] = {
            "runtime": "z0int",
            "entrypoint": "routine_registry.decide",
            "enabled": bool(promoted),
            "failOpen": "next_route_stage",
        }
        route_order.append(cfg.routine_service_id)

    seen_logical: set[str] = set()
    for stage in cascade.stages:
        logical = _logical_stage(stage.name, cfg)
        sid = _stage_node_id(logical)
        if sid in seen_logical:
            raise ValueError(f"multiple implementation stages map to logical AODL slot {logical!r}")
        seen_logical.add(sid)
        nodes.append(
            _node(
                sid,
                "model",
                [
                    _port("in", "in", "StageRequest"),
                    _port("out", "out", "DecisionResult"),
                ],
                ["infer"],
                authority=["infer"],
            )
        )
        edges.extend(
            [
                _edge(
                    f"e-runtime-{sid}",
                    "allocation",
                    cfg.executor_id,
                    sid,
                    "route",
                    "in",
                    sh,
                    grant=["infer"],
                ),
                _edge(
                    f"e-{sid}-verify",
                    "verification",
                    sid,
                    cfg.verifier_id,
                    "out",
                    "in",
                    sh,
                    grant=["verify"],
                ),
            ]
        )
        bindings[sid] = {
            **_stage_binding(stage.name, cfg),
            "implementationStage": stage.name,
            "logicalRole": logical,
            "enabled": True,
            "minConfidence": stage.min_confidence,
            "terminal": stage.terminal,
        }
        route_order.append(sid)

    final_node = _stage_node_id(_logical_stage(cascade.final_stage, cfg))
    policies = {
        "kinds": ["router", "sequence"],
        "fanIn": "any",
        "dynamic": {"allowed": False, "maxChildren": 0, "maxDepth": 0},
        "route": {
            "strategy": "first_eligible",
            "order": route_order,
            "eligibility": "compiled_plan_binding",
            "abstain": "next",
            "final": final_node,
        },
    }
    constraints = {
        "budgets": cfg.budgets.to_dict(),
        "termination": {"on": f"{cfg.verifier_id}.succeeded"},
        "acceptance": {
            "capabilityId": capability_id,
            "precisionFloor": cfg.precision_floor,
            "maxSuccessRegression": cascade.max_success_regression,
            "objective": cascade.objective,
        },
        "privacy": {
            "personalArtifacts": "local_only",
            "receipts": "confidential",
        },
    }
    plan = {
        "compiler": "z0int.aodl.v1",
        "harnessId": cfg.harness_id,
        "bindings": bindings,
        "route": {
            "order": route_order,
            "thresholds": {
                _stage_node_id(_logical_stage(s.name, cfg)): s.min_confidence
                for s in cascade.stages
                if not s.terminal
            },
            "final": final_node,
        },
        "sourceSchemas": {
            "cascade": cascade.schema,
            "routine": "z0int.routine_candidate.v1",
        },
        "note": "AODL owns intent/policy/budgets; z0int owns implementation; provider placement may be resolved by Kerdoios.",
    }
    doc = {
        "specVersion": SPEC_VERSION,
        "graphId": _safe_id("z0int-", capability_id),
        "revision": cfg.revision,
        "intentGraph": {"nodes": nodes, "edges": edges},
        "policies": policies,
        "constraints": constraints,
        "provenance": {"source": cfg.source, "sourceHash": sh},
        "plan": plan,
        "eventLog": [],
    }
    assert_basic_aodl_invariants(doc)
    return doc


def assert_basic_aodl_invariants(doc: Mapping[str, Any]) -> None:
    """Small local guard; AODL's own validator remains authoritative.

    This deliberately checks only the invariants z0int itself could violate and
    does not attempt to fork AODL's validator.
    """

    required = {"specVersion", "graphId", "revision", "intentGraph", "policies", "constraints", "provenance"}
    missing = required - set(doc)
    if missing:
        raise ValueError(f"AODL document missing {sorted(missing)}")
    if doc["specVersion"] != SPEC_VERSION:
        raise ValueError("z0int only emits HOTL/AODL 0.2")
    _require_id(str(doc["graphId"]))
    graph = doc["intentGraph"]
    nodes = list(graph["nodes"])
    edges = list(graph["edges"])
    index = {n["id"]: n for n in nodes}
    if len(index) != len(nodes):
        raise ValueError("duplicate AODL node id")
    for node in nodes:
        if node["kind"] not in AODL_NODE_KINDS:
            raise ValueError(f"non-AODL node kind {node['kind']!r}")
    for edge in edges:
        if edge["relation"] not in AODL_EDGE_RELATIONS:
            raise ValueError(f"non-AODL edge relation {edge['relation']!r}")
        if edge["from"] not in index or edge["to"] not in index:
            raise ValueError("AODL edge endpoint missing")
        fp = {p["id"]: p for p in index[edge["from"]]["ports"]}[edge["fromPort"]]
        tp = {p["id"]: p for p in index[edge["to"]]["ports"]}[edge["toPort"]]
        if fp["direction"] != "out" or tp["direction"] != "in":
            raise ValueError("AODL edge port direction invalid")
        if fp["schema"] != tp["schema"]:
            raise ValueError("AODL edge port schemas do not match")
    budgets = doc["constraints"].get("budgets")
    termination = doc["constraints"].get("termination")
    if not isinstance(budgets, dict) or not isinstance(termination, dict):
        raise ValueError("AODL Gamma requires budgets and termination")


def runtime_contract(doc: Mapping[str, Any]) -> AodlRuntimeContract:
    """Extract the z0int runtime contract from a compiled AODL document."""

    assert_basic_aodl_invariants(doc)
    plan = doc.get("plan")
    if not isinstance(plan, Mapping) or plan.get("compiler") != "z0int.aodl.v1":
        raise ValueError("AODL document has no z0int compiled plan")
    route = plan.get("route")
    bindings = plan.get("bindings")
    if not isinstance(route, Mapping) or not isinstance(bindings, Mapping):
        raise ValueError("z0int plan requires route and bindings")
    acceptance = doc.get("constraints", {}).get("acceptance", {})
    if not isinstance(acceptance, Mapping) or not acceptance.get("capabilityId"):
        raise ValueError("AODL constraints.acceptance.capabilityId is required")
    return AodlRuntimeContract(
        graph_id=str(doc["graphId"]),
        revision=int(doc["revision"]),
        capability_id=str(acceptance["capabilityId"]),
        harness_id=str(plan.get("harnessId") or ""),
        route_order=tuple(str(x) for x in route.get("order") or ()),
        bindings={str(k): dict(v) for k, v in bindings.items() if isinstance(v, Mapping)},
        budgets=dict(doc.get("constraints", {}).get("budgets") or {}),
        acceptance=dict(acceptance),
        source_hash=str(doc.get("provenance", {}).get("sourceHash") or ""),
    )


def _event_id(kind: str, trace_id: str, revision: int, payload: Mapping[str, Any]) -> str:
    digest = hashlib.blake2b(
        _canonical({"kind": kind, "trace": trace_id, "revision": revision, "payload": payload}).encode("utf-8"),
        digest_size=10,
    ).hexdigest()
    return f"{kind}-{digest}"


def route_event(
    *,
    trace_id: str,
    source_hash_hex: str,
    revision: int,
    stage: str,
    capability_id: str,
    confidence: float | None = None,
    routine_id: str | None = None,
    causal_parents: Sequence[str] = (),
) -> dict[str, Any]:
    payload: dict[str, Any] = {
        "traceId": trace_id,
        "capabilityId": capability_id,
        "stage": stage,
    }
    if confidence is not None:
        payload["confidence"] = float(confidence)
    if routine_id is not None:
        payload["routineId"] = routine_id
    return {
        "eventId": _event_id("route", trace_id, revision, payload),
        "type": "route",
        "sourceHash": source_hash_hex,
        "revision": revision,
        "causalParents": list(causal_parents),
        "payload": payload,
    }


def outcome_event(
    *,
    trace_id: str,
    source_hash_hex: str,
    revision: int,
    success: bool,
    verifier: str,
    premium_tokens: int = 0,
    total_tokens: int = 0,
    latency_ms: float | None = None,
    causal_parents: Sequence[str] = (),
) -> dict[str, Any]:
    payload: dict[str, Any] = {
        "traceId": trace_id,
        "verified": True,
        "success": bool(success),
        "verifier": verifier,
        "spend": {
            "premiumTokens": max(0, int(premium_tokens)),
            "tokens": max(0, int(total_tokens)),
        },
    }
    if latency_ms is not None:
        payload["latencyMs"] = max(0.0, float(latency_ms))
    return {
        "eventId": _event_id("stateUpdate", trace_id, revision, payload),
        "type": "stateUpdate",
        "sourceHash": source_hash_hex,
        "revision": revision,
        "causalParents": list(causal_parents),
        "payload": payload,
    }


def append_event(doc: dict[str, Any], event: Mapping[str, Any]) -> dict[str, Any]:
    if event.get("sourceHash") != doc.get("provenance", {}).get("sourceHash"):
        raise ValueError("event sourceHash must match compiled AODL document")
    out = json.loads(json.dumps(doc))
    out.setdefault("eventLog", []).append(dict(event))
    return out


def load_routines_jsonl(path: Path) -> list[RoutineCandidate]:
    rows: list[RoutineCandidate] = []
    with path.open(encoding="utf-8") as fh:
        for line in fh:
            line = line.strip()
            if line:
                rows.append(RoutineCandidate.from_dict(json.loads(line)))
    return rows


def _main(argv: list[str] | None = None) -> int:
    parser = argparse.ArgumentParser(prog="python -m z0int.aodl")
    sub = parser.add_subparsers(dest="cmd", required=True)

    comp = sub.add_parser("compile")
    comp.add_argument("--capability", required=True)
    comp.add_argument("--cascade", type=Path, required=True)
    comp.add_argument("--routines", type=Path)
    comp.add_argument("--output", type=Path, required=True)
    comp.add_argument("--harness", default="omp")
    comp.add_argument("--tokens", type=int)
    comp.add_argument("--premium-tokens", type=int)
    comp.add_argument("--latency-ms", type=float)
    comp.add_argument("--precision-floor", type=float, default=0.95)

    args = parser.parse_args(argv)
    if args.cmd == "compile":
        cascade = CascadePolicy.from_dict(json.loads(args.cascade.read_text(encoding="utf-8")))
        routines = load_routines_jsonl(args.routines) if args.routines else []
        cfg = AodlBindingConfig(
            harness_id=args.harness,
            precision_floor=args.precision_floor,
            budgets=AodlBudgets(
                tokens=args.tokens,
                premium_tokens=args.premium_tokens,
                latency_ms=args.latency_ms,
            ),
        )
        doc = compile_aodl(capability_id=args.capability, cascade=cascade, routines=routines, config=cfg)
        args.output.parent.mkdir(parents=True, exist_ok=True)
        args.output.write_text(json.dumps(doc, indent=2) + "\n", encoding="utf-8")
        print(json.dumps({"ok": True, "output": str(args.output), "sourceHash": doc["provenance"]["sourceHash"]}, indent=2))
        return 0
    raise AssertionError(args.cmd)


if __name__ == "__main__":
    raise SystemExit(_main())
