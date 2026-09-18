from __future__ import annotations

import json
import os
import tempfile
import unittest
from pathlib import Path


class ReceiptBuild(unittest.TestCase):
    def test_schema_and_trace(self):
        from z0int.receipt import SCHEMA, build_receipt, validate_receipt

        r = build_receipt(
            capability_id="coding.next_action",
            route="local",
            confidence=0.9,
            baseline_input_tokens=4000,
            baseline_output_tokens=800,
            estimated_frontier_tokens_avoided=4800,
        )
        d = r.to_dict()
        self.assertEqual(d["schema"], SCHEMA)
        self.assertTrue(d["trace_id"])
        self.assertEqual(d["capability_id"], "coding.next_action")
        self.assertEqual(validate_receipt(d), [])
        self.assertEqual(r.tokens_saved_est(), 4800)

    def test_validate_missing_trace(self):
        from z0int.receipt import validate_receipt

        self.assertIn("missing_trace_id", validate_receipt({"schema": "z0int.decision_receipt.v1"}))


class OutcomeTiers(unittest.TestCase):
    def test_execution_completed_is_not_gold(self):
        from z0int.receipt import Outcome

        self.assertEqual(Outcome(execution_completed=True, source="bridge_turn_end").tier(), "execution")

    def test_tool_ok_and_success_not_gold(self):
        from z0int.receipt import Outcome

        self.assertEqual(Outcome(success=True, tool_ok=True, source="legacy").tier(), "soft")
        self.assertFalse(Outcome(success=True, tool_ok=True).is_verified())

    def test_test_pass_and_verified_success_are_gold(self):
        from z0int.receipt import Outcome

        self.assertEqual(Outcome(test_pass=True, source="ci").tier(), "gold")
        self.assertEqual(Outcome(verified_success=True, source="join").tier(), "gold")
        self.assertEqual(Outcome(verified=True, source="join").tier(), "gold")
        self.assertTrue(Outcome(test_pass=True).is_verified())

    def test_negative_beats_gold_signals(self):
        from z0int.receipt import Outcome

        self.assertEqual(
            Outcome(test_pass=True, user_correction=True, source="user").tier(),
            "negative",
        )


class ReceiptJoin(unittest.TestCase):
    def test_emit_join_summary_gold_from_test_pass(self):
        from z0int.receipt import (
            Outcome,
            append_receipt,
            build_receipt,
            find_receipt,
            join_outcome,
            summarize_tokenomics,
        )

        with tempfile.TemporaryDirectory() as tmp:
            home = Path(tmp)
            r = build_receipt(
                capability_id="recovery_action",
                route="local",
                estimated_frontier_tokens_avoided=1200,
                baseline_input_tokens=2000,
                baseline_output_tokens=400,
            )
            row = append_receipt(r, root=home)
            tid = row["trace_id"]
            joined = join_outcome(
                tid,
                Outcome(test_pass=True, source="test"),
                root=home,
            )
            self.assertEqual(joined["outcome_tier"], "gold")
            found = find_receipt(tid, root=home)
            self.assertIsNotNone(found)
            self.assertEqual(found.get("outcome", {}).get("test_pass"), True)
            summary = summarize_tokenomics(root=home)
            self.assertGreaterEqual(summary["rows"], 1)
            self.assertGreaterEqual(summary["frontier_tokens_avoided_est"], 1200)
            self.assertGreaterEqual(summary["rows_with_outcome"], 1)
            self.assertIn("actual_tokens_saved", summary)
            self.assertIn("tokens_per_verified_task", summary)
            self.assertIn("baseline_tokens_sum", summary)
            self.assertGreaterEqual(summary["verified_tasks"], 1)
            self.assertTrue((home / "receipts" / "decisions.jsonl").is_file())
            self.assertTrue((home / "receipts" / "outcomes.jsonl").is_file())

    def test_execution_close_does_not_inflate_verified(self):
        from z0int.receipt import Outcome, append_receipt, build_receipt, close_turn, summarize_tokenomics

        with tempfile.TemporaryDirectory() as tmp:
            home = Path(tmp)
            r = append_receipt(
                build_receipt(
                    capability_id="coding.edit",
                    route="model",
                    baseline_input_tokens=2500,
                    baseline_output_tokens=600,
                ),
                root=home,
            )
            tid = r["trace_id"]
            closed = close_turn(
                tid,
                measured_frontier_tokens=1240,
                input_tokens=900,
                output_tokens=340,
                outcome=Outcome(execution_completed=True, source="bridge_turn_end"),
                root=home,
            )
            self.assertEqual(closed["outcome_join"]["outcome_tier"], "execution")
            s = summarize_tokenomics(root=home)
            self.assertEqual(s["verified_tasks"], 0)
            self.assertGreaterEqual(s["rows_with_outcome"], 1)

    def test_tool_ok_close_not_verified(self):
        from z0int.receipt import Outcome, append_receipt, build_receipt, close_turn, summarize_tokenomics

        with tempfile.TemporaryDirectory() as tmp:
            home = Path(tmp)
            r = append_receipt(
                build_receipt(capability_id="coding.edit", route="model"),
                root=home,
            )
            close_turn(
                r["trace_id"],
                measured_frontier_tokens=100,
                outcome=Outcome(success=True, tool_ok=True, source="legacy"),
                root=home,
            )
            s = summarize_tokenomics(root=home)
            self.assertEqual(s["verified_tasks"], 0)


class ReceiptCli(unittest.TestCase):
    def test_cli_emit_join_summary(self):
        from z0int.cli import main

        with tempfile.TemporaryDirectory() as tmp:
            home = Path(tmp)
            old = os.environ.get("Z0INT_HOME")
            os.environ["Z0INT_HOME"] = str(home)
            try:
                rc = main(
                    [
                        "receipt",
                        "emit",
                        "--capability-id",
                        "needs_verification",
                        "--route",
                        "local",
                        "--avoided",
                        "900",
                        "--prediction",
                        "VERIFY",
                        "--confidence",
                        "0.88",
                    ]
                )
                self.assertEqual(rc, 0)
                lines = (home / "receipts" / "decisions.jsonl").read_text().strip().splitlines()
                self.assertEqual(len(lines), 1)
                tid = json.loads(lines[0])["trace_id"]
                rc = main(
                    [
                        "receipt",
                        "join",
                        tid,
                        "--test-pass",
                        "true",
                        "--verified-success",
                        "true",
                    ]
                )
                self.assertEqual(rc, 0)
                rc = main(["receipt", "summary", "--json"])
                self.assertEqual(rc, 0)
            finally:
                if old is None:
                    os.environ.pop("Z0INT_HOME", None)
                else:
                    os.environ["Z0INT_HOME"] = old


class ReceiptClose(unittest.TestCase):
    def test_close_turn_measured_savings(self):
        from z0int.receipt import Outcome, append_receipt, build_receipt, close_turn, summarize_tokenomics

        with tempfile.TemporaryDirectory() as tmp:
            home = Path(tmp)
            r = append_receipt(
                build_receipt(
                    capability_id="coding.edit",
                    route="model",
                    baseline_input_tokens=2500,
                    baseline_output_tokens=600,
                    estimated_frontier_tokens_avoided=0,
                ),
                root=home,
            )
            tid = r["trace_id"]
            closed = close_turn(
                tid,
                measured_frontier_tokens=1240,
                input_tokens=900,
                output_tokens=340,
                outcome=Outcome(test_pass=True, source="test"),
                root=home,
            )
            self.assertEqual(closed["schema"], "z0int.turn_close.v1")
            self.assertEqual(closed["actual_tokens_saved"], 1860)
            self.assertEqual(closed["outcome_join"]["outcome_tier"], "gold")
            s = summarize_tokenomics(root=home)
            self.assertGreaterEqual(s["rows_with_baseline_and_measured"], 1)
            self.assertGreaterEqual(s["actual_tokens_saved"], 1860)
            self.assertGreaterEqual(s["measured_frontier_tokens_sum"], 1240)
            self.assertGreaterEqual(s["verified_tasks"], 1)


class CounterfactualMine(unittest.TestCase):
    def test_grade_and_mine_smoke(self):
        from z0int.counterfactual import grade_snapshot, mine_omp_sessions, summarize_replay

        self.assertEqual(
            grade_snapshot(
                has_prompt=True,
                has_output=True,
                has_model=True,
                has_usage=True,
                has_env=True,
                has_verifier=False,
            ),
            "B",
        )
        self.assertEqual(
            grade_snapshot(
                has_prompt=True,
                has_output=True,
                has_model=False,
                has_usage=False,
                has_env=False,
                has_verifier=False,
            ),
            "C",
        )
        with tempfile.TemporaryDirectory() as tmp:
            home = Path(tmp)
            sess = home / "sessions" / "bucket"
            sess.mkdir(parents=True)
            # minimal synthetic OMP session with Grok assistant turn
            rows = [
                {
                    "type": "session",
                    "id": "sess-test",
                    "cwd": "/tmp/proj",
                    "timestamp": "2026-09-18T00:00:00Z",
                },
                {
                    "type": "message",
                    "message": {
                        "role": "user",
                        "content": "fix the recovery path",
                        "timestamp": "2026-09-18T00:00:01Z",
                    },
                },
                {
                    "type": "message",
                    "message": {
                        "role": "assistant",
                        "provider": "xai-oauth",
                        "model": "grok-composer-2.5-fast",
                        "content": [{"type": "text", "text": "done"}],
                        "usage": {"input": 100, "output": 20, "totalTokens": 120, "cost": {"total": 0.01}},
                        "timestamp": "2026-09-18T00:00:02Z",
                    },
                },
            ]
            (sess / "s.jsonl").write_text("\n".join(json.dumps(r) for r in rows) + "\n")
            rep = mine_omp_sessions(sessions_root=home / "sessions", root=home, limit=100)
            self.assertTrue(rep["ok"])
            self.assertGreaterEqual(rep["n_written"], 1)
            self.assertGreaterEqual(rep["grades"].get("B", 0), 1)
            s = summarize_replay(root=home)
            self.assertGreaterEqual(s["n_snapshots"], 1)


if __name__ == "__main__":
    unittest.main()
