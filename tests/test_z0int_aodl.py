from __future__ import annotations

import re
import unittest
from dataclasses import replace

from z0int.aodl import (
    AODL_NODE_KINDS,
    AodlBindingConfig,
    AodlBudgets,
    AodlSpend,
    append_event,
    assert_basic_aodl_invariants,
    binding_for_implementation_stage,
    check_budget,
    compile_aodl,
    outcome_event,
    route_event,
    runtime_contract,
)
from z0int.cascade import CascadePolicy, StagePolicy
from z0int.routines import LiteralPredicate, RoutineCandidate, RoutineRule


class AodlBindingTests(unittest.TestCase):
    def cascade(self) -> CascadePolicy:
        return CascadePolicy(
            capability_id="coding.needs_verification",
            stages=(
                StagePolicy("mb", 0.95),
                StagePolicy("local_slm", 0.90),
                StagePolicy("frontier", 0.0, terminal=True),
            ),
            final_stage="frontier",
            min_success_rate=0.99,
            max_success_regression=0.01,
            objective="premium_tokens_per_verified_success",
            status="promoted",
        )

    def routine(self) -> RoutineCandidate:
        return RoutineCandidate(
            capability_id="coding.needs_verification",
            rule=RoutineRule((LiteralPredicate("prev_family", "eq", "EDIT"), LiteralPredicate("tool_ok", "eq", True))),
            output="VERIFY",
            source_specialist="mb-gen2",
            source_generation=2,
            evidence_level="L2_outcome",
            precision_floor=0.98,
            min_sessions=5,
            status="promoted",
        )

    def test_uses_only_existing_aodl_kinds_and_routes_by_policy(self) -> None:
        doc = compile_aodl(
            capability_id="coding.needs_verification",
            cascade=self.cascade(),
            routines=[self.routine()],
            config=AodlBindingConfig(
                harness_id="omp",
                budgets=AodlBudgets(tokens=4000, premium_tokens=1000, latency_ms=1500, joules=25.0),
            ),
        )
        assert_basic_aodl_invariants(doc)
        kinds = {n["kind"] for n in doc["intentGraph"]["nodes"]}
        self.assertTrue(kinds <= AODL_NODE_KINDS)
        self.assertNotIn("fly", kinds)
        self.assertNotIn("routine", kinds)
        self.assertNotIn("cascade", kinds)
        self.assertIn("service", kinds)
        self.assertIn("model", kinds)
        order = doc["policies"]["route"]["order"]
        self.assertEqual(order[0], "routine-service")
        self.assertEqual(order[-1], "stage-frontier")
        self.assertEqual(doc["constraints"]["budgets"]["premium_tokens"], 1000)
        self.assertEqual(doc["constraints"]["acceptance"]["capabilityId"], "coding.needs_verification")

    def test_private_implementation_binding_lives_in_plan_not_node_ontology(self) -> None:
        doc = compile_aodl(
            capability_id="coding.needs_verification",
            cascade=self.cascade(),
            routines=[self.routine()],
            config=AodlBindingConfig(
                stage_bindings={
                    "mb": {"binding": "artifact", "uri": "z0int://specialists/needs-verification/mb"},
                    "local_slm": {"binding": "resource", "selector": {"class": "local_semantic"}},
                    "frontier": {"binding": "resource", "selector": {"class": "frontier"}},
                },
                stage_roles={"mb": "specialist", "local_slm": "local-semantic", "frontier": "frontier"},
            ),
        )
        plan = doc["plan"]
        self.assertEqual(plan["bindings"]["stage-specialist"]["uri"], "z0int://specialists/needs-verification/mb")
        self.assertEqual(plan["bindings"]["stage-specialist"]["implementationStage"], "mb")
        self.assertEqual(plan["route"]["thresholds"]["stage-specialist"], 0.95)
        self.assertNotIn("confidenceThresholds", doc["policies"]["route"])
        self.assertEqual(plan["bindings"]["routine-registry"]["artifactType"], "z0int.routine_registry.v1")
        # Intent nodes remain implementation-agnostic: no paths/model ids/private rules.
        rendered_nodes = str(doc["intentGraph"]["nodes"])
        self.assertNotIn("prev_family", rendered_nodes)
        self.assertNotIn("Qwen", rendered_nodes)

    def test_source_hash_is_deterministic_sha256(self) -> None:
        a = compile_aodl(capability_id="coding.needs_verification", cascade=self.cascade(), routines=[self.routine()])
        b = compile_aodl(capability_id="coding.needs_verification", cascade=self.cascade(), routines=[self.routine()])
        self.assertEqual(a["provenance"]["sourceHash"], b["provenance"]["sourceHash"])
        self.assertRegex(a["provenance"]["sourceHash"], re.compile(r"^[a-f0-9]{64}$"))

    def test_observed_route_and_outcome_are_aodl_events(self) -> None:
        doc = compile_aodl(capability_id="coding.needs_verification", cascade=self.cascade(), routines=[self.routine()])
        sh = doc["provenance"]["sourceHash"]
        routed = route_event(
            trace_id="trace-1",
            source_hash_hex=sh,
            revision=doc["revision"],
            stage="routine-service",
            capability_id="coding.needs_verification",
            confidence=1.0,
            routine_id=self.routine().routine_id,
        )
        outcome = outcome_event(
            trace_id="trace-1",
            source_hash_hex=sh,
            revision=doc["revision"],
            success=True,
            verifier="pytest",
            premium_tokens=0,
            total_tokens=0,
            latency_ms=0.4,
            causal_parents=[routed["eventId"]],
        )
        observed = append_event(append_event(doc, routed), outcome)
        self.assertEqual([e["type"] for e in observed["eventLog"]], ["route", "stateUpdate"])
        self.assertEqual(observed["eventLog"][1]["causalParents"], [routed["eventId"]])
        self.assertTrue(observed["eventLog"][1]["payload"]["success"])

    def test_no_routine_keeps_stable_slot_but_binding_is_disabled(self) -> None:
        doc = compile_aodl(capability_id="coding.needs_verification", cascade=self.cascade(), routines=[])
        self.assertEqual(doc["policies"]["route"]["order"][0], "routine-service")
        self.assertIn("routine-service", {n["id"] for n in doc["intentGraph"]["nodes"]})
        self.assertFalse(doc["plan"]["bindings"]["routine-service"]["enabled"])

    def test_runtime_consumes_the_same_aodl_contract(self) -> None:
        doc = compile_aodl(
            capability_id="coding.needs_verification",
            cascade=self.cascade(),
            routines=[self.routine()],
            config=AodlBindingConfig(
                harness_id="codex",
                budgets=AodlBudgets(tokens=3000, premium_tokens=700),
                stage_roles={"mb": "specialist", "local_slm": "local-semantic", "frontier": "frontier"},
            ),
        )
        contract = runtime_contract(doc)
        self.assertEqual(contract.capability_id, "coding.needs_verification")
        self.assertEqual(contract.harness_id, "codex")
        self.assertEqual(contract.route_order[0], "routine-service")
        self.assertEqual(contract.route_order[-1], "stage-frontier")
        self.assertEqual(contract.budgets["premium_tokens"], 700)
        self.assertEqual(contract.bindings["stage-specialist"]["implementationStage"], "mb")

    def test_aodl_gamma_budget_is_runtime_enforced(self) -> None:
        doc = compile_aodl(
            capability_id="coding.needs_verification",
            cascade=self.cascade(),
            routines=[self.routine()],
            config=AodlBindingConfig(
                budgets=AodlBudgets(tokens=500, premium_tokens=100, latency_ms=1000),
                stage_roles={"mb": "specialist", "local_slm": "local-semantic", "frontier": "frontier"},
            ),
        )
        contract = runtime_contract(doc)
        ok = check_budget(
            contract,
            observed=AodlSpend(tokens=100, premium_tokens=0, latency_ms=100),
            proposed=AodlSpend(tokens=120, premium_tokens=0, latency_ms=20),
        )
        self.assertTrue(ok.allowed)
        blocked = check_budget(
            contract,
            observed=AodlSpend(tokens=450, premium_tokens=90, latency_ms=900),
            proposed=AodlSpend(tokens=100, premium_tokens=20, latency_ms=150),
        )
        self.assertFalse(blocked.allowed)
        self.assertEqual(set(blocked.exceeded), {"tokens", "premium_tokens", "latency_ms"})
        slot = binding_for_implementation_stage(contract, "mb")
        self.assertIsNotNone(slot)
        assert slot is not None
        self.assertEqual(slot[0], "stage-specialist")


if __name__ == "__main__":
    unittest.main()
