from __future__ import annotations

import copy
import json
from dataclasses import replace
from pathlib import Path

import pytest

from z0int.aodl import AodlBindingConfig, compile_aodl, assert_basic_aodl_invariants
from z0int.cascade import CascadePolicy, StagePolicy
from z0int.refinement import (
    RepairConfig, RepairTrialStore, activate_repair, canonical, digest,
    export_shadow_plan, propose_repair, rollback_repair, score_repair, validate_proposal,
)
from z0int.routines import LiteralPredicate, RoutineCandidate, RoutineRegistry, RoutineRule

CAP = "coding.needs_verification"


def parent() -> RoutineCandidate:
    return RoutineCandidate(
        capability_id=CAP,
        rule=RoutineRule((LiteralPredicate("prev_family", "eq", "EDIT"), LiteralPredicate("tool_ok", "eq", True))),
        output="VERIFY", source_specialist="mb-test", source_generation=1,
        evidence_level="L2_outcome", precision_floor=0.95, min_sessions=3, status="demoted",
    )


def cohort(split: str, start: int, n: int = 8, *, reversed_labels: bool = False) -> list[dict]:
    rows = []
    for session in range(n):
        for i, (phase, prev) in enumerate((("batch", "EDIT"), ("batch", "EDIT"), ("interactive", "EDIT"), ("batch", "READ"))):
            target = "VERIFY" if prev == "EDIT" and phase == "batch" else "INSPECT"
            if reversed_labels and prev == "EDIT" and phase == "batch":
                target = "INSPECT"
            rows.append({
                "capability_id": CAP, "split": split, "episode_id": f"{split}-{session}-{i}",
                "session_id": f"{split}-{session}", "session_started_at": start,
                "features_at": start + 1, "decision_at": start + 2, "observed_at": start + 3,
                "features": {"prev_family": prev, "tool_ok": True, "phase": phase},
                "target": target, "evidence_level": "L2_outcome", "verified": True,
                "verifier_id": "synthetic-task-contract-v1",
            })
    return rows


def config(**kwargs) -> RepairConfig:
    return RepairConfig(allowed_features=("phase",), min_train_matches=12, min_dev_matches=12, **kwargs)


def proposal(**kwargs) -> dict:
    return propose_repair(parent(), cohort("train", 100), cohort("dev", 200), config=config(), frozen_at=400, **kwargs)


def cascade() -> CascadePolicy:
    return CascadePolicy(capability_id=CAP, stages=(StagePolicy("mb", .95), StagePolicy("frontier", 0, terminal=True)),
                         final_stage="frontier", min_success_rate=.99, max_success_regression=.01, status="promoted")


def credits(tmp_path: Path):
    p = proposal()
    store = RepairTrialStore(tmp_path / "trials.sqlite3")
    sealed = score_repair(p, cohort("sealed", 500, 96), phase="sealed", store=store, evaluated_at=600)
    future = score_repair(p, cohort("future", 700, 96), phase="future", store=store, evaluated_at=800)
    return p, store, sealed, future


def test_repair_preserves_parent_and_adds_one_positive_guard():
    p = proposal()
    par, child, cfg = validate_proposal(p)
    assert child is not None
    assert child.rule.all[:-1] == par.rule.all
    assert child.rule.all[-1] == LiteralPredicate("phase", "eq", "batch")
    assert child.output == par.output
    assert p["diagnosis"]["counterexamples"] == 8
    assert p["dev_metrics"]["precision"] == 1
    assert child.status == "candidate" and child.sealed is None
    assert not child.rule.matches({"prev_family": "EDIT", "tool_ok": True, "phase": "unseen"})
    assert not child.rule.matches({"prev_family": "EDIT", "tool_ok": True})


def test_deterministic_proposal_under_input_reordering():
    first = proposal()
    second = propose_repair(parent(), list(reversed(cohort("train", 100))), list(reversed(cohort("dev", 200))),
                            config=config(), frozen_at=400)
    assert first == second


def test_new_guard_values_are_discovered_on_train_only():
    train = cohort("train", 100)
    dev = cohort("dev", 200)
    for r in dev:
        if r["features"]["phase"] == "batch":
            r["features"]["phase"] = "only_in_dev"
    p = propose_repair(parent(), train, dev, config=config(), frozen_at=400)
    assert p["status"] == "no_update" and p["child"] is None


def test_no_guessed_guard_when_features_cannot_explain_failure():
    train, dev = cohort("train", 100), cohort("dev", 200)
    for r in train + dev:
        r["features"]["phase"] = "same"
    p = propose_repair(parent(), train, dev, config=config(), frozen_at=400)
    assert p["status"] == "no_update"


@pytest.mark.parametrize("field,value", [("verified", False), ("evidence_level", "L1_teacher"),
                                          ("verifier_id", ""), ("session_id", ""), ("episode_id", "")])
def test_weak_or_unattributed_evidence_is_rejected(field, value):
    rows = cohort("train", 100)
    rows[0][field] = value
    with pytest.raises(ValueError):
        propose_repair(parent(), rows, cohort("dev", 200), config=config(), frozen_at=400)


def test_duplicate_rows_and_cross_split_sessions_rejected():
    train, dev = cohort("train", 100), cohort("dev", 200)
    with pytest.raises(ValueError, match="duplicate"):
        propose_repair(parent(), train + [train[0]], dev, config=config(), frozen_at=400)
    for r in dev:
        r["session_id"] = r["session_id"].replace("dev", "train")
    with pytest.raises(ValueError, match="overlap"):
        propose_repair(parent(), train, dev, config=config(), frozen_at=400)


def test_post_decision_feature_timestamp_rejected():
    train = cohort("train", 100)
    train[0]["features_at"] = train[0]["decision_at"] + 1
    with pytest.raises(ValueError, match="features must exist"):
        propose_repair(parent(), train, cohort("dev", 200), config=config(), frozen_at=400)


@pytest.mark.parametrize("value", [float("nan"), float("inf"), float("-inf")])
def test_nonfinite_predicates_fail_closed(value):
    with pytest.raises(ValueError):
        LiteralPredicate("x", "eq", value)
    with pytest.raises(ValueError):
        LiteralPredicate("x", "ge", value)
    assert not LiteralPredicate("x", "ge", 0).matches({"x": value})


def test_boolean_guard_never_accepts_numeric_one():
    guard = LiteralPredicate("tool_ok", "eq", True)
    assert guard.matches({"tool_ok": True})
    assert not guard.matches({"tool_ok": 1})


def test_only_demoted_outcome_backed_parents_can_be_repaired():
    for p in (replace(parent(), status="promoted"), replace(parent(), evidence_level="L1_teacher")):
        with pytest.raises(ValueError):
            propose_repair(p, cohort("train", 100), cohort("dev", 200), config=config(), frozen_at=400)


def test_prospectively_frozen_sealed_and_future_credit(tmp_path):
    p, store, sealed, future = credits(tmp_path)
    assert sealed["passed"] and future["passed"]
    assert sealed["metrics"]["all_correct_session_lower_95"] > .95
    assert future["prior_receipt_id"] == sealed["receipt_id"]
    assert sealed["whole_task_success"] is None and sealed["measured_token_savings"] is None
    assert "features" not in canonical(sealed)


def test_future_cannot_skip_sealed_or_reuse_its_sessions(tmp_path):
    p = proposal()
    store = RepairTrialStore(tmp_path / "trials.sqlite3")
    with pytest.raises(ValueError, match="sealed receipt"):
        score_repair(p, cohort("future", 700, 96), phase="future", store=store, evaluated_at=800)
    score_repair(p, cohort("sealed", 500, 96), phase="sealed", store=store, evaluated_at=600)
    rows = cohort("future", 700, 96)
    for r in rows:
        r["session_id"] = r["session_id"].replace("future", "sealed")
    with pytest.raises(ValueError, match="already consumed"):
        score_repair(p, rows, phase="future", store=store, evaluated_at=800)


def test_evaluation_cannot_reuse_open_session_even_with_new_timestamp(tmp_path):
    rows = cohort("sealed", 500, 96)
    rows[0]["session_id"] = "train-0"
    with pytest.raises(ValueError, match="overlap"):
        score_repair(proposal(), rows, phase="sealed", store=RepairTrialStore(tmp_path / "trials.sqlite3"), evaluated_at=600)


def test_evaluation_sessions_must_start_after_freeze(tmp_path):
    with pytest.raises(ValueError, match="new sessions"):
        score_repair(proposal(), cohort("sealed", 300, 96), phase="sealed",
                     store=RepairTrialStore(tmp_path / "trials.sqlite3"), evaluated_at=600)


def test_perfect_small_cohort_does_not_claim_95_percent_reliability(tmp_path):
    cfg = config(min_eval_matches=1, min_eval_sessions=1, max_session_share=1)
    p = propose_repair(parent(), cohort("train", 100), cohort("dev", 200), config=cfg, frozen_at=400)
    r = score_repair(p, cohort("sealed", 500, 3), phase="sealed",
                     store=RepairTrialStore(tmp_path / "trials.sqlite3"), evaluated_at=600)
    assert r["metrics"]["precision"] == 1.0
    assert not r["passed"] and "session_uncertainty" in r["reasons"]


def test_bad_holdout_consumes_attempt_and_cannot_be_shopped(tmp_path):
    store = RepairTrialStore(tmp_path / "trials.sqlite3")
    rows = cohort("sealed", 500, 96, reversed_labels=True)
    rejected = score_repair(proposal(), rows, phase="sealed", store=store, evaluated_at=600)
    assert not rejected["passed"]
    other = propose_repair(parent(), cohort("train", 100), cohort("dev", 200), config=config(), frozen_at=401)
    with pytest.raises(ValueError, match="already consumed"):
        score_repair(other, rows, phase="sealed", store=store, evaluated_at=600)


def test_receipts_are_idempotent_and_immutable_across_restarts(tmp_path):
    p, store, sealed, future = credits(tmp_path)
    restarted = RepairTrialStore(store.path)
    again = score_repair(p, cohort("sealed", 500, 96), phase="sealed", store=restarted, evaluated_at=900)
    assert again == sealed
    changed = cohort("sealed", 500, 96)
    changed[0]["target"] = "DIFFERENT"
    with pytest.raises(ValueError, match="different cohort"):
        score_repair(p, changed, phase="sealed", store=restarted, evaluated_at=900)


def test_invalid_second_feature_or_output_tampering_is_rejected():
    p = proposal()
    p["child"]["output"] = "WRONG"
    with pytest.raises(ValueError, match="hash"):
        validate_proposal(p)
    p.pop("proposal_id")
    p["proposal_id"] = digest(p)
    with pytest.raises(ValueError, match="cannot change"):
        validate_proposal(p)


def test_activation_requires_approval_both_receipts_and_unchanged_parent(tmp_path):
    p, store, sealed, future = credits(tmp_path)
    registry = RoutineRegistry([parent()])
    with pytest.raises(ValueError, match="approval"):
        activate_repair(registry, p, store=store)
    with pytest.raises(ValueError, match="parent changed"):
        activate_repair(RoutineRegistry([replace(parent(), status="promoted")]), p, store=store, approved=True)
    updated = activate_repair(registry, p, store=store, approved=True)
    assert len(registry.candidates) == 1  # no in-place mutation
    assert len(updated.candidates) == 2
    assert updated.candidates[0].status == "demoted"
    assert activate_repair(updated, p, store=store, approved=True).candidates == updated.candidates
    assert updated.decide(CAP, {"prev_family": "EDIT", "tool_ok": True, "phase": "batch"}).matched
    assert not updated.decide(CAP, {"prev_family": "EDIT", "tool_ok": True, "phase": "interactive"}).matched
    assert not updated.decide("another-capability", {"prev_family": "EDIT", "tool_ok": True, "phase": "batch"}).matched


def test_rollback_keeps_bad_parent_disabled(tmp_path):
    p, store, *_ = credits(tmp_path)
    updated = activate_repair(RoutineRegistry([parent()]), p, store=store, approved=True)
    rolled = rollback_repair(updated, p["proposal_id"])
    assert all(c.status == "demoted" for c in rolled.candidates)
    assert not rolled.decide(CAP, {"prev_family": "EDIT", "tool_ok": True, "phase": "batch"}).matched
    assert any(c.status == "promoted" for c in updated.candidates)


def test_aodl_new_composition_is_shadow_only_and_changes_only_plan():
    p = proposal()
    doc = export_shadow_plan(p, cascade())
    assert_basic_aodl_invariants(doc)
    assert doc["plan"]["deployment"]["mode"] == "shadow"
    assert doc["plan"]["deployment"]["trafficEligible"] is False
    artifact = doc["plan"]["bindings"]["routine-registry"]
    assert artifact["enabled"] and artifact["routineIds"] == [p["child"]["routine_id"]]
    assert artifact["routineArtifacts"][p["child"]["routine_id"]]["status"] == "candidate"
    baseline = compile_aodl(capability_id=CAP, cascade=cascade())
    assert doc["intentGraph"] == baseline["intentGraph"]
    assert doc["provenance"]["sourceHash"] == baseline["provenance"]["sourceHash"]
    assert doc["plan"]["planHash"] != baseline["plan"]["planHash"]
    with pytest.raises(ValueError, match="shadow-only"):
        compile_aodl(capability_id=CAP, cascade=cascade(), config=AodlBindingConfig(include_candidate_routines_in_shadow=True))


def test_aodl_plan_hash_binds_mode_not_only_thresholds():
    prod = compile_aodl(capability_id=CAP, cascade=cascade())
    shadow = compile_aodl(capability_id=CAP, cascade=replace(cascade(), status="candidate"),
                          config=AodlBindingConfig(allow_uncredited_shadow=True))
    assert prod["provenance"]["sourceHash"] == shadow["provenance"]["sourceHash"]
    assert prod["plan"]["planHash"] != shadow["plan"]["planHash"]


def test_shadow_binding_has_an_actual_advisory_entrypoint():
    p = proposal()
    child = RoutineCandidate.from_dict(p["child"])
    registry = RoutineRegistry([child])
    features = {"prev_family": "EDIT", "tool_ok": True, "phase": "batch"}
    assert not registry.decide(CAP, features).matched
    assert registry.decide_shadow(CAP, features).matched
    assert registry.decide_shadow(CAP, features).reason == "shadow_routine_match"
    assert child.status == "candidate"
    doc = export_shadow_plan(p, cascade())
    assert doc["plan"]["bindings"]["routine-service"]["entrypoint"] == "routine_registry.decide_shadow"


def test_concurrent_candidates_cannot_consume_the_same_cohort(tmp_path):
    from concurrent.futures import ThreadPoolExecutor
    store = RepairTrialStore(tmp_path / "trials.sqlite3")
    p = proposal()
    second = propose_repair(parent(), cohort("train", 100), cohort("dev", 200), config=config(), frozen_at=401)
    def run(candidate):
        try:
            return score_repair(candidate, cohort("sealed", 500, 96), phase="sealed", store=store, evaluated_at=600)["passed"]
        except ValueError:
            return False
    with ThreadPoolExecutor(max_workers=2) as executor:
        results = list(executor.map(run, (p, second)))
    assert sorted(results) == [False, True]


def test_idempotent_activation_does_not_allow_resurrected_parent(tmp_path):
    p, store, *_ = credits(tmp_path)
    updated = activate_repair(RoutineRegistry([parent()]), p, store=store, approved=True)
    corrupted = RoutineRegistry([replace(c, status="promoted") if c.routine_id == parent().routine_id else c
                                 for c in updated.candidates])
    with pytest.raises(ValueError, match="parent changed"):
        activate_repair(corrupted, p, store=store, approved=True)


def test_numeric_guard_uses_existing_ast_without_generated_code():
    train, dev = cohort("train", 100), cohort("dev", 200)
    for rows in (train, dev):
        for i, r in enumerate(rows):
            depth = i % 4
            r["features"] = {"prev_family": "EDIT", "tool_ok": True, "queue_depth": depth}
            r["target"] = "VERIFY" if depth < 2 else "INSPECT"
    cfg = replace(config(), allowed_features=("queue_depth",))
    p = propose_repair(parent(), train, dev, config=cfg, frozen_at=400)
    assert p["status"] == "candidate"
    assert p["child"]["rule"]["all"][-1] == {"feature": "queue_depth", "op": "le", "value": 1.0}


def test_rule_depth_cap_stops_repair_instead_of_expanding_indefinitely():
    p = propose_repair(parent(), cohort("train", 100), cohort("dev", 200),
                       config=replace(config(), max_rule_depth=2), frozen_at=400)
    assert p["status"] == "no_update"
    assert p["diagnosis"]["reasons"] == ["depth_budget_exhausted"]


def test_a_rejected_future_candidate_cannot_activate(tmp_path):
    p = proposal()
    store = RepairTrialStore(tmp_path / "trial.sqlite3")
    score_repair(p, cohort("sealed", 500, 96), phase="sealed", store=store, evaluated_at=600)
    bad = score_repair(p, cohort("future", 700, 96, reversed_labels=True), phase="future", store=store, evaluated_at=800)
    assert not bad["passed"]
    with pytest.raises(ValueError, match="future repair credit"):
        activate_repair(RoutineRegistry([parent()]), p, store=store, approved=True)


def test_label_features_are_not_allowlisted():
    for field in ("target", "outcome", "verified", "evidence_level", "verifier_id"):
        with pytest.raises(ValueError):
            RepairConfig(allowed_features=(field,))


def test_explicit_parent_floor_cannot_be_lowered_by_repair_config():
    par = replace(parent(), precision_floor=.99)
    p = propose_repair(par, cohort("train", 100), cohort("dev", 200), config=config(), frozen_at=400)
    assert p["child"]["precision_floor"] == .99


def test_property_narrowed_region_is_always_a_parent_subset():
    import random
    child = RoutineCandidate.from_dict(proposal()["child"])
    rng = random.Random(44)
    for _ in range(512):
        features = {"prev_family": rng.choice(["EDIT", "READ", None]),
                    "tool_ok": rng.choice([True, False, 1, 0, "yes", None]),
                    "phase": rng.choice(["batch", "interactive", "novel", None])}
        assert not child.rule.matches(features) or parent().rule.matches(features)


def test_drift_accounting_compares_typed_outputs_too():
    from z0int.routines import evaluate_rule
    met = evaluate_rule([
        {"split": "future", "session_id": "a", "features": {"x": 1}, "target": 1},
        {"split": "future", "session_id": "b", "features": {"x": 1}, "target": True},
    ], rule=RoutineRule((LiteralPredicate("x", "eq", 1),)), output=True, split="future")
    assert met.correct == 1 and met.precision == .5


def test_child_does_not_inherit_parents_closed_loop_evidence():
    par = replace(parent(), evidence_level="L3_closed_loop")
    p = propose_repair(par, cohort("train", 100), cohort("dev", 200), config=config(), frozen_at=400)
    assert p["parent"]["evidence_level"] == "L3_closed_loop"
    assert p["child"]["evidence_level"] == "L2_outcome"
