"""Configuration for the vLLM DiffusionGemma structured-read route."""

from __future__ import annotations

from dataclasses import dataclass


@dataclass(frozen=True)
class VllmDiffusionConfig:
    """HTTP client settings for vLLM structured diffusion reads."""

    upstream: str
    model: str
    tokenizer: str
    canvas: int = 64
    canvas_step: int = 16
    steps: int = 1
    top_logprobs: int = 20
    turn_close_token: int = 106
    pad_token: int = 0
    scaffold_text: str = "<|channel>thought\n<channel|>"
    timeout: float = 600.0
    seed: int = 42

    def __post_init__(self) -> None:
        if self.canvas < 8:
            raise ValueError("canvas must be at least 8")
        if self.canvas_step < 1:
            raise ValueError("canvas_step must be positive")
        if self.steps < 1:
            raise ValueError("steps must be at least 1")
