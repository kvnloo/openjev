"""Counterfactual capability cartography V0.

Mine historical Grok/reference OMP turns into private task snapshots so cheap
candidates can be replayed against stored reference outcomes — zero new Grok
tokens for the first training signal.

Private data stays under ``~/.z0int/replay/`` (never public git).
"""

from __future__ import annotations

import hashlib
import json
import os
import time
from collections import Counter
from pathlib import Path
from typing import Any

from . import paths
from .receipt import treatment_hash

SCHEMA_SNAPSHOT = "z0int.task_snapshot.v1"
SCHEMA_MINE = "z0int.counterfactual_mine.v1"
SNAPSHOTS_NAME = "task_snapshots.jsonl"
MINE_META = "counterfactual_mine.json"

# Providers/models treated as scarce reference instruments by default.
DEFAULT_REF_SUBSTR = ("xai", "grok")


def _text_from_content(content: Any) -> str:
    if content is None:
        return ""
    if isinstance(content, str):
        return content
    if isinstance(content, list):
        parts: list[str] = []
        for c in content:
            if isinstance(c, dict):
                if c.get("type") in ("text", "input_text", "output_text") and c.get("text"):
                    parts.append(str(c["text"]))
                elif isinstance(c.get("content"), str):
                    parts.append(c["content"])
            elif isinstance(c, str):
                parts.append(c)
        return "\n".join(parts)
    return str(content)


def _prompt_hash(text: str) -> str:
    return hashlib.sha256(text.encode("utf-8", errors="replace")).hexdigest()[:16]


def _is_reference(provider: str | None, model: str | None, substrs: tuple[str, ...]) -> bool:
    blob = f"{provider or ''}/{model or ''}".lower()
    return any(s.strip().lower() in blob for s in substrs if s.strip())


def grade_snapshot(
    *,
    has_prompt: bool,
    has_output: bool,
    has_model: bool,
    has_usage: bool,
    has_env: bool,
    has_verifier: bool,
) -> str:
    """A–D replayability grade (see research brief)."""
    if has_prompt and has_output and has_model and has_env and has_verifier:
        return "A"
    if has_prompt and has_output and has_model and (has_usage or has_env):
        return "B"
    if has_prompt and has_output:
        return "C"
    return "D"


def default_sessions_root() -> Path | None:
    env = os.environ.get("OMP_SESSIONS_ROOT")
    if env:
        p = Path(env).expanduser()
        if p.is_dir():
            return p
    candidates = [
        Path.home() / ".omp" / "agent" / "sessions",
        Path("/workspace/kvn-home/.omp/agent/sessions"),
    ]
    for c in candidates:
        try:
            c = c.resolve()
        except OSError:
            continue
        if c.is_dir():
            return c
    return None


def _iter_session_jsonl(root: Path):
    # cwd buckets then session jsonl files
    for path in sorted(root.rglob("*.jsonl"), key=lambda p: p.stat().st_mtime, reverse=True):
        # skip huge non-session dumps if any
        if path.name.startswith("."):
            continue
        yield path


def mine_omp_sessions(
    *,
    sessions_root: Path | None = None,
    root: Path | None = None,
    limit: int = 5000,
    provider_substr: str = "xai,grok",
) -> dict[str, Any]:
    """Scan OMP session jsonl; freeze reference (Grok) turns as task snapshots."""
    layout = paths.ensure_layout(root)
    replay_dir = layout["replay"]
    out_path = replay_dir / SNAPSHOTS_NAME
    substrs = tuple(s.strip() for s in provider_substr.split(",") if s.strip()) or DEFAULT_REF_SUBSTR

    sess_root = sessions_root or default_sessions_root()
    if sess_root is None:
        return {
            "schema": SCHEMA_MINE,
            "ok": False,
            "error": "no_sessions_root",
            "n_snapshots": 0,
        }

    n_scanned = 0
    n_ref = 0
    grades: Counter[str] = Counter()
    providers: Counter[str] = Counter()
    written = 0
    # append mode; de-dupe by task_snapshot_id
    existing: set[str] = set()
    if out_path.is_file():
        for line in out_path.read_text(encoding="utf-8").splitlines():
            try:
                row = json.loads(line)
            except json.JSONDecodeError:
                continue
            tid = row.get("task_snapshot_id")
            if tid:
                existing.add(str(tid))

    with out_path.open("a", encoding="utf-8") as out:
        for path in _iter_session_jsonl(sess_root):
            if n_scanned >= limit:
                break
            session_id = None
            cwd = None
            model_session = None
            last_user: dict[str, Any] | None = None
            try:
                fh = path.open(encoding="utf-8", errors="replace")
            except OSError:
                continue
            with fh:
                for line in fh:
                    if n_scanned >= limit:
                        break
                    line = line.strip()
                    if not line:
                        continue
                    try:
                        ev = json.loads(line)
                    except json.JSONDecodeError:
                        continue
                    et = ev.get("type")
                    if et == "session":
                        session_id = ev.get("id") or session_id
                        cwd = ev.get("cwd") or cwd
                        continue
                    if et == "model_change":
                        model_session = ev.get("model") or model_session
                        continue
                    if et != "message":
                        continue
                    msg = ev.get("message") if isinstance(ev.get("message"), dict) else None
                    if not msg:
                        continue
                    role = msg.get("role")
                    if role == "user":
                        text = _text_from_content(msg.get("content"))
                        # skip pure system/tag walls
                        if not text or text.lstrip().startswith("<"):
                            # still keep short user intents
                            if "z0int" not in text.lower() and len(text) > 2000:
                                last_user = None
                                continue
                        last_user = {
                            "text": text,
                            "ts": msg.get("timestamp") or ev.get("timestamp"),
                            "hash": _prompt_hash(text),
                        }
                        continue
                    if role != "assistant":
                        continue
                    n_scanned += 1
                    provider = msg.get("provider")
                    model = msg.get("model") or model_session
                    if not _is_reference(
                        str(provider) if provider else None,
                        str(model) if model else None,
                        substrs,
                    ):
                        continue
                    n_ref += 1
                    usage = msg.get("usage") if isinstance(msg.get("usage"), dict) else {}
                    out_text = _text_from_content(msg.get("content"))
                    # Prefer text-only excerpt for privacy-ish store; keep hash of full
                    out_hash = _prompt_hash(out_text)
                    user_text = (last_user or {}).get("text") or ""
                    user_hash = (last_user or {}).get("hash") or _prompt_hash("")
                    has_prompt = bool(user_text.strip())
                    has_output = bool(out_text.strip())
                    has_model = bool(model)
                    has_usage = bool(usage)
                    has_env = bool(cwd or session_id)
                    # V0: no executable verifier join yet → max grade B
                    has_verifier = False
                    grade = grade_snapshot(
                        has_prompt=has_prompt,
                        has_output=has_output,
                        has_model=has_model,
                        has_usage=has_usage,
                        has_env=has_env,
                        has_verifier=has_verifier,
                    )
                    grades[grade] += 1
                    prov_key = f"{provider}/{model}"
                    providers[prov_key] += 1
                    th = treatment_hash(model_version=str(model) if model else None)
                    snap_id = hashlib.sha256(
                        f"{session_id}|{user_hash}|{out_hash}|{model}".encode()
                    ).hexdigest()[:24]
                    if snap_id in existing:
                        continue
                    existing.add(snap_id)
                    row = {
                        "schema": SCHEMA_SNAPSHOT,
                        "ts": time.time(),
                        "task_snapshot_id": snap_id,
                        "arm_id": "reference",
                        "selection_policy": "historical_replay",
                        "replay_grade": grade,
                        "session_id": session_id,
                        "cwd": cwd,
                        "source_path": str(path),
                        "provider": provider,
                        "model": model,
                        "api": msg.get("api"),
                        "treatment_hash": th,
                        "prompt_hash": user_hash,
                        "output_hash": out_hash,
                        # Private local fields — truncated; full text stays off public git
                        "prompt_preview": user_text[:400],
                        "output_preview": out_text[:400],
                        "usage": {
                            "input": usage.get("input"),
                            "output": usage.get("output"),
                            "cacheRead": usage.get("cacheRead"),
                            "totalTokens": usage.get("totalTokens"),
                            "cost_total": (usage.get("cost") or {}).get("total")
                            if isinstance(usage.get("cost"), dict)
                            else None,
                        },
                        "stop_reason": msg.get("stopReason"),
                        "user_ts": (last_user or {}).get("ts"),
                        "assistant_ts": msg.get("timestamp") or ev.get("timestamp"),
                        "reference_requested": True,
                        "reason_for_reference": "historical_grok_corpus",
                    }
                    out.write(json.dumps(row, ensure_ascii=False, default=str) + "\n")
                    written += 1

    meta = {
        "schema": SCHEMA_MINE,
        "ok": True,
        "ts": time.time(),
        "sessions_root": str(sess_root),
        "snapshots_path": str(out_path),
        "n_scanned_assistant": n_scanned,
        "n_reference_hits": n_ref,
        "n_written": written,
        "n_unique_total": len(existing),
        "grades": dict(grades),
        "providers": dict(providers),
        "note": (
            "V0 historical mine only. Candidate replay + non-inferiority gates next. "
            "Grades capped at B until verifier join exists. Private under ~/.z0int/replay."
        ),
    }
    (replay_dir / MINE_META).write_text(json.dumps(meta, indent=2) + "\n", encoding="utf-8")
    return meta


def summarize_replay(*, root: Path | None = None) -> dict[str, Any]:
    layout = paths.ensure_layout(root)
    path = layout["replay"] / SNAPSHOTS_NAME
    grades: Counter[str] = Counter()
    providers: Counter[str] = Counter()
    n = 0
    tokens_in = 0
    tokens_out = 0
    if path.is_file():
        for line in path.read_text(encoding="utf-8").splitlines():
            try:
                row = json.loads(line)
            except json.JSONDecodeError:
                continue
            n += 1
            grades[str(row.get("replay_grade") or "?")] += 1
            providers[f"{row.get('provider')}/{row.get('model')}"] += 1
            u = row.get("usage") or {}
            try:
                tokens_in += int(u.get("input") or 0)
                tokens_out += int(u.get("output") or 0)
            except (TypeError, ValueError):
                pass
    meta_path = layout["replay"] / MINE_META
    meta = None
    if meta_path.is_file():
        try:
            meta = json.loads(meta_path.read_text(encoding="utf-8"))
        except json.JSONDecodeError:
            meta = None
    return {
        "schema": "z0int.counterfactual_summary.v1",
        "n_snapshots": n,
        "grades": dict(grades),
        "providers": dict(providers),
        "reference_input_tokens_sum": tokens_in,
        "reference_output_tokens_sum": tokens_out,
        "snapshots_path": str(path),
        "last_mine": meta,
        "next": [
            "Run cheap candidate on grade-B+ snapshots with frozen prompt_hash",
            "Join executable verifier → promote grade A",
            "Non-inferiority gate before safe-offload threshold",
            "Live Grok only for VoI + 15-20% stratified audit",
        ],
    }


def cmd_mine(*, args: Any, as_json: bool) -> int:
    root = None
    sessions = Path(args.sessions_root).expanduser() if getattr(args, "sessions_root", None) else None
    rep = mine_omp_sessions(
        sessions_root=sessions,
        root=root,
        limit=int(getattr(args, "limit", 5000) or 5000),
        provider_substr=str(getattr(args, "provider_substr", "xai,grok") or "xai,grok"),
    )
    print(json.dumps(rep, indent=2, default=str))
    return 0 if rep.get("ok") else 1


def cmd_summary(*, args: Any, as_json: bool) -> int:
    rep = summarize_replay()
    print(json.dumps(rep, indent=2, default=str))
    return 0
