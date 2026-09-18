"""GPU smoke + optional upstream NanoJev probability parity.

Skipped when checkpoint (or CUDA / upstream) is unavailable. Never downloads.
"""

from __future__ import annotations

import importlib.util
import json
import os
import subprocess
import unittest
from pathlib import Path

import pytest

from z0int.backends.base import request_from_mapping
from z0int.backends.nanojev import NanoJevBackend
from z0int.backends.nanojev_runtime import validate_checkpoint_dir


def _checkpoint() -> Path | None:
    env = os.environ.get("Z0INT_NANOJEV_CHECKPOINT")
    if env:
        p = Path(env).expanduser()
    else:
        home = Path(os.environ["Z0INT_HOME"]).expanduser() if os.environ.get("Z0INT_HOME") else Path.home() / ".z0int"
        p = home / "models" / "nanojev_06b"
    if p.is_dir() and not validate_checkpoint_dir(p):
        return p
    return None


def _upstream_predict_module():
    """Load NanoJev fork predict_toy_decisions if checkout is present."""
    candidates = []
    env = os.environ.get("NANOJEV_ROOT")
    if env:
        candidates.append(Path(env))
    candidates.extend(
        [
            Path.home() / "tmp" / "NanoJev",
            Path("/home/kvn/tmp/NanoJev"),
            Path(__file__).resolve().parents[2].parent / "NanoJev",
        ]
    )
    for root in candidates:
        script = root / "scripts" / "predict_toy_decisions.py"
        if script.is_file():
            spec = importlib.util.spec_from_file_location("nanojev_upstream_predict", script)
            if spec is None or spec.loader is None:
                continue
            mod = importlib.util.module_from_spec(spec)
            spec.loader.exec_module(mod)
            return mod, root
    return None, None


def _fixture_pair(state_as_string: bool = True):
    fixture = json.loads(
        (Path(__file__).resolve().parent / "fixtures" / "nanojev_request.json").read_text(encoding="utf-8")
    )
    state_obj = fixture["state"]
    state_text = json.dumps(
        state_obj, ensure_ascii=False, allow_nan=False, sort_keys=True, separators=(",", ":")
    )
    state = state_text if state_as_string else state_obj
    z0_raw = {"state": state, "questions": fixture["questions"]}
    questions = {}
    for q in fixture["questions"]:
        item = {"type": q["type"], "instructions": q["instructions"]}
        if q["type"] == "choice":
            item["criteria"] = {c["id"]: c["description"] for c in q["criteria"]}
        elif q["type"] == "score":
            item["criteria"] = list(q["criteria"])
        elif "criteria" in q:
            item["criteria"] = q["criteria"]
        questions[q["id"]] = item
    up_raw = {"states": [{"id": "s0", "state": state_text, "questions": questions}]}
    return z0_raw, up_raw


@pytest.mark.skipif(_checkpoint() is None, reason="NanoJev checkpoint not installed")
class NanoJevParityTests(unittest.TestCase):
    def test_eval_fixture_no_network(self):
        torch = pytest.importorskip("torch")
        if not torch.cuda.is_available():
            self.skipTest("CUDA required for NanoJev V0")
        ckpt = _checkpoint()
        assert ckpt is not None
        z0_raw, _ = _fixture_pair(state_as_string=True)
        request = request_from_mapping(z0_raw)
        backend = NanoJevBackend(ckpt)
        result = backend.evaluate(request)
        self.assertEqual(result.backend, "nanojev")
        self.assertEqual(result.diagnostics.get("autoregressive_decode_steps"), 0)
        self.assertEqual(result.diagnostics.get("network_model_calls"), 0)
        self.assertEqual(len(result.answers), 3)
        for ans in result.answers:
            self.assertAlmostEqual(sum(ans.probabilities.values()), 1.0, places=5)

    def test_upstream_probability_parity(self):
        torch = pytest.importorskip("torch")
        np = pytest.importorskip("numpy")
        if not torch.cuda.is_available():
            self.skipTest("CUDA required for NanoJev V0")
        mod, root = _upstream_predict_module()
        if mod is None:
            self.skipTest("NanoJev upstream checkout not found (set NANOJEV_ROOT)")
        ckpt = _checkpoint()
        assert ckpt is not None
        z0_raw, up_raw = _fixture_pair(state_as_string=True)
        backend = NanoJevBackend(ckpt)
        z0 = backend.evaluate(request_from_mapping(z0_raw))
        engine = mod.DecisionPredictor(str(ckpt), device_name="cuda:0", precision="bf16")
        up = engine.predict(up_raw, batch_questions=0, temperature=1.0)
        up_answers = up["states"][0]["answers"]
        for ans in z0.answers:
            u = up_answers[ans.question_id]["probabilities"]
            keys = list(ans.probabilities.keys())
            self.assertEqual(keys, list(u.keys()))
            zp = np.array([ans.probabilities[k] for k in keys], dtype=np.float64)
            upv = np.array([u[k] for k in keys], dtype=np.float64)
            np.testing.assert_allclose(zp, upv, rtol=1e-5, atol=1e-6)


if __name__ == "__main__":
    unittest.main()
