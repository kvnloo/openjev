"""Optional GPU parity vs upstream NanoJev runtime.

Skipped when checkpoint or CUDA is unavailable. Does not download weights.
"""

from __future__ import annotations

import json
import os
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
        p = Path.home() / ".z0int" / "models" / "nanojev_06b"
        if os.environ.get("Z0INT_HOME"):
            p = Path(os.environ["Z0INT_HOME"]).expanduser() / "models" / "nanojev_06b"
    if p.is_dir() and not validate_checkpoint_dir(p):
        return p
    return None


@pytest.mark.skipif(_checkpoint() is None, reason="NanoJev checkpoint not installed")
class NanoJevParityTests(unittest.TestCase):
    def test_eval_fixture_no_network(self):
        torch = pytest.importorskip("torch")
        if not torch.cuda.is_available():
            self.skipTest("CUDA required for NanoJev V0")
        ckpt = _checkpoint()
        assert ckpt is not None
        fixture = Path(__file__).resolve().parent / "fixtures" / "nanojev_request.json"
        request = request_from_mapping(json.loads(fixture.read_text(encoding="utf-8")))
        backend = NanoJevBackend(ckpt)
        result = backend.evaluate(request)
        self.assertEqual(result.backend, "nanojev")
        self.assertEqual(result.diagnostics.get("autoregressive_decode_steps"), 0)
        self.assertEqual(result.diagnostics.get("network_model_calls"), 0)
        self.assertEqual(len(result.answers), 3)
        for ans in result.answers:
            self.assertAlmostEqual(sum(ans.probabilities.values()), 1.0, places=5)


if __name__ == "__main__":
    unittest.main()
