#!/usr/bin/env python3
"""OMP/Hermes recovery controller backend. Delegates to evolution-lab.

One-shot:  python3 recovery.py '{"action":"plan",...}'
Resident:  python3 -u recovery.py --worker   # stdin JSONL -> stdout JSONL
"""

from __future__ import annotations

import json
import os
import sys
from pathlib import Path


def _repo() -> Path:
    env = os.environ.get("FLYFORGE_EVOLUTION_LAB")
    if env:
        return Path(env)
    return Path("/workspace/evolution-lab")


def _ensure_import_path() -> None:
    root = _repo()
    if not root.is_dir():
        raise RuntimeError(f"evolution-lab not found at {root}")
    text = str(root)
    if text not in sys.path:
        sys.path.insert(0, text)


def serve() -> int:
    _ensure_import_path()
    from evolution_lab.recovery_cli import handle_request
    from evolution_lab.schema import GenomeError

    for raw in sys.stdin:
        line = raw.strip()
        if not line:
            continue
        try:
            payload = json.loads(line)
            if not isinstance(payload, dict):
                raise ValueError("request must be a JSON object")
            result = handle_request(payload)
            sys.stdout.write(json.dumps({"ok": True, **result}, separators=(",", ":")) + "\n")
        except (json.JSONDecodeError, GenomeError, ValueError, RuntimeError) as exc:
            sys.stdout.write(json.dumps({"ok": False, "error": str(exc)}, separators=(",", ":")) + "\n")
        sys.stdout.flush()
    return 0


def main() -> int:
    _ensure_import_path()
    from evolution_lab.recovery_cli import main as recovery_main

    return recovery_main()


if __name__ == "__main__":
    if len(sys.argv) > 1 and sys.argv[1] == "--worker":
        raise SystemExit(serve())
    raw = sys.argv[1] if len(sys.argv) > 1 else "{}"
    try:
        json.loads(raw)
    except json.JSONDecodeError as exc:
        print(json.dumps({"ok": False, "error": f"invalid JSON request: {exc}"}))
        raise SystemExit(2)
    sys.argv = [sys.argv[0], raw]
    raise SystemExit(main())
