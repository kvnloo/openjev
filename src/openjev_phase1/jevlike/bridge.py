"""Bridge jevlike JSONL training data and checkpoints to OpenJev decision rows."""

from __future__ import annotations

import json
from pathlib import Path
from typing import Any

from .data import ChoiceExample, JsonlDataset
from .model import load_checkpoint
from .train import move


def choice_to_openjev_row(example: ChoiceExample, *, row_id: str = "trainable") -> dict[str, Any]:
    """Convert one jevlike example into an openjev direct-scoring row."""
    letters = "ABCDEFGHIJKLMNOP"
    if len(example.options) > len(letters):
        raise ValueError(f"At most {len(letters)} options supported in bridge rows")
    return {
        "id": row_id,
        "state": example.context,
        "question": "Which option best matches the context?",
        "options": [
            {"id": letters[index], "description": option}
            for index, option in enumerate(example.options)
        ],
        "label_index": example.label,
    }


def score_jsonl(
    checkpoint: str | Path,
    data: str | Path,
    *,
    device_name: str = "auto",
    batch_size: int = 64,
) -> list[dict[str, Any]]:
    """Score a jevlike JSONL file with a trained checkpoint."""
    from torch.utils.data import DataLoader

    from .model import select_device

    device = select_device(device_name)
    model, collator, _config = load_checkpoint(checkpoint, device)
    loader = DataLoader(
        JsonlDataset(data),
        batch_size=batch_size,
        collate_fn=collator,
    )
    results: list[dict[str, Any]] = []
    model.eval()
    with __import__("torch").no_grad():
        for host_batch in loader:
            batch = move(host_batch, device)
            logits = model(batch).cpu()
            probabilities = logits.softmax(-1)
            for row_index in range(logits.shape[0]):
                option_count = int(batch["option_mask"][row_index].sum())
                probs = probabilities[row_index, :option_count].tolist()
                choice = int(probs.index(max(probs)))
                results.append(
                    {
                        "choice_index": choice,
                        "probabilities": probs,
                        "top1_confidence": max(probs),
                    }
                )
    return results


def main() -> None:
    import argparse

    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("checkpoint")
    parser.add_argument("data", type=Path)
    parser.add_argument("--output", type=Path, required=True)
    parser.add_argument("--device", default="auto")
    parser.add_argument("--batch-size", type=int, default=64)
    args = parser.parse_args()
    rows = score_jsonl(args.checkpoint, args.data, device_name=args.device, batch_size=args.batch_size)
    args.output.parent.mkdir(parents=True, exist_ok=True)
    with args.output.open("w", encoding="utf-8") as handle:
        for row in rows:
            handle.write(json.dumps(row) + "\n")
    print(json.dumps({"scored": len(rows), "output": str(args.output)}))


if __name__ == "__main__":
    main()
