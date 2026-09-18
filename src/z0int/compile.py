"""Compile Hermes tool traces into local next-action episodes.

Writes ~/.z0int/episodes/ (gitignored). Never commit raw history.
"""

from __future__ import annotations

import argparse
import json
import re
import sqlite3
from collections import Counter
from pathlib import Path

from .families import FAMILIES, family_of

SECRET_RE = re.compile(
    r"(api[_-]?key|sk-[A-Za-z0-9]|password|secret|bearer\s|BEGIN (RSA |OPENSSH )?PRIVATE)",
    re.I,
)
DEFAULT_DB = Path("/workspace/hermes-home/state.db")
DEFAULT_OUT = Path.home() / ".z0int" / "episodes"


def _sanitize(text: str, limit: int = 400) -> str:
    t = (text or "").replace("\x00", " ")
    if SECRET_RE.search(t):
        return ""
    return t[:limit]


def compile_hermes(db: Path, out_dir: Path, *, limit: int | None = None) -> dict:
    if not db.is_file():
        raise SystemExit(f"missing Hermes state.db: {db}")
    out_dir.mkdir(parents=True, exist_ok=True)
    dest = out_dir / "next_action.jsonl"
    con = sqlite3.connect(f"file:{db}?mode=ro", uri=True)
    rows = con.execute(
        """
        select session_id, role, content, tool_name, timestamp
        from messages
        where role in ('user', 'tool')
        order by timestamp
        """
    )
    last_user: dict[str, str] = {}
    recent: dict[str, list[str]] = {}
    counts: Counter[str] = Counter()
    n = 0
    gold = 0
    with dest.open("w", encoding="utf-8") as handle:
        for session_id, role, content, tool_name, ts in rows:
            if role == "user":
                last_user[session_id] = _sanitize(content or "")
                continue
            fam = family_of(tool_name)
            prev = recent.setdefault(session_id, [])[-5:]
            success = None
            if content and content.lstrip().startswith("{"):
                try:
                    blob = json.loads(content)
                    if isinstance(blob, dict) and "success" in blob:
                        success = bool(blob["success"])
                except json.JSONDecodeError:
                    success = None
            rec = {
                "ts": float(ts or 0),
                "session": session_id,
                "user": last_user.get(session_id, ""),
                "prev": prev,
                "tool": tool_name,
                "family": fam,
                "tier": "gold" if success is True else ("fail" if success is False else "silver"),
            }
            handle.write(json.dumps(rec, ensure_ascii=False) + "\n")
            counts[fam] += 1
            if rec["tier"] == "gold":
                gold += 1
            prev.append(fam)
            recent[session_id] = prev[-8:]
            n += 1
            if limit and n >= limit:
                break
    manifest = {
        "source": str(db),
        "n": n,
        "gold": gold,
        "families": dict(counts),
        "path": str(dest),
        "note": "local vault only; not for public git",
    }
    (out_dir / "manifest.json").write_text(json.dumps(manifest, indent=2) + "\n", encoding="utf-8")
    return manifest


def main(argv: list[str] | None = None) -> int:
    p = argparse.ArgumentParser(prog="z0int-compile")
    p.add_argument("--db", type=Path, default=DEFAULT_DB)
    p.add_argument("--out", type=Path, default=DEFAULT_OUT)
    p.add_argument("--limit", type=int, default=None)
    args = p.parse_args(argv)
    print(json.dumps(compile_hermes(args.db, args.out, limit=args.limit), indent=2))
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
