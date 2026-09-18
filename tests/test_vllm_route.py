"""Tests for Route C (vLLM DiffusionGemma structured reads)."""

from __future__ import annotations

import json
from unittest import mock

import pytest

from openjev_phase1.vllm_route.client import parse_token_logprobs, slot_distribution
from openjev_phase1.vllm_route.config import VllmDiffusionConfig
from openjev_phase1.vllm_route.score import score_row
from openjev_phase1.vllm_route.template import (
    TemplateError,
    answer_line,
    resolve_template,
    system_text,
)


class LetterTokenizer:
    def encode(self, text: str, add_special_tokens: bool = False) -> list[int]:
        assert add_special_tokens is False
        return [ord(character) for character in text]

    @property
    def vocab_size(self) -> int:
        return 512


ROW = {
    "id": "case-1",
    "state": "The deployment completed without rollback.",
    "question": "Did the deployment succeed?",
    "options": [
        {"id": "yes", "description": "Deployment succeeded."},
        {"id": "no", "description": "Deployment failed."},
    ],
}


def test_system_text_lists_option_letters():
    rendered = system_text(ROW)
    assert "Question decision:" in rendered
    assert "  A: Deployment succeeded." in rendered
    assert "  B: Deployment failed." in rendered

def test_resolve_template_finds_single_slot():
    scaffold = [1, 2, 3]
    template, slots = resolve_template(
        LetterTokenizer(),
        2,
        canvas=64,
        scaffold=scaffold,
    )
    slot_index = len(scaffold) + len("decision: ")
    assert slots[0]["pos"] == slot_index
    assert slots[0]["label_ids"] == [ord("A"), ord("B")]
    assert template[slot_index] == ord("A")


def test_resolve_template_rejects_multi_token_label_swap():
    class CollapseTokenizer(LetterTokenizer):
        def encode(self, text: str, add_special_tokens: bool = False) -> list[int]:
            if text.endswith("B"):
                return super().encode(text + "extra")
            return super().encode(text)

    with pytest.raises(TemplateError, match="single-token"):
        resolve_template(CollapseTokenizer(), 2, canvas=64, scaffold=[])

def test_slot_distribution_normalizes_label_probs():
    distribution = slot_distribution({65: -0.1, 66: -2.0}, [65, 66])
    assert abs(sum(distribution["probs"]) - 1.0) < 1e-6
    assert distribution["probs"][0] > distribution["probs"][1]


def test_parse_token_logprobs_accepts_token_id_prefix():
    parsed = parse_token_logprobs(
        {
            "top_logprobs": [
                {"token": "token_id:65", "logprob": -0.2},
                {"token": "token_id:66", "logprob": -1.4},
            ]
        }
    )
    assert parsed[65] == pytest.approx(-0.2)


def test_score_row_calls_upstream_and_emits_openjev_shape():
    config = VllmDiffusionConfig(
        upstream="http://127.0.0.1:8000",
        model="dgemma",
        tokenizer="fake-tokenizer",
        canvas=64,
        seed=7,
    )
    tokenizer = LetterTokenizer()
    scaffold = tokenizer.encode("<|channel>thought\n<channel|>", add_special_tokens=False)
    _, slots = resolve_template(tokenizer, 2, canvas=64, scaffold=scaffold)
    slot_index = slots[0]["pos"]
    content = [{"top_logprobs": []} for _ in range(slot_index + 1)]
    content[slot_index] = {
        "top_logprobs": [
            {"token": f"token_id:{token_id}", "logprob": logprob}
            for token_id, logprob in ((65, -0.1), (66, -2.0))
        ]
    }
    fake_response = {
        "choices": [{"logprobs": {"content": content}}],
        "usage": {"prompt_tokens": 128},
    }

    with mock.patch(
        "openjev_phase1.vllm_route.score.upstream_chat",
        return_value=fake_response,
    ) as upstream:
        result = score_row(config, ROW, tokenizer=tokenizer)

    upstream.assert_called_once()
    body = upstream.call_args.args[1]
    assert body["vllm_xargs"]["diffusion_read_only"] is True
    assert body["logprob_token_ids"] == [65, 66]
    assert result["id"] == "case-1"
    assert result["option_ids"] == ["yes", "no"]
    assert len(result["probabilities"]) == 2
    assert result["model"]["backend"] == "vllm-diffusion"
    assert json.dumps(result)
