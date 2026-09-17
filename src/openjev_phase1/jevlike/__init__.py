"""Trainable one-pass option scorers ported from vinnylarouge/jevlike (MIT).

Route A: train a small attention head (byte encoder or frozen HF encoder) on
labelled JSONL when zero-shot direct logit readout (Route B) is not enough.
"""

from __future__ import annotations

from .model import (
    AttentionHead,
    FrozenTransformerScorer,
    TinyScorer,
    load_checkpoint,
    make_system,
    select_device,
)
from .vision import CHESS_OPTION_IDS, DOOM_OPTION_IDS, TOTAL_OPTIONS, DoomScorerV2

__all__ = [
    "AttentionHead",
    "CHESS_OPTION_IDS",
    "DOOM_OPTION_IDS",
    "DoomScorerV2",
    "FrozenTransformerScorer",
    "TOTAL_OPTIONS",
    "TinyScorer",
    "load_checkpoint",
    "make_system",
    "select_device",
]

__version__ = "0.2.0"
