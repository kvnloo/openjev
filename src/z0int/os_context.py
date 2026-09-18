"""OS next-context episodes from workspace-copilot → z0int vault.

Ownership split:
  workspace-copilot owns sensors, episode join, shadow predict, prepare/commit UI
  z0int owns specialist training, receipts, Evolution Lab promotion

This module only imports already-sanitized semantic episodes/shadow rows.
It never reads raw keystrokes, titles, pane text, clipboard, or screenshots.
"""

from __future__ import annotations

import json
import os
import sqlite3
import subprocess
from pathlib import Path
from typing import Any

from . import paths

SCHEMA_EPISODE = "os.context_episode.v0"
SCHEMA_SHADOW = "os.next_context.v0"
SCHEMA_OPERATOR = "os.next_operator.v0"

OPERATOR_FAMILIES = (
    "inspect_result",
    "run_test",
    "open_context",
    "delegate",
    "retrieve",
    "resume_previous",
    "noop",
)


def workspace_copilot_db() -> Path:
    override = os.environ.get("WORKSPACE_COPILOT_STATE_DIR")
    if override:
        return Path(override).expanduser() / "workspace.db"
    state_home = Path(os.environ.get("XDG_STATE_HOME", Path.home() / ".local/state"))
    return state_home / "workspace-copilot" / "workspace.db"


def _connect_ro(db_path: Path) -> sqlite3.Connection:
    if not db_path.is_file():
        raise FileNotFoundError(f"workspace-copilot db missing: {db_path}")
    uri = f"file:{db_path}?mode=ro"
    conn = sqlite3.connect(uri, uri=True, timeout=5)
    conn.row_factory = sqlite3.Row
    return conn


def import_from_db(
    *,
    db_path: Path | None = None,
    root: Path | None = None,
    limit: int = 50000,
) -> dict[str, Any]:
    """Copy closed episodes + scored shadow preds into ~/.z0int/episodes."""
    db_path = db_path or workspace_copilot_db()
    layout = paths.ensure_layout(root)
    ep_dest = layout["episodes"] / "os_next_context.jsonl"
    sh_dest = layout["episodes"] / "os_next_context_shadow.jsonl"
    conn = _connect_ro(db_path)
    try:
        tables = {r[0] for r in conn.execute("SELECT name FROM sqlite_master WHERE type='table'")}
        if "context_episodes" not in tables or "shadow_predictions" not in tables:
            return {
                "ok": False,
                "error": "flow tables missing — upgrade workspace-copilot schema to v4",
                "db": str(db_path),
                "tables": sorted(tables),
            }
        n_ep = 0
        with ep_dest.open("w", encoding="utf-8") as fh:
            for row in conn.execute(
                """SELECT id, context_id, ts_before, ts_after, action_family, action_target,
                          state_before_json, state_after_json, horizon_ms
                   FROM context_episodes WHERE closed=1
                   ORDER BY id DESC LIMIT ?""",
                (limit,),
            ):
                try:
                    before = json.loads(row["state_before_json"])
                    after = json.loads(row["state_after_json"] or "{}")
                except json.JSONDecodeError:
                    continue
                rec = {
                    "schema": SCHEMA_EPISODE,
                    "episode_id": row["id"],
                    "context_id": row["context_id"],
                    "ts_before": row["ts_before"],
                    "ts_after": row["ts_after"],
                    "action_family": row["action_family"],
                    "action_target": row["action_target"],
                    "horizon_ms": row["horizon_ms"],
                    "state_before": before,
                    "state_after": after,
                    "source": "workspace-copilot",
                }
                fh.write(json.dumps(rec, separators=(",", ":"), sort_keys=True) + "\n")
                n_ep += 1

        n_sh = 0
        matched = 0
        top1 = 0
        with sh_dest.open("w", encoding="utf-8") as fh:
            for row in conn.execute(
                """SELECT pred_id, schema_name, ts, context_id, state_json, topk_json,
                          latency_ms, actual_context_id, actual_family, actual_target,
                          ranked, manual_equivalent, matched_at, horizon_ms
                   FROM shadow_predictions
                   ORDER BY id DESC LIMIT ?""",
                (limit,),
            ):
                try:
                    state = json.loads(row["state_json"])
                    topk = json.loads(row["topk_json"])
                except json.JSONDecodeError:
                    continue
                rec = {
                    "schema": row["schema_name"] or SCHEMA_SHADOW,
                    "pred_id": row["pred_id"],
                    "ts": row["ts"],
                    "context_id": row["context_id"],
                    "state": state,
                    "topk": topk,
                    "latency_ms": row["latency_ms"],
                    "actual_context_id": row["actual_context_id"],
                    "actual_family": row["actual_family"],
                    "actual_target": row["actual_target"],
                    "ranked": row["ranked"],
                    "manual_equivalent": row["manual_equivalent"],
                    "matched_at": row["matched_at"],
                    "horizon_ms": row["horizon_ms"],
                    "source": "workspace-copilot",
                }
                fh.write(json.dumps(rec, separators=(",", ":"), sort_keys=True) + "\n")
                n_sh += 1
                if row["matched_at"] is not None:
                    matched += 1
                    if row["manual_equivalent"] == 1:
                        top1 += 1
    finally:
        conn.close()

    manifest = {
        "schema": "z0int.os_context_import.v1",
        "db": str(db_path),
        "episodes_path": str(ep_dest),
        "shadow_path": str(sh_dest),
        "n_episodes": n_ep,
        "n_shadow": n_sh,
        "matched": matched,
        "top1_manual_equivalent": top1,
        "top1_rate": round(top1 / matched, 4) if matched else None,
        "operator_families": list(OPERATOR_FAMILIES),
        "operator_schema": SCHEMA_OPERATOR,
    }
    man_path = layout["episodes"] / "os_next_context_manifest.json"
    man_path.write_text(json.dumps(manifest, indent=2) + "\n", encoding="utf-8")
    manifest["manifest_path"] = str(man_path)
    manifest["ok"] = True
    return manifest


def import_via_cli(*, root: Path | None = None) -> dict[str, Any]:
    """Prefer workspace-copilot export-flow when the binary is on PATH."""
    layout = paths.ensure_layout(root)
    dest = layout["episodes"]
    exe = os.environ.get("WORKSPACE_COPILOT_BIN", "workspace-copilot")
    try:
        proc = subprocess.run(
            [exe, "--json", "export-flow", "--dest", str(dest)],
            text=True,
            capture_output=True,
            timeout=60,
            check=False,
        )
    except (FileNotFoundError, subprocess.TimeoutExpired) as exc:
        return import_from_db(root=root) | {"cli_error": str(exc), "fallback": "db"}
    if proc.returncode != 0:
        return import_from_db(root=root) | {
            "cli_error": (proc.stderr or proc.stdout)[:400],
            "fallback": "db",
        }
    try:
        payload = json.loads(proc.stdout)
    except json.JSONDecodeError:
        return import_from_db(root=root) | {"cli_error": "bad json", "fallback": "db"}
    # Normalize paths produced by export-flow into manifest.
    return import_from_db(root=root) | {"cli_export": payload, "via": "cli+db"}


def stats(*, root: Path | None = None) -> dict[str, Any]:
    layout = paths.ensure_layout(root)
    man = layout["episodes"] / "os_next_context_manifest.json"
    if man.is_file():
        try:
            return json.loads(man.read_text(encoding="utf-8"))
        except json.JSONDecodeError:
            pass
    db = workspace_copilot_db()
    if not db.is_file():
        return {"ok": False, "error": "no workspace-copilot db", "db": str(db)}
    try:
        conn = _connect_ro(db)
    except FileNotFoundError as exc:
        return {"ok": False, "error": str(exc)}
    try:
        tables = {r[0] for r in conn.execute("SELECT name FROM sqlite_master WHERE type='table'")}
        if "shadow_predictions" not in tables:
            return {"ok": False, "error": "flow tables missing", "db": str(db)}
        n_ep = conn.execute("SELECT COUNT(*) FROM context_episodes WHERE closed=1").fetchone()[0]
        n_sh = conn.execute("SELECT COUNT(*) FROM shadow_predictions").fetchone()[0]
        matched = conn.execute(
            "SELECT COUNT(*) FROM shadow_predictions WHERE matched_at IS NOT NULL"
        ).fetchone()[0]
        top1 = conn.execute(
            "SELECT COUNT(*) FROM shadow_predictions WHERE manual_equivalent=1"
        ).fetchone()[0]
        lat = conn.execute(
            "SELECT AVG(latency_ms), MAX(latency_ms) FROM shadow_predictions"
        ).fetchone()
        return {
            "ok": True,
            "db": str(db),
            "closed_episodes": n_ep,
            "shadow_predictions": n_sh,
            "matched": matched,
            "top1_manual_equivalent": top1,
            "top1_rate": round(top1 / matched, 4) if matched else None,
            "latency_ms_avg": round(float(lat[0] or 0.0), 3),
            "latency_ms_max": round(float(lat[1] or 0.0), 3),
            "schema": SCHEMA_SHADOW,
            "operator_schema": SCHEMA_OPERATOR,
            "operator_families": list(OPERATOR_FAMILIES),
        }
    finally:
        conn.close()
