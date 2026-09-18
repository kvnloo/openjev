from __future__ import annotations

import tempfile
import unittest
from pathlib import Path

from z0int.backends.base import (
    DecisionOption,
    DecisionQuestion,
    DecisionRequest,
)
from z0int.backends.nanojev import NanoJevBackend
from z0int.backends.nanojev_runtime import (
    build_examples,
    canonical_state_text,
    validate_checkpoint_dir,
)


class FakeTokenizer:
    eos_token_id = 0

    def encode(self, text, add_special_tokens=False):
        self.last = text
        return [ord(c) % 251 + 1 for c in text]


class NanoJevAdapterTests(unittest.TestCase):
    def test_health_missing_checkpoint_is_clean(self):
        with tempfile.TemporaryDirectory() as tmp:
            b = NanoJevBackend(Path(tmp))
            h = b.health()
            self.assertFalse(h.ready)
            self.assertIn("missing", h.detail)

    def test_validate_required_entries(self):
        with tempfile.TemporaryDirectory() as tmp:
            root = Path(tmp)
            self.assertIn("best.safetensors", validate_checkpoint_dir(root))
            (root / "best.safetensors").write_bytes(b"x")
            self.assertIn("config.json", validate_checkpoint_dir(root))
            (root / "config.json").write_text("{}")
            self.assertIn("tokenizer/", validate_checkpoint_dir(root))
            (root / "tokenizer").mkdir()
            self.assertIn("backbone_config/", validate_checkpoint_dir(root))
            (root / "backbone_config").mkdir()
            self.assertEqual(validate_checkpoint_dir(root), [])

    def test_question_id_not_semantic_input(self):
        tok = FakeTokenizer()
        q1 = DecisionQuestion(
            id="transport-a",
            type="choice",
            instructions="Pick the relevant route.",
            options=(
                DecisionOption("local", "Use local"),
                DecisionOption("frontier", "Escalate"),
            ),
        )
        q2 = DecisionQuestion(
            id="transport-b",
            type="choice",
            instructions="Pick the relevant route.",
            options=q1.options,
        )
        a = build_examples(DecisionRequest(state={"x": 1}, questions=(q1,)), tok, 4096)[0]
        b = build_examples(DecisionRequest(state={"x": 1}, questions=(q2,)), tok, 4096)[0]
        self.assertEqual(a.leaf_tokens, b.leaf_tokens)
        # transport ids must not appear in encoded text path length equality already; check last encode
        joined = " ".join(str(x) for x in a.leaf_tokens)
        self.assertNotIn("transport-a", joined)

    def test_structured_state_is_canonical(self):
        self.assertEqual(
            canonical_state_text({"b": 2, "a": 1}),
            canonical_state_text({"a": 1, "b": 2}),
        )

    def test_no_silent_truncation(self):
        tok = FakeTokenizer()
        q = DecisionQuestion(
            id="q",
            type="choice",
            instructions="x" * 200,
            options=(DecisionOption("a", "A"), DecisionOption("b", "B")),
        )
        with self.assertRaises(ValueError):
            build_examples(DecisionRequest(state="s", questions=(q,)), tok, max_length=8)


if __name__ == "__main__":
    unittest.main()
