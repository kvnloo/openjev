from __future__ import annotations

import json
from pathlib import Path

from z0int import paths


def root() -> Path:
    paths.ensure_layout()
    r = paths.home() / "autoresearch"
    r.mkdir(parents=True, exist_ok=True)
    (r / "champions").mkdir(parents=True, exist_ok=True)
    return r


def queue_db() -> Path:
    return root() / "queue.sqlite3"


def results_path() -> Path:
    return root() / "results.jsonl"


def control_path() -> Path:
    return root() / "control.json"


def champions_dir() -> Path:
    return root() / "champions"


def append_result(row: dict) -> None:
    p = results_path()
    with p.open("a", encoding="utf-8") as f:
        f.write(json.dumps(row, default=str) + "\n")


def read_control() -> dict:
    p = control_path()
    if not p.is_file():
        return {"paused": False}
    try:
        return json.loads(p.read_text(encoding="utf-8"))
    except Exception:
        return {"paused": False}


def write_control(data: dict) -> None:
    control_path().write_text(json.dumps(data, indent=2) + "\n", encoding="utf-8")
