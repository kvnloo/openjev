from __future__ import annotations

import json
import tempfile
import unittest
from pathlib import Path

from z0int.routines import (
    CompileConfig,
    LiteralPredicate,
    RoutineRegistry,
    RoutineRule,
    compile_and_credit,
    credit_sealed,
    mine_routines,
)


def row(split: str, session: str, *, prev: str, tool_ok: bool, target: str, capability: str = "needs_verification"):
    return {
        "capability_id": capability,
        "split": split,
        "session_id": session,
        "features": {"prev_family": prev, "tool_ok": tool_ok},
        "target": target,
        "evidence_level": "L2_outcome",
    }


def synthetic_rows() -> list[dict]:
    rows: list[dict] = []
    # Rule we want: prev_family=EDIT AND tool_ok=True -> VERIFY.
    # Outside that region, EXECUTE is common enough that the rule has real lift.
    for split, n_sessions in (("train", 8), ("dev", 4), ("sealed", 4)):
        for s in range(n_sessions):
            sid = f"{split}-{s}"
            for _ in range(8):
                rows.append(row(split, sid, prev="EDIT", tool_ok=True, target="VERIFY"))
            # Single literal prev=EDIT is imperfect; conjunction is needed.
            for _ in range(3):
                rows.append(row(split, sid, prev="EDIT", tool_ok=False, target="EXECUTE"))
            for _ in range(8):
                rows.append(row(split, sid, prev="EXECUTE", tool_ok=True, target="EXECUTE"))
    return rows


class RoutineCompilerTests(unittest.TestCase):
    def setUp(self) -> None:
        self.cfg = CompileConfig(
            precision_floor=0.95,
            min_train_matches=20,
            min_dev_matches=10,
            min_sealed_matches=10,
            min_sessions=3,
            min_lift_vs_global_prior=0.05,
            max_rule_depth=2,
        )

    def test_rule_ast_is_safe_and_exact(self) -> None:
        rule = RoutineRule(
            (
                LiteralPredicate("prev_family", "eq", "EDIT"),
                LiteralPredicate("tool_ok", "eq", True),
            )
        )
        self.assertTrue(rule.matches({"prev_family": "EDIT", "tool_ok": True}))
        self.assertFalse(rule.matches({"prev_family": "EDIT", "tool_ok": False}))
        self.assertFalse(rule.matches({"prev_family": "EDIT"}))
        with self.assertRaises(ValueError):
            LiteralPredicate("x", "python", "__import__('os')")

    def test_mining_uses_train_dev_and_never_sealed_for_discovery(self) -> None:
        rows = synthetic_rows()
        # Poison sealed with an opposite label; discovery must still produce the
        # same predicates before crediting, then sealed should reject them.
        discovered = mine_routines(
            rows,
            capability_id="needs_verification",
            source_specialist="mb-gen1",
            evidence_level="L2_outcome",
            config=self.cfg,
        )
        self.assertTrue(discovered)
        self.assertTrue(all(c.sealed is None for c in discovered))
        self.assertTrue(all(c.provenance["sealed_visible_during_discovery"] is False for c in discovered))

        poisoned = [dict(r) for r in rows]
        for r in poisoned:
            if r["split"] == "sealed" and r["features"] == {"prev_family": "EDIT", "tool_ok": True}:
                r["target"] = "EXECUTE"
        credited = credit_sealed(poisoned, discovered, config=self.cfg)
        self.assertTrue(any(c.status == "candidate" for c in credited))
        self.assertFalse(any(c.status == "credited" for c in credited if c.output == "VERIFY"))

    def test_compile_promotes_high_precision_conjunction(self) -> None:
        routines = compile_and_credit(
            synthetic_rows(),
            capability_id="needs_verification",
            source_specialist="mb-gen1",
            source_generation=1,
            evidence_level="L2_outcome",
            config=self.cfg,
        )
        promoted = [r for r in routines if r.status == "promoted" and r.output == "VERIFY"]
        self.assertTrue(promoted)
        best = promoted[0]
        self.assertGreaterEqual(best.sealed.precision or 0, 0.95)
        self.assertGreaterEqual(best.sealed.matched_sessions, 3)
        self.assertGreater((best.sealed.lift_vs_global_prior or 0), 0.05)

    def test_registry_fail_open_and_json_round_trip(self) -> None:
        routines = compile_and_credit(
            synthetic_rows(),
            capability_id="needs_verification",
            source_specialist="mb-gen1",
            source_generation=1,
            evidence_level="L2_outcome",
            config=self.cfg,
        )
        registry = RoutineRegistry(routines)
        hit = registry.decide("needs_verification", {"prev_family": "EDIT", "tool_ok": True})
        self.assertTrue(hit.matched)
        self.assertEqual(hit.output, "VERIFY")

        miss = registry.decide("needs_verification", {"prev_family": "UNKNOWN", "tool_ok": False})
        self.assertFalse(miss.matched)
        self.assertEqual(miss.reason, "fallback_to_specialist")

        with tempfile.TemporaryDirectory() as td:
            p = Path(td) / "registry.jsonl"
            registry.to_jsonl(p)
            loaded = RoutineRegistry.from_jsonl(p)
            hit2 = loaded.decide("needs_verification", {"prev_family": "EDIT", "tool_ok": True})
            self.assertEqual(hit.to_dict(), hit2.to_dict())

    def test_future_counterexamples_demote_only_after_enough_sessions(self) -> None:
        routines = compile_and_credit(
            synthetic_rows(),
            capability_id="needs_verification",
            source_specialist="mb-gen1",
            source_generation=1,
            evidence_level="L2_outcome",
            config=self.cfg,
        )
        registry = RoutineRegistry(routines)
        active = [r for r in registry.candidates if r.status == "promoted" and r.output == "VERIFY"]
        self.assertTrue(active)

        # One bad session is insufficient to demote.
        few = [row("future", "f-1", prev="EDIT", tool_ok=True, target="EXECUTE") for _ in range(20)]
        registry.observe_future(few, min_future_matches=10, min_future_sessions=3)
        self.assertTrue(any(r.status == "promoted" and r.output == "VERIFY" for r in registry.candidates))

        # Three independent bad sessions cross the evidence floor and demote.
        many = []
        for s in range(3):
            many.extend(row("future", f"f-{s}", prev="EDIT", tool_ok=True, target="EXECUTE") for _ in range(10))
        registry.observe_future(many, min_future_matches=10, min_future_sessions=3)
        self.assertTrue(any(r.status == "demoted" and r.output == "VERIFY" for r in registry.candidates))


if __name__ == "__main__":
    unittest.main()
