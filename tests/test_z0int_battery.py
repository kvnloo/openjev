from __future__ import annotations

import unittest

from z0int.battery import CascadeSealedGate, RoutineSealedGate
from z0int.cascade import CascadeCompileConfig, compile_cascade
from z0int.routines import CompileConfig, mine_routines


class BlindBatteryTests(unittest.TestCase):
    def test_routine_search_can_run_without_sealed_rows(self) -> None:
        rows = []
        for split in ("train", "dev", "sealed"):
            for s in range(3):
                for i in range(12):
                    rows.append({
                        "capability_id": "cap",
                        "split": split,
                        "session_id": f"{split}-{s}",
                        "features": {"x": "A" if i < 10 else "B"},
                        "target": "YES" if i < 10 else "NO",
                    })
        open_rows = [r for r in rows if r["split"] != "sealed"]
        candidates = mine_routines(
            open_rows,
            capability_id="cap",
            source_specialist="mb",
            evidence_level="L2_outcome",
            config=CompileConfig(min_train_matches=10, min_dev_matches=10, min_sealed_matches=10, min_sessions=3),
        )
        self.assertTrue(candidates)
        gate = RoutineSealedGate(rows)
        credited = gate.credit(candidates, config=CompileConfig(min_train_matches=10, min_dev_matches=10, min_sealed_matches=10, min_sessions=3))
        self.assertTrue(any(c.status == "credited" for c in credited))
        self.assertFalse(hasattr(gate, "rows"))
        self.assertEqual(gate.manifest.n_sessions, 3)

    def test_cascade_search_and_sealed_gate_are_separate(self) -> None:
        rows = []
        for split in ("dev", "sealed"):
            for s in range(3):
                for i in range(10):
                    target = "A" if i % 2 == 0 else "B"
                    rows.append({
                        "capability_id": "cap",
                        "split": split,
                        "session_id": f"{split}-{s}",
                        "target": target,
                        "predictions": {
                            "mb": {"output": target, "confidence": 0.99, "premium_tokens": 0, "total_tokens": 0},
                            "frontier": {"output": target, "confidence": 1.0, "premium_tokens": 1000, "total_tokens": 1000},
                        },
                    })
        open_rows = [r for r in rows if r["split"] == "dev"]
        cfg = CascadeCompileConfig(min_rows=20, min_sessions=3)
        policy = compile_cascade(open_rows, capability_id="cap", stage_order=("mb", "frontier"), final_stage="frontier", config=cfg)
        self.assertIsNotNone(policy)
        credited = CascadeSealedGate(rows).credit(policy, config=cfg)
        self.assertEqual(credited.status, "credited")


if __name__ == "__main__":
    unittest.main()
