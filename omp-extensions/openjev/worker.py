#!/usr/bin/env python3
"""Resident OpenJev scorer: stdin JSONL -> stdout JSONL. Load the SLM once.

Actions:
  status
  decide     {state, question, options}  — OpenJev row
  systemone  {state, questions}          — TypeSafe Jev / System One shape
"""

from __future__ import annotations

import json
import math
import os
import sys
import time
import traceback


def _emit(payload: dict) -> None:
    sys.stdout.write(json.dumps(payload, allow_nan=False, separators=(",", ":")) + "\n")
    sys.stdout.flush()


def _confidence(probs: list[float]) -> float:
    if len(probs) < 2:
        return 1.0
    entropy = -sum(p * math.log(p) for p in probs if p > 0)
    return max(0.0, min(1.0, 1.0 - entropy / math.log(len(probs))))


def _question_row(qid: str, state, question: dict) -> dict:
    kind = question.get("type")
    if kind in {"bool", "boolean"}:
        kind = "noul"
    instructions = str(question.get("instructions") or "").strip() or qid
    if kind == "noul":
        criteria = question.get("criteria") or {}
        yes = criteria.get("true") or "Yes"
        no = criteria.get("false") or "No"
        options = [{"id": "yes", "description": yes}, {"id": "no", "description": no}]
    elif kind == "choice":
        criteria = question.get("criteria") or {}
        if not isinstance(criteria, dict) or len(criteria) < 2:
            raise ValueError(f"{qid}: choice needs criteria with >= 2 options")
        options = [
            {"id": str(name), "description": str(rubric) if rubric else str(name)}
            for name, rubric in criteria.items()
        ]
    elif kind == "score":
        levels = question.get("criteria") or []
        if not isinstance(levels, list) or len(levels) < 2:
            raise ValueError(f"{qid}: score needs ordered criteria with >= 2 levels")
        options = [{"id": str(index), "description": str(level)} for index, level in enumerate(levels)]
    else:
        raise ValueError(f"{qid}: unknown type {kind!r}")
    return {
        "id": qid,
        "state": state,
        "question": instructions,
        "options": options,
        "_kind": kind,
    }


def _answer(kind: str, scored: dict) -> dict:
    ids = scored["option_ids"]
    probs = scored["probabilities"]
    if kind == "noul":
        yes = dict(zip(ids, probs)).get("yes", probs[0])
        return {"type": "noul", "noul": yes}
    if kind == "choice":
        mapping = dict(zip(ids, probs))
        choice = ids[max(range(len(probs)), key=probs.__getitem__)]
        return {
            "type": "choice",
            "choice": choice,
            "probabilities": mapping,
            "confidence": _confidence(probs),
        }
    mapping = {str(i): p for i, p in enumerate(probs)}
    score = sum(i * p for i, p in enumerate(probs))
    return {
        "type": "score",
        "score": score,
        "probabilities": mapping,
        "confidence": _confidence(probs),
    }


def systemone(model, tokenizer, metadata, payload: dict, max_tokens: int) -> dict:
    from openjev_phase1.core import validate_row
    from openjev_phase1.direct import score as direct_score

    state = payload.get("state")
    questions = payload.get("questions")
    if not isinstance(questions, dict) or not questions:
        raise ValueError("systemone requires a nonempty questions object")
    rows = [_question_row(qid, state, q) for qid, q in questions.items()]
    answers = {}
    started = time.perf_counter()
    for row in rows:
        kind = row.pop("_kind")
        validate_row(row)
        scored = direct_score(model, tokenizer, row, metadata, max_tokens)
        answers[row["id"]] = _answer(kind, scored)
    return {
        "ok": True,
        "api": "openjev",
        "model": metadata,
        "answers": answers,
        "total_seconds": time.perf_counter() - started,
    }


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
            action = payload.get("action")
            if action == "status":
                _emit({"ok": True, "ready": True, "model": metadata})
                continue
            if action == "systemone" or "questions" in payload:
                _emit(systemone(model, tokenizer, metadata, payload, max_tokens))
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
