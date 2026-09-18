from __future__ import annotations

import tempfile
import unittest
from pathlib import Path

from z0int.abab import (
    AbabConfig,
    CreditGates,
    ExperimentArchive,
    ExperimentMetrics,
    ExperimentProposal,
    ExperimentRecord,
    HypothesisBelief,
    calibrate_credit_gate,
    choose_discriminating_test,
    choose_next,
    expected_information_gain,
    transfer_credit_ok,
    gate_status,
    should_run_c,
)


def prop(i: str, niche: str, *, learned: bool = True, eig: float = 0.8, cost: float = 1.0):
    return ExperimentProposal(
        id=i,
        hypothesis_id=f"h-{i}",
        niche=niche,
        summary=i,
        expected_information_gain=eig,
        impact=0.9,
        decision_change=0.8,
        transferability=0.9,
        estimated_cost=cost,
        learned=learned,
    )


def credited(p: ExperimentProposal, m: ExperimentMetrics, *, cost: float = 1.0):
    g = CreditGates(validity=True, activation=True, credit=True)
    return ExperimentRecord(p, "credited", gates=g, metrics=m, actual_cost=cost)


class AbabMetaLoopTests(unittest.TestCase):
    def test_priority_prefers_information_and_impact_per_cost(self) -> None:
        cheap = prop("cheap", "routing", eig=0.8, cost=1.0)
        expensive = prop("expensive", "routing", eig=0.9, cost=10.0)
        self.assertEqual(choose_next([expensive, cheap]).id, "cheap")

    def test_niche_archive_preserves_pareto_tradeoffs_and_learned_front(self) -> None:
        rule = credited(
            prop("rule", "delegate", learned=False),
            ExperimentMetrics(verified_success=0.99, safe_coverage=0.30, premium_tokens_per_success=0.0),
        )
        mb = credited(
            prop("mb", "delegate", learned=True),
            ExperimentMetrics(verified_success=0.98, safe_coverage=0.80, premium_tokens_per_success=0.0),
        )
        slm = credited(
            prop("slm", "delegate", learned=True),
            ExperimentMetrics(verified_success=0.995, safe_coverage=0.85, premium_tokens_per_success=100.0),
        )
        archive = ExperimentArchive([rule, mb, slm])
        all_ids = {r.proposal.id for r in archive.niche_front("delegate")}
        learned_ids = {r.proposal.id for r in archive.niche_front("delegate", learned_only=True)}
        self.assertIn("rule", all_ids)
        self.assertNotIn("rule", learned_ids)
        self.assertEqual(learned_ids, {"mb", "slm"})

    def test_negative_experiment_is_retained_and_can_kill_hypothesis(self) -> None:
        p = prop("bad", "verify")
        g = CreditGates(validity=True, activation=True, credit=False)
        rec = ExperimentRecord(
            p,
            gate_status(g, killed_hypothesis=True),
            gates=g,
            failure_class="missing_temporal_state",
            killed_hypothesis=True,
        )
        archive = ExperimentArchive([rec])
        self.assertEqual(archive.records[0].status, "disconfirmed")
        self.assertEqual(archive.records[0].failure_class, "missing_temporal_state")
        self.assertEqual(archive.niche_front("verify"), [])

    def test_stop_on_repeated_no_update_and_low_information_gain(self) -> None:
        rows = []
        for i in range(3):
            p = prop(f"n{i}", "routing")
            rows.append(ExperimentRecord(p, "no_update", actual_cost=1.0))
        archive = ExperimentArchive(rows)
        self.assertEqual(archive.stop_reason(), "repeated_no_update")

        fresh = ExperimentArchive()
        low = prop("low", "routing", eig=0.001, cost=10.0)
        self.assertEqual(fresh.stop_reason([low], config=AbabConfig(min_priority=0.01)), "collapsed_information_gain")

    def test_c_stage_computes_information_gain_from_rival_predictions(self) -> None:
        hs = [
            HypothesisBelief("representation", 0.5, {"add-features": 0.95, "more-kc": 0.55}),
            HypothesisBelief("capacity", 0.5, {"add-features": 0.05, "more-kc": 0.55}),
        ]
        self.assertGreater(expected_information_gain("add-features", hs), 0.7)
        self.assertLess(expected_information_gain("more-kc", hs), 0.01)

        tests = [
            ExperimentProposal("add-features", "h", "verify", "feature ablation", 0.0, 1.0, 1.0, 1.0, 2.0, kind="C"),
            ExperimentProposal("more-kc", "h", "verify", "capacity sweep", 1.0, 1.0, 1.0, 1.0, 1.0, kind="C"),
        ]
        # Despite the second proposal claiming a higher manual EIG, C chooses
        # the test that actually separates the hypotheses.
        self.assertEqual(choose_discriminating_test(tests, hs).id, "add-features")

    def test_c_stage_only_when_discriminating_experiment_is_better(self) -> None:
        self.assertTrue(should_run_c(experiment_value=0.8, further_research_value=0.3, competing_hypotheses=2))
        self.assertFalse(should_run_c(experiment_value=0.2, further_research_value=0.3, competing_hypotheses=2))
        self.assertFalse(should_run_c(experiment_value=0.9, further_research_value=0.1, competing_hypotheses=1))

    def test_phase0_gate_calibration_must_separate_known_controls(self) -> None:
        good = credited(prop("known-good", "cal"), ExperimentMetrics(verified_success=1.0))
        bad_gates = CreditGates(validity=True, activation=True, credit=False)
        bad = ExperimentRecord(prop("known-bad", "cal"), "rejected", gates=bad_gates)
        cal = calibrate_credit_gate([(good, True), (bad, False)], min_good_accept=1.0, min_bad_reject=1.0)
        self.assertTrue(cal.passed)

        broken = calibrate_credit_gate([(bad, True), (good, False)], min_good_accept=1.0, min_bad_reject=1.0)
        self.assertFalse(broken.passed)

    def test_transfer_gate_preserves_cross_family_lift(self) -> None:
        weak = credited(prop("weak-transfer", "x"), ExperimentMetrics(transfer_retention=0.4))
        strong = credited(prop("strong-transfer", "x"), ExperimentMetrics(transfer_retention=0.9))
        self.assertFalse(transfer_credit_ok(weak, floor=0.8))
        self.assertTrue(transfer_credit_ok(strong, floor=0.8))

    def test_archive_round_trip(self) -> None:
        rec = credited(prop("x", "recovery"), ExperimentMetrics(verified_success=1.0), cost=2.0)
        with tempfile.TemporaryDirectory() as td:
            p = Path(td) / "archive.jsonl"
            ExperimentArchive([rec]).to_jsonl(p)
            got = ExperimentArchive.from_jsonl(p)
            self.assertEqual(got.records[0].to_dict(), rec.to_dict())


if __name__ == "__main__":
    unittest.main()
