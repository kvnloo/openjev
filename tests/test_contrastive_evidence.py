from __future__ import annotations

import tempfile
import unittest
from pathlib import Path

from z0int.contrastive_evidence import (
    ContrastFamily,
    EvidenceItem,
    evaluate_family,
    evaluate_recipe_on_family,
    example_project_status_family,
    store_dependency,
)


class ContrastiveFourConditionTests(unittest.TestCase):
    def test_fixture_full_pass(self) -> None:
        fam = example_project_status_family()
        out = evaluate_family(fam)
        self.assertTrue(out["pair_pass"])
        self.assertTrue(out["robust_pass"])
        self.assertTrue(out["full_pass"])
        self.assertIsNone(out["conditions"]["necessity_delete"]["expected"])
        self.assertIsNone(out["conditions"]["necessity_delete"]["predicted"])

    def test_relevant_edit_flips_answer(self) -> None:
        fam = example_project_status_family()
        out = evaluate_family(fam)
        self.assertEqual(out["conditions"]["original"]["predicted"], "rev-3")
        self.assertEqual(out["conditions"]["relevant_edit"]["predicted"], "rev-4")

    def test_irrelevant_control_preserves(self) -> None:
        fam = example_project_status_family()
        out = evaluate_family(fam)
        self.assertTrue(out["conditions"]["irrelevant_control"]["ok"])

    def test_dropping_necessary_evidence_fails_recipe(self) -> None:
        fam = example_project_status_family()
        # drop rfc_rev — cheaper but brittle
        bad = evaluate_recipe_on_family(fam, {"drop_evidence_ids": ["rfc_rev"]})
        self.assertFalse(bad["full_pass"])
        self.assertFalse(bad["pair_pass"])

    def test_cheaper_drop_stale_still_sensitive(self) -> None:
        fam = example_project_status_family()
        good = evaluate_recipe_on_family(
            fam, {"drop_evidence_ids": ["old_summary", "unrelated_chat"]}
        )
        self.assertTrue(good["full_pass"])
        self.assertLess(len(good["recipe_keep_ids"]), 4)

    def test_store_dependency_not_verified_success(self) -> None:
        import os
        from contextlib import contextmanager

        @contextmanager
        def z0home(tmp: str):
            old = os.environ.get("Z0INT_HOME")
            os.environ["Z0INT_HOME"] = tmp
            try:
                yield
            finally:
                if old is None:
                    os.environ.pop("Z0INT_HOME", None)
                else:
                    os.environ["Z0INT_HOME"] = old

        fam = example_project_status_family()
        out = evaluate_family(fam)
        with tempfile.TemporaryDirectory() as tmp, z0home(tmp):
            path = store_dependency(out["dependency"])
            self.assertTrue(path.is_file())
            text = path.read_text(encoding="utf-8")
            self.assertIn("curation_accepted", text)
            self.assertNotIn('"verified_success": true', text)


class ContrastiveAutoresearchJobTests(unittest.TestCase):
    def test_run_contrastive_job_keeps_cheaper_sensitive(self) -> None:
        import os
        from contextlib import contextmanager

        @contextmanager
        def z0home(tmp: str):
            old = os.environ.get("Z0INT_HOME")
            os.environ["Z0INT_HOME"] = tmp
            try:
                yield
            finally:
                if old is None:
                    os.environ.pop("Z0INT_HOME", None)
                else:
                    os.environ["Z0INT_HOME"] = old

        from z0int.autoresearch.daemon import run_once, resume
        from z0int.autoresearch.queue import enqueue_trace

        with tempfile.TemporaryDirectory() as tmp, z0home(tmp):
            resume()
            r = enqueue_trace(
                "contrast-1",
                verified_success=True,
                verifier_id="unit_contrastive",
                payload={"kind": "contrastive_evidence"},
            )
            self.assertTrue(r["enqueued"])
            out = run_once(force=True)
            self.assertTrue(out.get("ok"))
            self.assertEqual(out.get("kind"), "contrastive_evidence")
            self.assertEqual(out.get("decision"), "KEEP_CHEAPER_SENSITIVE_RECIPE")
            self.assertIs(out.get("production_credit_eligible"), False)
            # results row must not claim verified_success
            results = (Path(tmp) / "autoresearch" / "results.jsonl").read_text(encoding="utf-8")
            self.assertIn('"verified_success": null', results)


if __name__ == "__main__":
    unittest.main()
