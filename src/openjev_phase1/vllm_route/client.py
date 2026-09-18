"""Minimal HTTP client for vLLM OpenAI-compatible diffusion reads."""

from __future__ import annotations

import json
import math
import urllib.error
import urllib.request
from typing import Any

from .config import VllmDiffusionConfig


class VllmUpstreamError(RuntimeError):
    """Raised when the vLLM server returns an HTTP or transport error."""


def upstream_chat(config: VllmDiffusionConfig, body: dict[str, Any]) -> dict[str, Any]:
    request = urllib.request.Request(
        config.upstream.rstrip("/") + "/v1/chat/completions",
        data=json.dumps(body).encode(),
        headers={"content-type": "application/json"},
        method="POST",
    )
    try:
        with urllib.request.urlopen(request, timeout=config.timeout) as response:
            return json.load(response)
    except urllib.error.HTTPError as error:
        detail = error.read(512).decode(errors="replace")
        raise VllmUpstreamError(f"upstream HTTP {error.code}: {detail}") from error
    except urllib.error.URLError as error:
        raise VllmUpstreamError(f"upstream unreachable: {error.reason}") from error


def slot_distribution(top: dict[int, float], label_ids: list[int]) -> dict[str, Any]:
    """Label probabilities from returned logprobs at one canvas slot."""
    floor = min(top.values()) - 5.0 if top else -20.0
    logps = [top.get(token_id, floor) for token_id in label_ids]
    maximum = max(logps)
    weights = [math.exp(value - maximum) for value in logps]
    total = sum(weights)
    probs = [weight / total for weight in weights]
    return {
        "probs": probs,
        "entropy": -sum(
            math.exp(value) * value for value in top.values() if math.isfinite(value)
        ),
        "argmax_is_label": bool(top) and max(top, key=top.get) in label_ids,
    }


def parse_token_logprobs(content_row: dict[str, Any]) -> dict[int, float]:
    parsed: dict[int, float] = {}
    for entry in content_row.get("top_logprobs") or []:
        token = entry["token"]
        if isinstance(token, str) and token.startswith("token_id:"):
            token_id = int(token.split(":", 1)[1])
        else:
            token_id = int(token)
        parsed[token_id] = float(entry["logprob"])
    return parsed
