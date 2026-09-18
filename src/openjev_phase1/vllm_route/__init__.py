"""Route C: Jev-like structured reads via vLLM DiffusionGemma (PR #57250)."""

from .config import VllmDiffusionConfig
from .score import score_row

__all__ = ["VllmDiffusionConfig", "score_row"]
