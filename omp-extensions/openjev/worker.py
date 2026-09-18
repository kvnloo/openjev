#!/usr/bin/env python3
"""Resident OpenJev scorer: stdin JSONL -> stdout JSONL. Load the SLM once."""

from __future__ import annotations

import json
import os
import sys
import traceback


def _emit(payload: dict) -> None:
    sys.stdout.write(json.dumps(payload, allow_nan=False, separators=(",", ":")) + "\n")
    sys.stdout.flush()


def serve() -> int:
    from openjev_phase1.core import load_causal_model, validate_row
    from openjev_phase1.direct import score as direct_score

    source = os.environ.get("OPENJEV_MODEL", "Qwen/Qwen3.5-4B")
    revision = os.environ.get("OPENJEV_REVISION", "851bf6e806efd8d0a36b00ddf55e13ccb7b8cd0a")
    max_tokens = int(os.environ.get("OPENJEV_MAX_TOKENS", "4096"))
    try:
        model, tokenizer, metadata = load_causal_model(source, revision)
    except Exception as exc:  # noqa: BLE001 — worker must stay up and fail-open
        _emit({"ok": False, "error": f"load failed: {exc}", "fail_open": True})
        return 1
    sys.stderr.write(f"openjev worker ready {source} {revision[:12]}\n")
    sys.stderr.flush()
    for raw in sys.stdin:
        line = raw.strip()
        if not line:
            continue
        try:
            payload = json.loads(line)
            if not isinstance(payload, dict):
                raise ValueError("request must be a JSON object")
            if payload.get("action") == "status":
                _emit({"ok": True, "ready": True, "model": metadata})
                continue
            row = {
                "id": str(payload.get("id") or "omp"),
                "state": payload["state"],
                "question": payload["question"],
                "options": payload["options"],
            }
            validate_row(row)
            result = direct_score(model, tokenizer, row, metadata, max_tokens)
            _emit({"ok": True, **result})
        except Exception as exc:  # noqa: BLE001
            _emit(
                {
                    "ok": False,
                    "error": str(exc),
                    "fail_open": True,
                    "trace": traceback.format_exc()[-500:],
                }
            )
    return 0


if __name__ == "__main__":
    if len(sys.argv) > 1 and sys.argv[1] == "--worker":
        raise SystemExit(serve())
    print(json.dumps({"ok": False, "error": "use --worker"}))
    raise SystemExit(2)
