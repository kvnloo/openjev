"""Create-only JSONL command line scorer."""

from __future__ import annotations

import argparse
import json
from pathlib import Path

from .core import load_causal_model, validate_row
from .direct import score as direct_score
from .reranker import score as reranker_score
from .serial import SerialPrefixScorer
from .shared import score_shared


def _add_vllm_arguments(parser: argparse.ArgumentParser) -> None:
    parser.add_argument(
        "--upstream",
        default="http://127.0.0.1:8000",
        help="vLLM OpenAI server base URL (mode vllm)",
    )
    parser.add_argument(
        "--tokenizer",
        default=None,
        help="HF tokenizer id/path for canvas templates (defaults to --model)",
    )
    parser.add_argument("--canvas", type=int, default=64, help="Served diffusion canvas length")
    parser.add_argument("--canvas-step", type=int, default=16, help="Request width rounding")
    parser.add_argument("--steps", type=int, default=1, help="diffusion_max_steps per read")
    parser.add_argument("--seed", type=int, default=42, help="Canvas noise seed")


def main() -> None:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument(
        "--mode",
        choices=("direct", "serial", "shared", "reranker", "vllm"),
        required=True,
    )
    parser.add_argument("--model", required=True)
    parser.add_argument("--revision", default="", help="Pinned HF revision (not used for mode vllm)")
    parser.add_argument("--input", type=Path, required=True)
    parser.add_argument("--output", type=Path, required=True)
    parser.add_argument("--max-tokens", type=int, default=4096)
    _add_vllm_arguments(parser)
    args = parser.parse_args()
    if args.output.exists() or args.max_tokens < 1:
        parser.error("Output must be new and max-tokens must be positive")
    rows = [json.loads(line) for line in args.input.read_text().splitlines() if line.strip()]
    if not rows:
        parser.error("Input is empty")
    for row in rows:
        validate_row(row)
    args.output.parent.mkdir(parents=True, exist_ok=True)
    if args.mode == "vllm":
        from .vllm_route import VllmDiffusionConfig, score_row

        config = VllmDiffusionConfig(
            upstream=args.upstream,
            model=args.model,
            tokenizer=args.tokenizer or args.model,
            canvas=args.canvas,
            canvas_step=args.canvas_step,
            steps=args.steps,
            seed=args.seed,
        )
        with args.output.open("x") as destination:
            for index, row in enumerate(rows):
                result = score_row(config, row, seed=config.seed + index)
                destination.write(json.dumps(result, allow_nan=False) + "\n")
                destination.flush()
        return
    if not args.revision:
        parser.error("--revision is required unless --mode vllm")
    model, tokenizer, metadata = load_causal_model(args.model, args.revision)
    with args.output.open("x") as destination:
        if args.mode == "shared":
            results, timing = score_shared(model, tokenizer, rows, metadata, args.max_tokens)
            for result in results:
                destination.write(json.dumps({**result, "shared_timing": timing}, allow_nan=False) + "\n")
        elif args.mode == "serial":
            scorer = SerialPrefixScorer(model, tokenizer, metadata, args.max_tokens)
            for row in rows:
                destination.write(json.dumps(scorer.score(row), allow_nan=False) + "\n")
                destination.flush()
        else:
            scorer = direct_score if args.mode == "direct" else reranker_score
            for row in rows:
                destination.write(json.dumps(scorer(model, tokenizer, row, metadata, args.max_tokens), allow_nan=False) + "\n")
                destination.flush()


if __name__ == "__main__":
    main()
