from __future__ import annotations

import json
import os
import tempfile
import unittest
from pathlib import Path
from unittest import mock


class ReceiptBuild(unittest.TestCase):
    def test_schema_and_trace(self):
        from z0int.receipt import SCHEMA, build_receipt, validate_receipt

        r = build_receipt(
            capability_id="coding.next_action",
            provider="local_mb",
            prediction="EDIT",
            confidence=0.91,
            route="local",
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


class ReceiptJoin(unittest.TestCase):
    def test_emit_join_summary(self):
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
            )
            row = append_receipt(r, root=home)
            tid = row["trace_id"]
            joined = join_outcome(
                tid,
                Outcome(test_pass=True, success=True, source="test"),
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
            self.assertTrue((home / "receipts" / "decisions.jsonl").is_file())
            self.assertTrue((home / "receipts" / "outcomes.jsonl").is_file())


class ReceiptCli(unittest.TestCase):
    def test_cli_emit_join_summary(self):
        from z0int.cli import main

        with tempfile.TemporaryDirectory() as tmp:
            home = Path(tmp)
            env = {**os.environ, "Z0INT_HOME": str(home)}
            with mock.patch.dict(os.environ, env, clear=False):
                # paths.home reads env at call time
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
                rc = main(["receipt", "join", tid, "--test-pass", "true", "--success", "true"])
                self.assertEqual(rc, 0)
                rc = main(["receipt", "summary", "--json"])
                self.assertEqual(rc, 0)


if __name__ == "__main__":
    unittest.main()
