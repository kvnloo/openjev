from __future__ import annotations

import unittest

from z0int.cascade import (
    CascadeCompileConfig,
    credit_sealed,
    compile_cascade,
    decide_row,
    observe_future,
    promote,
)


def trace(split: str, session: str, i: int, *, hard: bool = False, corrupt_local: bool = False):
    target = "A" if i % 2 == 0 else "B"
    # Cheap MB is correct/confident on easy rows; uncertain on hard rows.
    if hard:
        mb_out = "B" if target == "A" else "A"
        mb_conf = 0.55
    else:
        mb_out = target if not corrupt_local else ("B" if target == "A" else "A")
        mb_conf = 0.97
    # Local semantic scorer recovers most hard rows at medium token cost.
    local_out = target
    local_conf = 0.92 if hard else 0.99
    # Frontier is the trusted baseline and spends premium quota.
    frontier_out = target
    return {
        "capability_id": "coding.route",
        "split": split,
        "session_id": session,
        "target": target,
        "predictions": {
            "routine": {"available": False},
            "mb": {"output": mb_out, "confidence": mb_conf, "premium_tokens": 0, "total_tokens": 0, "latency_ms": 0.2},
            "local_slm": {"output": local_out, "confidence": local_conf, "premium_tokens": 0, "total_tokens": 120, "latency_ms": 20.0},
            "frontier": {"output": frontier_out, "confidence": 1.0, "premium_tokens": 2000, "total_tokens": 2000, "latency_ms": 500.0},
        },
    }


def dataset() -> list[dict]:
    rows = []
    for split, sessions in (("dev", 4), ("sealed", 4)):
        for s in range(sessions):
            for i in range(25):
                rows.append(trace(split, f"{split}-{s}", i, hard=(i % 5 == 0)))
    return rows


class CascadeCompilerTests(unittest.TestCase):
    def setUp(self) -> None:
        self.cfg = CascadeCompileConfig(
            max_success_regression=0.0,
            min_rows=20,
            min_sessions=3,
            threshold_grid=(0.0, 0.6, 0.8, 0.9, 0.95, 0.99),
        )

    def test_compile_uses_dev_only_and_saves_frontier_tokens(self) -> None:
        rows = dataset()
        policy = compile_cascade(
            rows,
            capability_id="coding.route",
            stage_order=("mb", "local_slm", "frontier"),
            final_stage="frontier",
            config=self.cfg,
        )
        self.assertIsNotNone(policy)
        assert policy is not None
        self.assertTrue(policy.provenance["sealed_visible_during_search"] is False)
        self.assertEqual(policy.dev.success_rate, 1.0)
        self.assertEqual(policy.dev.premium_tokens, 0)
        self.assertEqual(policy.dev.baseline_premium_tokens, 200000)
        self.assertEqual(policy.dev.premium_token_reduction, 1.0)

    def test_sealed_credit_and_promotion(self) -> None:
        rows = dataset()
        policy = compile_cascade(
            rows,
            capability_id="coding.route",
            stage_order=("mb", "local_slm", "frontier"),
            final_stage="frontier",
            config=self.cfg,
        )
        assert policy is not None
        credited = credit_sealed(rows, policy, config=self.cfg)
        self.assertEqual(credited.status, "credited")
        promoted = promote(credited)
        self.assertEqual(promoted.status, "promoted")
        self.assertEqual(promoted.sealed.success_rate, 1.0)
        self.assertLess(promoted.sealed.premium_tokens, promoted.sealed.baseline_premium_tokens)

    def test_runtime_escalates_on_low_confidence(self) -> None:
        rows = dataset()
        policy = compile_cascade(
            rows,
            capability_id="coding.route",
            stage_order=("mb", "local_slm", "frontier"),
            final_stage="frontier",
            config=self.cfg,
        )
        assert policy is not None
        easy = trace("future", "f", 2, hard=False)
        hard = trace("future", "f", 5, hard=True)
        d1 = decide_row(easy, policy)
        d2 = decide_row(hard, policy)
        self.assertIn(d1.stage, {"mb", "local_slm"})
        self.assertEqual(d2.stage, "local_slm")
        self.assertEqual(d1.premium_tokens, 0)
        self.assertEqual(d2.premium_tokens, 0)

    def test_future_drift_demotes_after_enough_independent_evidence(self) -> None:
        rows = dataset()
        policy = compile_cascade(
            rows,
            capability_id="coding.route",
            stage_order=("mb", "local_slm", "frontier"),
            final_stage="frontier",
            config=self.cfg,
        )
        assert policy is not None
        policy = promote(credit_sealed(rows, policy, config=self.cfg))
        self.assertEqual(policy.status, "promoted")

        # Corrupt both local stages on future traffic so the frozen cascade fails,
        # while the frontier baseline remains correct.
        future = []
        for s in range(3):
            for i in range(20):
                r = trace("future", f"future-{s}", i, hard=False, corrupt_local=True)
                r["predictions"]["local_slm"]["output"] = "B" if r["target"] == "A" else "A"
                future.append(r)
        observed = observe_future(future, policy, min_rows=20, min_sessions=3)
        self.assertEqual(observed.status, "demoted")
        self.assertLess(observed.future.success_rate, observed.future.baseline_success_rate)


if __name__ == "__main__":
    unittest.main()
