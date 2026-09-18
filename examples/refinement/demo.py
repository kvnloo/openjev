"""Deterministic synthetic repair integration test. No model or network required."""
from __future__ import annotations

import argparse
import json
from dataclasses import replace
from pathlib import Path

from z0int.abab import CreditGates, ExperimentArchive, ExperimentMetrics, ExperimentProposal, ExperimentRecord
from z0int.aodl import append_event, assert_basic_aodl_invariants, outcome_event, route_event
from z0int.cascade import CascadePolicy, StagePolicy
from z0int.refinement import RepairConfig, RepairTrialStore, activate_repair, export_shadow_plan, propose_repair, rollback_repair, score_repair
from z0int.routines import LiteralPredicate, RoutineCandidate, RoutineRegistry, RoutineRule

CAP = "fixture.needs_verification"


def oracle(features):
    """Known toy contract, not an LLM, a Blender verifier, or a learned teacher."""
    return "VERIFY" if features["prev_family"] == "EDIT" and features["phase"] == "batch" else "INSPECT"


def cohort(split, start, n=8, prefix=None, poisoned=False):
    rows = []
    prefix = prefix or split
    for s in range(n):
        for i, (prev, phase) in enumerate((("EDIT", "batch"), ("EDIT", "batch"), ("EDIT", "interactive"), ("READ", "batch"))):
            features = {"prev_family": prev, "tool_ok": True, "phase": phase}
            rows.append({
                "capability_id": CAP, "split": split, "episode_id": f"{prefix}-{s}-{i}",
                "session_id": f"{prefix}-{s}", "features": features,
                "target": "INSPECT" if poisoned else oracle(features),
                "session_started_at": start, "features_at": start+1, "decision_at": start+2, "observed_at": start+3,
                "evidence_level": "L2_outcome", "verified": True, "verifier_id": "fixture-oracle-v1",
            })
    return rows


def replay(registry, rows):
    calls = 0
    correct = 0
    for row in rows:
        decision = registry.decide(CAP, row["features"])
        if decision.matched:
            output = decision.output
        else:
            calls += 1
            output = oracle(row["features"])
        correct += output == row["target"]
    return {"rows": len(rows), "correct": correct, "fallback_calls": calls,
            "local_hits": len(rows)-calls, "real_llm_tokens": None}


def run(output: Path):
    output.mkdir(parents=True, exist_ok=False)
    parent = RoutineCandidate(
        capability_id=CAP,
        rule=RoutineRule((LiteralPredicate("prev_family", "eq", "EDIT"), LiteralPredicate("tool_ok", "eq", True))),
        output="VERIFY", source_specialist="fixture-mb", source_generation=1,
        evidence_level="L2_outcome", precision_floor=.95, min_sessions=3, status="promoted",
    )
    # Future traffic for the OLD parent becomes OPEN repair evidence, never the
    # new child's evaluation. The new sealed/future cohorts start after freeze.
    train, dev = cohort("train", 100), cohort("dev", 200)
    registry = RoutineRegistry([parent])
    registry.observe_future([dict(r, split="future") for r in train])
    parent = registry.candidates[0]
    assert parent.status == "demoted"
    cfg = RepairConfig(allowed_features=("phase",), min_train_matches=12, min_dev_matches=12)
    proposal = propose_repair(parent, train, dev, config=cfg, frozen_at=400)
    store = RepairTrialStore(output / "private-credit.sqlite3")
    sealed = score_repair(proposal, cohort("sealed", 500, 96), phase="sealed", store=store, evaluated_at=600)
    future = score_repair(proposal, cohort("future", 700, 96), phase="future", store=store, evaluated_at=800)
    assert sealed["passed"] and future["passed"]
    repaired = activate_repair(registry, proposal, store=store, approved=True)
    rolled = rollback_repair(repaired, proposal["proposal_id"])
    acceptance = cohort("future", 900, 96, prefix="rollout")
    before, after, rollback = (replay(r, acceptance) for r in (registry, repaired, rolled))
    assert before["correct"] == after["correct"] == rollback["correct"] == len(acceptance)
    assert after["fallback_calls"] < before["fallback_calls"] == rollback["fallback_calls"]

    # Negative control: the unchanged candidate cannot survive reversed labels.
    negative_store = RepairTrialStore(output / "negative-control-credit.sqlite3")
    negative = score_repair(proposal, cohort("sealed", 500, 96, prefix="negative", poisoned=True),
                            phase="sealed", store=negative_store, evaluated_at=600)
    assert not negative["passed"]

    cascade = CascadePolicy(CAP, (StagePolicy("mb", .95), StagePolicy("frontier", 0, terminal=True)),
                            final_stage="frontier", min_success_rate=.99, max_success_regression=.01, status="promoted")
    doc = export_shadow_plan(proposal, cascade)
    route = route_event(trace_id="fixture-route", source_hash_hex=doc["provenance"]["sourceHash"], revision=0,
                        stage="routine-service", capability_id=CAP, routine_id=proposal["child"]["routine_id"])
    route["payload"]["fixtureOnly"] = True
    doc = append_event(doc, route)
    outcome = outcome_event(trace_id="fixture-route", source_hash_hex=doc["provenance"]["sourceHash"], revision=0,
                            success=True, verifier="fixture-oracle-v1", causal_parents=(route["eventId"],))
    outcome["payload"]["fixtureOnly"] = True
    doc = append_event(doc, outcome)
    assert_basic_aodl_invariants(doc)
    assert not doc["plan"]["deployment"]["trafficEligible"]

    # This integrates the existing archive. It deliberately does NOT invent EIG
    # or claim transfer: this agenda was user-selected, not an acquired hypothesis.
    archive = ExperimentArchive()
    for name, receipt in (("repair", future), ("negative-control", negative)):
        passed = receipt["passed"]
        p = ExperimentProposal(
            id=f"{proposal['proposal_id']}:{name}", hypothesis_id="narrow-the-failing-phase", niche=CAP,
            summary="Synthetic narrowed-rule credit", expected_information_gain=0, impact=0, decision_change=1,
            transferability=0, estimated_cost=0, learned=False, parents=(parent.routine_id,),
        )
        archive.append(ExperimentRecord(
            proposal=p, status="credited" if passed else "rejected",
            gates=CreditGates(validity=True, activation=receipt["metrics"]["matched"] > 0, credit=passed,
                              notes=("synthetic fixture, not product credit",)),
            metrics=ExperimentMetrics(safe_coverage=receipt["metrics"]["coverage"] if passed else None),
            actual_cost=0, failure_class=None if passed else "repair_rejected",
            evidence=(receipt["receipt_id"],),
        ))
    archive.to_jsonl(output / "fixture-archive.jsonl")
    repaired.to_jsonl(output / "repaired-registry.jsonl")
    rolled.to_jsonl(output / "rolled-back-registry.jsonl")
    report = {
        "schema": "z0int.repair_demo.v1", "evidence": "SYNTHETIC_INTEGRATION_FIXTURE_ONLY",
        "parent_demoted": True, "child_rule": proposal["child"]["rule"],
        "sealed": sealed["metrics"], "future": future["metrics"],
        "negative_control_rejected": not negative["passed"],
        "replay_after_demotion": before, "replay_after_repair": after, "replay_after_rollback": rollback,
        "production_traffic_enabled": False,
        "real_token_savings": None, "real_task_quality": None, "gpu_training_performed": False,
        "notes": "384 toy cases exercise real Python routing and fallback calls. Targets are deterministic synthetic labels; no LLM or Blender was evaluated.",
    }
    for name, value in (("proposal.json", proposal), ("sealed-credit.json", sealed), ("future-credit.json", future),
                        ("aodl-shadow.json", doc), ("report.json", report)):
        (output / name).write_text(json.dumps(value, indent=2, sort_keys=True) + "\n")
    return report


if __name__ == "__main__":
    parser = argparse.ArgumentParser()
    parser.add_argument("--output", type=Path, required=True)
    args = parser.parse_args()
    result = run(args.output)
    print(json.dumps({"report": str(args.output / "report.json"), "evidence": result["evidence"],
                      "fallback_calls_before": result["replay_after_demotion"]["fallback_calls"],
                      "fallback_calls_after": result["replay_after_repair"]["fallback_calls"]}, indent=2))
