"""Score OpenJev rows through a vLLM DiffusionGemma structured read."""

from __future__ import annotations

import time
from typing import Any

from openjev_phase1.core import digest

from .client import parse_token_logprobs, slot_distribution, upstream_chat
from .config import VllmDiffusionConfig
from .template import (
    PROMPT_VERSION,
    build_canvas,
    canvas_width,
    resolve_template,
    state_text,
    system_text,
)


def _load_tokenizer(source: str):
    import transformers

    return transformers.AutoTokenizer.from_pretrained(source, trust_remote_code=True)


def score_row(
    config: VllmDiffusionConfig,
    row: dict[str, Any],
    *,
    tokenizer=None,
    seed: int | None = None,
) -> dict[str, Any]:
    """Score one OpenJev row; output shape matches ``direct.score``."""
    started = time.perf_counter()
    tokenizer = tokenizer or _load_tokenizer(config.tokenizer)
    scaffold = tokenizer.encode(config.scaffold_text, add_special_tokens=False)
    template, slots = resolve_template(
        tokenizer,
        len(row["options"]),
        canvas=config.canvas,
        scaffold=scaffold,
    )
    slot = slots[0]
    width = canvas_width(template, canvas=config.canvas, canvas_step=config.canvas_step)
    vocab_size = int(getattr(tokenizer, "vocab_size", None) or len(tokenizer))
    seed_value = config.seed if seed is None else seed
    canvas = build_canvas(
        template,
        slot,
        width=width,
        turn_close_token=config.turn_close_token,
        pad_token=config.pad_token,
        seed=seed_value,
        vocab_size=vocab_size,
    )
    sys_text = system_text(row)
    user_text = state_text(row)
    body = {
        "model": config.model,
        "messages": [
            {"role": "system", "content": sys_text},
            {"role": "user", "content": user_text},
        ],
        "max_tokens": len(template) + 1,
        "logprobs": True,
        "top_logprobs": config.top_logprobs,
        "logprob_token_ids": sorted(set(slot["label_ids"])),
        "return_tokens_as_token_ids": True,
        "chat_template_kwargs": {"enable_thinking": False},
        "vllm_xargs": {
            "diffusion_seed_canvas": canvas,
            "diffusion_canvas_length": width,
            "diffusion_max_steps": config.steps,
            "diffusion_read_only": True,
        },
    }
    forward_start = time.perf_counter()
    response = upstream_chat(config, body)
    content = response["choices"][0]["logprobs"]["content"]
    top = parse_token_logprobs(content[slot["pos"]])
    distribution = slot_distribution(top, slot["label_ids"])
    return {
        "id": row["id"],
        "option_ids": [option["id"] for option in row["options"]],
        "probabilities": distribution["probs"],
        "option_logits": None,
        "input_tokens": response.get("usage", {}).get("prompt_tokens"),
        "forward_seconds": time.perf_counter() - forward_start,
        "total_seconds": time.perf_counter() - started,
        "prompt_sha256": digest(sys_text + "\n" + user_text),
        "prompt_version": PROMPT_VERSION,
        "model": {
            "backend": "vllm-diffusion",
            "upstream": config.upstream,
            "served_model": config.model,
            "tokenizer": config.tokenizer,
            "canvas_width": width,
            "steps": config.steps,
        },
        "readout": "vLLM DiffusionGemma read-only canvas logprobs (PR #57250)",
        "probability_status": "conditional option score; uncalibrated as decision confidence",
        "diagnostics": {
            "entropy": distribution["entropy"],
            "argmax_is_label": distribution["argmax_is_label"],
            "slot_pos": slot["pos"],
        },
    }
