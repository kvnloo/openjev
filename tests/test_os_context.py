"""OS context import from workspace-copilot flow tables."""

from __future__ import annotations

import json
import sqlite3
from pathlib import Path

from z0int import os_context


def _seed_flow_db(path: Path) -> None:
    conn = sqlite3.connect(path)
    conn.executescript(
        """
        CREATE TABLE context_episodes (
            id INTEGER PRIMARY KEY,
            context_id TEXT NOT NULL,
            open_event_id INTEGER,
            close_event_id INTEGER,
            ts_before REAL NOT NULL,
            ts_after REAL,
            state_before_json TEXT NOT NULL,
            action_family TEXT NOT NULL DEFAULT '',
            action_target TEXT NOT NULL DEFAULT '',
            state_after_json TEXT NOT NULL DEFAULT '{}',
            horizon_ms REAL,
            closed INTEGER NOT NULL DEFAULT 0
        );
        CREATE TABLE shadow_predictions (
            id INTEGER PRIMARY KEY,
            pred_id TEXT NOT NULL UNIQUE,
            schema_name TEXT NOT NULL,
            ts REAL NOT NULL,
            trigger_event_id INTEGER,
            context_id TEXT NOT NULL,
            state_json TEXT NOT NULL,
            topk_json TEXT NOT NULL,
            latency_ms REAL NOT NULL,
            actual_context_id TEXT,
            actual_family TEXT,
            actual_target TEXT,
            ranked INTEGER,
            manual_equivalent INTEGER,
            matched_at REAL,
            horizon_ms REAL
        );
        """
    )
    before = {"app": "kitty", "workspace": "1"}
    after = {"app": "firefox", "workspace": "2"}
    conn.execute(
        """INSERT INTO context_episodes(
               context_id, ts_before, ts_after, state_before_json, action_family,
               action_target, state_after_json, horizon_ms, closed
           ) VALUES (?,?,?,?,?,?,?,?,1)""",
        (
            "aaa",
            100.0,
            101.0,
            json.dumps(before),
            "switch_app",
            "firefox",
            json.dumps(after),
            1000.0,
        ),
    )
    conn.execute(
        """INSERT INTO shadow_predictions(
               pred_id, schema_name, ts, context_id, state_json, topk_json, latency_ms,
               actual_context_id, actual_family, actual_target, ranked, manual_equivalent, matched_at, horizon_ms
           ) VALUES (?,?,?,?,?,?,?,?,?,?,?,?,?,?)""",
        (
            "p1",
            "os.next_context.v0",
            100.0,
            "aaa",
            json.dumps(before),
            json.dumps([{"context_id": "bbb", "p": 0.7, "family": "switch_app", "target": "firefox"}]),
            0.5,
            "bbb",
            "switch_app",
            "firefox",
            0,
            1,
            101.0,
            1000.0,
        ),
    )
    conn.commit()
    conn.close()


def test_import_from_db(tmp_path: Path, monkeypatch) -> None:
    db = tmp_path / "workspace.db"
    _seed_flow_db(db)
    root = tmp_path / "z0int-home"
    monkeypatch.setenv("Z0INT_HOME", str(root))
    out = os_context.import_from_db(db_path=db, root=root)
    assert out["ok"] is True
    assert out["n_episodes"] == 1
    assert out["n_shadow"] == 1
    assert out["top1_manual_equivalent"] == 1
    ep = (root / "episodes" / "os_next_context.jsonl").read_text(encoding="utf-8").strip()
    row = json.loads(ep)
    assert row["schema"] == "os.context_episode.v0"
    assert row["action_family"] == "switch_app"
    assert row["action_target"] == "firefox"


def test_stats_missing_tables(tmp_path: Path, monkeypatch) -> None:
    db = tmp_path / "empty.db"
    sqlite3.connect(db).close()
    monkeypatch.setenv("WORKSPACE_COPILOT_STATE_DIR", str(tmp_path))
    # empty dir → workspace.db missing path uses STATE_DIR/workspace.db
    db.write_text("")  # not used; create proper empty schema
    conn = sqlite3.connect(db)
    conn.execute("CREATE TABLE meta(key TEXT, value TEXT)")
    conn.commit()
    conn.close()
    # Point env at parent so workspace_copilot_db resolves to tmp_path/workspace.db
    # WORKSPACE_COPILOT_STATE_DIR is the state dir containing workspace.db
    monkeypatch.setenv("WORKSPACE_COPILOT_STATE_DIR", str(tmp_path))
    # rename: workspace_copilot_db uses STATE_DIR / workspace.db
    (tmp_path / "workspace.db").unlink(missing_ok=True)
    db.rename(tmp_path / "workspace.db")
    st = os_context.stats(root=tmp_path / "z")
    assert st["ok"] is False
    assert "flow tables missing" in st["error"]
