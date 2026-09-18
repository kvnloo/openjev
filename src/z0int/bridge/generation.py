"""Active bridge generation pointer + stale-writer quarantine."""

from __future__ import annotations

import json
import os
import time
from pathlib import Path
from typing import Any

from z0int import paths
from z0int.bridge.protocol import BRIDGE_PROTOCOL


def runtime_dir(root: Path | None = None) -> Path:
    d = (root or paths.home()) / "runtime"
    d.mkdir(parents=True, exist_ok=True)
    return d


def current_path(root: Path | None = None) -> Path:
    return runtime_dir(root) / "bridge-current.json"


def quarantine_path(root: Path | None = None) -> Path:
    return (root or paths.home()) / "stream" / "bridge_quarantine.jsonl"


def read_current(root: Path | None = None) -> dict[str, Any] | None:
    p = current_path(root)
    if not p.is_file():
        return None
    try:
        return json.loads(p.read_text(encoding="utf-8"))
    except (OSError, json.JSONDecodeError):
        return None


def publish_current(
    *,
    generation: int,
    instance_id: str,
    build_id: str,
    root: Path | None = None,
    extra: dict[str, Any] | None = None,
) -> dict[str, Any]:
    blob = {
        "protocol": BRIDGE_PROTOCOL,
        "generation": int(generation),
        "instance_id": instance_id,
        "build_id": build_id,
        "activated_at": time.time(),
        "pid": os.getpid(),
    }
    if extra:
        blob.update(extra)
    path = current_path(root)
    tmp = path.with_suffix(".tmp")
    tmp.write_text(json.dumps(blob, indent=2, ensure_ascii=False) + "\n", encoding="utf-8")
    tmp.replace(path)
    return blob


def is_stale(writer_generation: int | None, root: Path | None = None) -> bool:
    """True if writer_generation is behind the published current generation."""
    if writer_generation is None:
        return False  # unknown: accept but stamp; soft
    cur = read_current(root)
    if not cur:
        return False
    try:
        current_g = int(cur.get("generation") or 0)
    except (TypeError, ValueError):
        return False
    return int(writer_generation) < current_g


def quarantine(row: dict[str, Any], *, reason: str, root: Path | None = None) -> None:
    path = quarantine_path(root)
    path.parent.mkdir(parents=True, exist_ok=True)
    out = dict(row)
    out["quarantine_reason"] = reason
    out["quarantine_ts"] = time.time()
    with path.open("a", encoding="utf-8") as fh:
        fh.write(json.dumps(out, ensure_ascii=False) + "\n")
