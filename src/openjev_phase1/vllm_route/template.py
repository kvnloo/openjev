"""Map OpenJev decision rows to DiffusionGemma answer templates."""

from __future__ import annotations

import json
from typing import Any

from openjev_phase1.core import LETTERS, validate_row

PROMPT_VERSION = "vllm-diffusion-choice-v1"
QUESTION_ID = "decision"
REPLY_INSTRUCTION = (
    'Reply with one line formatted as "decision: label" where label is exactly one listed option letter.'
)


class TemplateError(ValueError):
    """Raised when a row cannot be compiled into a diffusion canvas."""


def _enc(tokenizer, text: str) -> list[int]:
    return tokenizer.encode(text, add_special_tokens=False)


def system_text(row: dict) -> str:
    validate_row(row)
    lines = [
        "Answer one decision question about the state the user provides.",
        "Each listed answer is a single letter; reply with exactly one letter.",
        "",
        f"Question {QUESTION_ID}: {row['question'].strip()}",
    ]
    for index, option in enumerate(row["options"]):
        letter = LETTERS[index]
        lines.append(f"  {letter}: {option['description'].strip()}")
    lines.extend(["", REPLY_INSTRUCTION])
    return "\n".join(lines)


def state_text(row: dict) -> str:
    validate_row(row)
    return json.dumps({"evidence": row["state"]}, ensure_ascii=False)


def answer_line(labels: list[str], choice_index: int) -> str:
    return f"{QUESTION_ID}: {labels[choice_index]}"


def resolve_template(
    tokenizer,
    option_count: int,
    *,
    canvas: int,
    scaffold: list[int],
) -> tuple[list[int], list[dict[str, Any]]]:
    """Return `(template_without_close, slots)` for one OpenJev row."""
    if option_count < 2 or option_count > len(LETTERS):
        raise TemplateError(f"Need 2-{len(LETTERS)} options; got {option_count}")
    labels = LETTERS[:option_count]
    base = scaffold + _enc(tokenizer, answer_line(labels, 0))
    if len(base) + 1 > canvas:
        raise TemplateError(
            f"Answer template is {len(base) + 1} tokens; canvas holds {canvas - 1} before turn close"
        )

    slot_pos: int | None = None
    label_ids = [0] * option_count
    for choice_index in range(1, option_count):
        encoded = scaffold + _enc(tokenizer, answer_line(labels, choice_index))
        if len(encoded) != len(base):
            raise TemplateError(
                f"Option letter {labels[choice_index]!r} is not a single-token label swap"
            )
        diffs = [index for index in range(len(encoded)) if encoded[index] != base[index]]
        if len(diffs) != 1 or (slot_pos is not None and diffs[0] != slot_pos):
            raise TemplateError("Option letters do not share one template slot")
        slot_pos = diffs[0]
        label_ids[choice_index] = encoded[slot_pos]
    if slot_pos is None:
        raise TemplateError("Need at least two options to locate an answer slot")
    label_ids[0] = base[slot_pos]
    if len(set(label_ids)) != len(label_ids):
        raise TemplateError("Two option letters tokenize to the same id")
    return base, [{"pos": slot_pos, "label_ids": label_ids, "labels": labels}]


def canvas_width(template: list[int], *, canvas: int, canvas_step: int) -> int:
    need = len(template) + 1
    width = min(canvas, -(-need // canvas_step) * canvas_step)
    return max(width, need)


def build_canvas(
    template: list[int],
    slot: dict[str, Any],
    *,
    width: int,
    turn_close_token: int,
    pad_token: int,
    seed: int,
    vocab_size: int,
) -> list[int]:
    import random

    rng = random.Random(seed)
    canvas = list(template) + [turn_close_token]
    canvas += [pad_token] * (width - len(canvas))
    for index in range(len(template)):
        if index == slot["pos"]:
            canvas[index] = rng.randrange(vocab_size)
        else:
            canvas[index] = rng.randrange(1, min(vocab_size, 4096))
    return canvas
