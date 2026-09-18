"""Production preflight owned by z0intelligence.

No Evolution Lab import or subprocess on the live path. Production may only
consume promoted routines/cascades, installed specialist artifacts, and
DecisionBackends. Missing coverage → route=model (residual).
"""

from __future__ import annotations

import json
import re
import time
from dataclasses import asdict, dataclass
from pathlib import Path
from typing import Any, Literal

from . import paths

Route = Literal["routine", "specialist", "local_model", "model"]
SCHEMA = "z0int.preflight.v1"


@dataclass(frozen=True)
class PreflightDecision:
    capability_id: str
    route: Route
    prediction: str | None = None
    confidence: float | None = None
    work_requirement: dict[str, Any] | None = None
    artifact_ids: tuple[str, ...] = ()
    context_packet_id: str | None = None
    evidence_level: str = "L0_imitation"
    baseline_input_tokens: int | None = None
    baseline_output_tokens: int | None = None
    estimated_frontier_tokens_avoided: int = 0
    label: str | None = None
    source: str = "z0int.preflight"
    reasons: tuple[str, ...] = ()

    def to_dict(self) -> dict[str, Any]:
        d = asdict(self)
        d["schema"] = SCHEMA
        d["artifact_ids"] = list(self.artifact_ids)
        d["reasons"] = list(self.reasons)
        return d


def _estimate_tokens(text: str) -> int:
    return max(1, len(text) // 4)


def _infer_capability(prompt: str) -> str:
    p = prompt.lower()
    if any(k in p for k in ("recover", "retry", "sandbox", "escalate", "page_human")):
        return "coding.recovery_action"
    if any(k in p for k in ("verify", "test", "pytest", "ci ")):
        return "coding.needs_verification"
    if any(k in p for k in ("review", "pr ", "pull request")):
        return "coding.review"
    if any(k in p for k in ("next action", "what should", "delegate")):
        return "coding.next_action"
    return "coding.generic"


def _read_json(path: Path) -> Any | None:
    if not path.is_file():
        return None
    try:
        return json.loads(path.read_text(encoding="utf-8"))
    except Exception:
        return None


def _match_promoted_routine(prompt: str, capability_id: str) -> dict[str, Any] | None:
    """Thin JSON routines under ~/.z0int — status must be promoted.

    Shape (list or {routines: [...]}):
      {
        "routine_id": "...",
        "capability_id": "...",
        "status": "promoted",
        "action": "VERIFY",
        "contains_any": ["verify", "pytest"]   # optional
      }
    """
    root = paths.home()
    for p in (
        root / "specialists" / "routine_registry.json",
        root / "state" / "routine_registry.json",
        root / "config" / "routine_registry.json",
    ):
        data = _read_json(p)
        if data is None:
            continue
        rows = data if isinstance(data, list) else (data.get("routines") or data.get("candidates") or [])
        pl = prompt.lower()
        for row in rows:
            if not isinstance(row, dict):
                continue
            if row.get("status") != "promoted":
                continue
            cap = row.get("capability_id")
            if cap and cap != capability_id and capability_id != "coding.generic":
                continue
            needles = row.get("contains_any") or row.get("match_any") or []
            if needles and not any(str(n).lower() in pl for n in needles):
                continue
            action = row.get("action") or row.get("output") or row.get("prediction")
            if not action or str(action) in ("model", "frontier"):
                continue
            return row
    return None


def _list_promoted_artifact_ids(capability_id: str) -> list[str]:
    from .artifacts import list_artifacts

    out: list[str] = []
    for art in list_artifacts():
        promo = (art.get("promotion") or {}).get("status")
        if promo != "promoted":
            continue
        acap = art.get("capability_id")
        if acap and acap != capability_id and capability_id != "coding.generic":
            continue
        aid = art.get("artifact_id")
        if aid:
            out.append(str(aid))
    return out


def _try_local_backend(prompt: str, capability_id: str) -> PreflightDecision | None:
    try:
        from .backends.registry import create_backend, list_backend_specs
    except Exception:
        return None
    for spec in list_backend_specs():
        bid = getattr(spec, "id", None) or (spec.get("id") if isinstance(spec, dict) else None)
        if not bid:
            continue
        bid_s = str(bid)
        if "nanojev" not in bid_s and "local" not in bid_s:
            continue
        try:
            be = create_backend(bid_s)
            decide = getattr(be, "decide", None) or getattr(be, "predict", None)
            if decide is None:
                continue
            try:
                raw = decide(prompt)  # type: ignore[misc]
            except TypeError:
                raw = decide({"prompt": prompt, "capability_id": capability_id})  # type: ignore[misc]
            if not isinstance(raw, dict):
                continue
            conf = raw.get("confidence", raw.get("p", raw.get("probability")))
            conf_f = float(conf) if conf is not None else None
            label = raw.get("label") or raw.get("prediction") or raw.get("action")
            if conf_f is not None and conf_f >= 0.90 and label is not None:
                return PreflightDecision(
                    capability_id=capability_id,
                    route="local_model",
                    prediction=str(label),
                    confidence=conf_f,
                    label=str(label),
                    evidence_level=str(raw.get("evidence_level") or "L1_local"),
                    reasons=("local_backend_high_conf", bid_s),
                )
        except Exception:
            continue
    return None


def run_preflight(
    prompt: str,
    *,
    capability_id: str | None = None,
    context_packet_id: str | None = None,
) -> PreflightDecision:
    """Production preflight. Never imports Evolution Lab."""
    _ = time.perf_counter()
    cap = capability_id or _infer_capability(prompt)
    reasons: list[str] = []
    baseline_in = _estimate_tokens(prompt)

    matched = _match_promoted_routine(prompt, cap)
    if matched is not None:
        action = str(matched.get("action") or matched.get("output") or matched.get("prediction"))
        reasons.append("promoted_routine")
        return PreflightDecision(
            capability_id=str(matched.get("capability_id") or cap),
            route="routine",
            prediction=action,
            confidence=float(matched["confidence"]) if matched.get("confidence") is not None else None,
            label=action,
            context_packet_id=context_packet_id,
            evidence_level="L2_routine",
            baseline_input_tokens=baseline_in,
            baseline_output_tokens=max(16, baseline_in // 8),
            estimated_frontier_tokens_avoided=baseline_in + max(16, baseline_in // 8),
            reasons=tuple(reasons),
        )

    arts = _list_promoted_artifact_ids(cap)
    if arts:
        reasons.append("promoted_specialist_artifact")
        return PreflightDecision(
            capability_id=cap,
            route="specialist",
            artifact_ids=tuple(arts),
            context_packet_id=context_packet_id,
            evidence_level="L2_specialist",
            baseline_input_tokens=baseline_in,
            baseline_output_tokens=max(16, baseline_in // 8),
            estimated_frontier_tokens_avoided=baseline_in // 2,
            reasons=tuple(reasons),
        )

    local = _try_local_backend(prompt, cap)
    if local is not None:
        return local

    reasons.append("no_promoted_coverage")
    work = {
        "mode": "balanced",
        "coding": 1.0,
        "reasoning": 0.5,
        "capability_id": cap,
        "context_tokens": baseline_in,
    }
    return PreflightDecision(
        capability_id=cap,
        route="model",
        work_requirement=work,
        context_packet_id=context_packet_id,
        evidence_level="L0_residual",
        baseline_input_tokens=baseline_in,
        baseline_output_tokens=max(32, baseline_in // 4),
        estimated_frontier_tokens_avoided=0,
        reasons=tuple(reasons),
    )


def preflight_dict(prompt: str, **kwargs: Any) -> dict[str, Any]:
    """Bridge-compatible dict (legacy keys preserved)."""
    d = run_preflight(prompt, **kwargs)
    out = d.to_dict()
    out["p"] = d.confidence
    out["ok"] = True
    return out
