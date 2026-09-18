"""Offline skeptic battery for os.next_context Flow episodes.

No promotion. Compares:
  global prior
  last-state transition table  (current production head)
  recency-weighted transition table
  2-gram / 3-gram of context_id
against log-loss, Brier, top-1/top-3, coverage@high-precision, calibration.

Split by time (first 70% train / last 30% test) — never random rows.
"""

from __future__ import annotations

import json
import math
from collections import Counter, defaultdict
from pathlib import Path
from typing import Any

from . import paths

EPS = 1e-12


def _load_jsonl(path: Path) -> list[dict[str, Any]]:
    if not path.is_file():
        return []
    rows: list[dict[str, Any]] = []
    for line in path.read_text(encoding="utf-8").splitlines():
        line = line.strip()
        if not line:
            continue
        try:
            rows.append(json.loads(line))
        except json.JSONDecodeError:
            continue
    return rows


def _softmax_from_counts(counts: Counter[str], vocab: set[str], alpha: float = 0.5) -> dict[str, float]:
    total = sum(counts.values())
    if not vocab:
        return {}
    denom = total + alpha * len(vocab)
    return {k: (counts.get(k, 0) + alpha) / denom for k in vocab}


def _logloss(p: float) -> float:
    return -math.log(max(min(p, 1.0 - EPS), EPS))


def _brier(p: float, y: int) -> float:
    return (p - y) ** 2


def _metrics(preds: list[dict[str, Any]]) -> dict[str, Any]:
    """preds: list of {p_true, rank, conf, correct_top1, correct_top3}"""
    if not preds:
        return {"n": 0}
    n = len(preds)
    ll = sum(_logloss(p["p_true"]) for p in preds) / n
    br = sum(_brier(p["p_true"], 1) for p in preds) / n
    top1 = sum(1 for p in preds if p["correct_top1"]) / n
    top3 = sum(1 for p in preds if p["correct_top3"]) / n
    # coverage @ high precision: fraction of rows where conf>=0.8 AND correct
    high = [p for p in preds if p["conf"] >= 0.8]
    if high:
        cov = len(high) / n
        prec = sum(1 for p in high if p["correct_top1"]) / len(high)
    else:
        cov, prec = 0.0, None
    # simple calibration: mean conf vs mean accuracy in bins
    bins = []
    for lo in (0.0, 0.2, 0.4, 0.6, 0.8):
        hi = lo + 0.2
        bucket = [p for p in preds if lo <= p["conf"] < hi or (hi >= 1.0 and p["conf"] >= lo)]
        if not bucket:
            continue
        bins.append(
            {
                "lo": lo,
                "hi": hi,
                "n": len(bucket),
                "mean_conf": round(sum(p["conf"] for p in bucket) / len(bucket), 4),
                "acc": round(sum(1 for p in bucket if p["correct_top1"]) / len(bucket), 4),
            }
        )
    return {
        "n": n,
        "log_loss": round(ll, 6),
        "brier": round(br, 6),
        "top1": round(top1, 4),
        "top3": round(top3, 4),
        "coverage_at_0.8": round(cov, 4),
        "precision_at_0.8": None if prec is None else round(prec, 4),
        "calibration_bins": bins,
    }


def _eval_model(
    name: str,
    test: list[dict[str, Any]],
    dist_fn,
) -> dict[str, Any]:
    out_rows = []
    for ep in test:
        dist = dist_fn(ep)
        if not dist:
            continue
        actual = ep["actual_id"]
        items = sorted(dist.items(), key=lambda kv: (-kv[1], kv[0]))
        top = [k for k, _ in items[:3]]
        conf = items[0][1] if items else 0.0
        p_true = dist.get(actual, EPS)
        rank = next((i for i, (k, _) in enumerate(items) if k == actual), None)
        out_rows.append(
            {
                "p_true": p_true,
                "rank": rank,
                "conf": conf,
                "correct_top1": bool(top and top[0] == actual),
                "correct_top3": actual in top,
            }
        )
    return {"model": name, **_metrics(out_rows)}


def build_episodes_from_shadow(shadow_rows: list[dict[str, Any]], *, horizon_ms: int | None = None) -> list[dict[str, Any]]:
    """Convert scored shadow/horizon rows into (context_id → actual) episodes."""
    eps: list[dict[str, Any]] = []
    for row in shadow_rows:
        # Prefer multi-horizon labels when present.
        horizons = row.get("horizons") or []
        if horizons and horizon_ms is not None:
            hz = next((h for h in horizons if int(h.get("horizon_ms") or 0) == horizon_ms), None)
            if not hz or hz.get("closed_at") is None:
                continue
            actual_id = hz.get("actual_context_id") or row.get("context_id")
            family = hz.get("actual_family") or "noop"
            ts = float(row.get("ts") or 0.0)
            eps.append(
                {
                    "ts": ts,
                    "context_id": row.get("context_id"),
                    "actual_id": actual_id if family != "noop" else row.get("context_id"),
                    "actual_family": family,
                    "prev_ids": [row.get("context_id")],
                }
            )
            continue
        if row.get("matched_at") is None:
            continue
        actual_id = row.get("actual_context_id")
        if not actual_id:
            continue
        family = row.get("actual_family") or ""
        # noop → stay = same context
        if family in {"noop", "stay"}:
            actual_id = row.get("context_id")
        eps.append(
            {
                "ts": float(row.get("ts") or 0.0),
                "context_id": row.get("context_id"),
                "actual_id": actual_id,
                "actual_family": family,
                "prev_ids": [row.get("context_id")],
            }
        )
    eps.sort(key=lambda e: e["ts"])
    # Attach 2/3-gram history
    hist: list[str] = []
    for e in eps:
        e["prev_ids"] = list(hist[-2:])
        hist.append(e["context_id"])
        # after outcome, chain continues from actual
        hist.append(e["actual_id"])
    return eps


def run_skeptic(
    *,
    root: Path | None = None,
    horizon_ms: int | None = 2000,
    train_frac: float = 0.7,
) -> dict[str, Any]:
    layout = paths.ensure_layout(root)
    sh_path = layout["episodes"] / "os_next_context_shadow.jsonl"
    rows = _load_jsonl(sh_path)
    episodes = build_episodes_from_shadow(rows, horizon_ms=horizon_ms)
    if len(episodes) < 8:
        # Fall back to closed context episodes if shadow is thin.
        ep_path = layout["episodes"] / "os_next_context.jsonl"
        raw = _load_jsonl(ep_path)
        episodes = []
        hist: list[str] = []
        for r in sorted(raw, key=lambda x: float(x.get("ts_before") or 0.0)):
            before = r.get("state_before") or {}
            after = r.get("state_after") or {}
            # context_id fields preferred
            cid = r.get("context_id") or before.get("context_id") or ""
            acid = after.get("context_id") or ""
            if not cid or not acid:
                continue
            episodes.append(
                {
                    "ts": float(r.get("ts_before") or 0.0),
                    "context_id": cid,
                    "actual_id": acid,
                    "actual_family": r.get("action_family") or "",
                    "prev_ids": list(hist[-2:]),
                }
            )
            hist.append(cid)
            hist.append(acid)

    n = len(episodes)
    if n < 4:
        return {
            "ok": False,
            "error": "need ≥4 labeled episodes",
            "n": n,
            "shadow_path": str(sh_path),
        }

    split = max(1, int(n * train_frac))
    train, test = episodes[:split], episodes[split:]
    if not test:
        train, test = episodes[:-1], episodes[-1:]

    vocab = {e["context_id"] for e in episodes} | {e["actual_id"] for e in episodes}

    # --- models ---
    global_counts: Counter[str] = Counter(e["actual_id"] for e in train)
    global_dist = _softmax_from_counts(global_counts, vocab)

    trans: dict[str, Counter[str]] = defaultdict(Counter)
    for e in train:
        trans[e["context_id"]][e["actual_id"]] += 1

    # recency: weight later train rows higher
    recency: dict[str, Counter[str]] = defaultdict(Counter)
    for i, e in enumerate(train):
        w = 1 + i / max(1, len(train))  # ~1..2
        # store as int by scaling
        recency[e["context_id"]][e["actual_id"]] += int(round(w * 10))

    bigram: dict[tuple[str, str], Counter[str]] = defaultdict(Counter)
    trigram: dict[tuple[str, str, str], Counter[str]] = defaultdict(Counter)
    for e in train:
        prev = e.get("prev_ids") or []
        if len(prev) >= 1:
            bigram[(prev[-1], e["context_id"])][e["actual_id"]] += 1
        if len(prev) >= 2:
            trigram[(prev[-2], prev[-1], e["context_id"])][e["actual_id"]] += 1

    def prior_fn(_ep: dict[str, Any]) -> dict[str, float]:
        return global_dist

    def transition_fn(ep: dict[str, Any]) -> dict[str, float]:
        c = trans.get(ep["context_id"]) or Counter()
        if not c:
            return global_dist
        return _softmax_from_counts(c, vocab)

    def recency_fn(ep: dict[str, Any]) -> dict[str, float]:
        c = recency.get(ep["context_id"]) or Counter()
        if not c:
            return transition_fn(ep)
        return _softmax_from_counts(c, vocab)

    def bigram_fn(ep: dict[str, Any]) -> dict[str, float]:
        prev = ep.get("prev_ids") or []
        if not prev:
            return transition_fn(ep)
        c = bigram.get((prev[-1], ep["context_id"])) or Counter()
        if not c:
            return transition_fn(ep)
        return _softmax_from_counts(c, vocab)

    def trigram_fn(ep: dict[str, Any]) -> dict[str, float]:
        prev = ep.get("prev_ids") or []
        if len(prev) < 2:
            return bigram_fn(ep)
        c = trigram.get((prev[-2], prev[-1], ep["context_id"])) or Counter()
        if not c:
            return bigram_fn(ep)
        return _softmax_from_counts(c, vocab)

    results = [
        _eval_model("global_prior", test, prior_fn),
        _eval_model("transition_table", test, transition_fn),
        _eval_model("recency_transition", test, recency_fn),
        _eval_model("bigram", test, bigram_fn),
        _eval_model("trigram", test, trigram_fn),
    ]
    # Rank by log_loss then top1
    ranked = sorted(
        results,
        key=lambda r: (r.get("log_loss", 9e9), -(r.get("top1") or 0)),
    )
    return {
        "ok": True,
        "schema": "flow_skeptic.v0",
        "horizon_ms": horizon_ms,
        "n_total": n,
        "n_train": len(train),
        "n_test": len(test),
        "train_frac": train_frac,
        "split": "time",
        "vocab_size": len(vocab),
        "models": results,
        "winner": ranked[0]["model"] if ranked else None,
        "note": "optimize precision@surface later; this is intent head only",
        "shadow_path": str(sh_path),
    }
