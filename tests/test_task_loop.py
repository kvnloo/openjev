from __future__ import annotations

import os
import tempfile
import unittest
from contextlib import contextmanager
from pathlib import Path

from z0int.aodl import assert_basic_aodl_invariants
from z0int.context_resolve import InformationNeed, attach_context_to_aodl, resolve_context
from z0int.task_loop import (
    PatchSpec,
    authorize_task,
    compile_family_aodl,
    load_checkpoint,
    make_fixture_repo,
    resume_task,
    run_until,
    step_verify,
)


@contextmanager
def z0home(tmp: str):
    prev = os.environ.get("Z0INT_HOME")
    os.environ["Z0INT_HOME"] = tmp
    try:
        yield
    finally:
        if prev is None:
            os.environ.pop("Z0INT_HOME", None)
        else:
            os.environ["Z0INT_HOME"] = prev


class AodlContextAttachTests(unittest.TestCase):
    def test_attach_preserves_source_hash_and_invariants(self) -> None:
        doc = compile_family_aodl()
        sh = doc["provenance"]["sourceHash"]
        with tempfile.TemporaryDirectory() as tmp:
            f = Path(tmp) / "r.md"
            f.write_text("need X\n", encoding="utf-8")
            packet = resolve_context(
                needs=[InformationNeed(id="r", description="r", kind="exact_path", path="r.md")],
                project_root=tmp,
                allow_qmd=False,
                use_cache=False,
                task_id="t-attach",
            )
        out = attach_context_to_aodl(doc, packet, trace_id="t-attach")
        assert_basic_aodl_invariants(out)
        self.assertEqual(out["provenance"]["sourceHash"], sh)
        self.assertIn("runtimeContext", out["provenance"])
        self.assertIn("context", out["constraints"])
        self.assertTrue(any(e.get("type") == "stateUpdate" for e in out["eventLog"]))
        self.assertNotIn("intentContract", out)
        ev = out["eventLog"][-1]
        self.assertIsNone(ev["payload"].get("verified_success"))


class VerifiedLoopTests(unittest.TestCase):
    def test_fixture_end_to_end_verified(self) -> None:
        with tempfile.TemporaryDirectory() as tmp, z0home(str(Path(tmp) / "z0home")):
            repo = Path(tmp) / "repo"
            root, patch = make_fixture_repo(repo)
            cp = authorize_task(
                base_repo=root,
                patch=patch,
                requirement_paths=["REQUIREMENT.md"],
            )
            self.assertEqual(cp.status, "authorized")
            self.assertIsNone(cp.verified_success)
            cp = run_until(cp, until="patched", worktrees_root=Path(tmp) / "wts")
            self.assertEqual(cp.status, "patched")
            self.assertTrue(cp.execution_completed)
            self.assertIsNone(cp.verified_success)
            self.assertIn("BROKEN", (root / "app.py").read_text())
            self.assertIn("READY", (Path(cp.worktree_path) / "app.py").read_text())
            cp = step_verify(cp)
            self.assertTrue(cp.verified_success)
            self.assertEqual(cp.status, "verified")
            cp2 = resume_task(cp.task_id, until="verified", worktrees_root=Path(tmp) / "wts")
            self.assertEqual(cp2.status, "verified")
            self.assertTrue(cp2.verified_success)

    def test_execution_completed_is_not_verified_success(self) -> None:
        with tempfile.TemporaryDirectory() as tmp, z0home(str(Path(tmp) / "z0home")):
            root, patch = make_fixture_repo(Path(tmp) / "repo")
            cp = authorize_task(base_repo=root, patch=patch, requirement_paths=["REQUIREMENT.md"])
            cp = run_until(cp, until="worktree_ready", worktrees_root=Path(tmp) / "wts")
            cp.execution_completed = True
            cp.status = "patched"
            cp = step_verify(cp)
            self.assertTrue(cp.execution_completed)
            self.assertFalse(cp.verified_success)
            self.assertEqual(cp.status, "failed")

    def test_checkpoint_resume_mid_loop(self) -> None:
        with tempfile.TemporaryDirectory() as tmp, z0home(str(Path(tmp) / "z0home")):
            root, patch = make_fixture_repo(Path(tmp) / "repo")
            cp = authorize_task(
                base_repo=root,
                patch=patch,
                requirement_paths=["REQUIREMENT.md", "app.py"],
            )
            tid = cp.task_id
            cp = run_until(cp, until="resolved", worktrees_root=Path(tmp) / "wts")
            self.assertEqual(cp.status, "resolved")
            self.assertTrue(Path(cp.aodl_path).is_file())
            cp2 = load_checkpoint(tid)
            self.assertEqual(cp2.status, "resolved")
            cp2 = resume_task(tid, until="verified", worktrees_root=Path(tmp) / "wts")
            self.assertTrue(cp2.verified_success)


if __name__ == "__main__":
    unittest.main()
