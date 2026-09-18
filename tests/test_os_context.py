"""OS context import from workspace-copilot flow tables."""

from __future__ import annotations

import json
import sqlite3
import time
from pathlib import Path

from z0int import flow_skeptic, os_context


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
            horizon_ms REAL,
            confidence REAL NOT NULL DEFAULT 0,
            margin REAL NOT NULL DEFAULT 0,
            entropy REAL NOT NULL DEFAULT 0,
            receipt_json TEXT NOT NULL DEFAULT '{}'
        );
        CREATE TABLE prediction_horizons (
            id INTEGER PRIMARY KEY,
            pred_id TEXT NOT NULL,
            horizon_ms INTEGER NOT NULL,
            opened_at REAL NOT NULL,
            closed_at REAL,
            actual_family TEXT,
            actual_target TEXT,
            actual_context_id TEXT,
            ranked INTEGER,
            manual_equivalent INTEGER,
            UNIQUE(pred_id, horizon_ms)
        );
        """
    )
    before = {"app": "kitty", "workspace": "1", "context_id": "ctx_a"}
    after = {"app": "firefox", "workspace": "2", "context_id": "ctx_b"}
    ts = time.time()
    conn.execute(
        """INSERT INTO context_episodes(
               context_id, ts_before, ts_after, state_before_json, action_family,
               action_target, state_after_json, horizon_ms, closed
           ) VALUES (?,?,?,?,?,?,?,?,1)""",
        (
            "ctx_a",
            ts - 2,
            ts - 1,
            json.dumps(before),
            "switch_app",
            "firefox",
            json.dumps(after),
            1000.0,
        ),
    )
    topk = [
        {"context_id": "ctx_b", "p": 0.7, "family": "switch_app", "target": "firefox"},
        {"context_id": "ctx_a", "p": 0.3, "family": "stay", "target": "kitty"},
    ]
    receipt = {
        "schema": "flow_prediction.v1",
        "prediction_id": "p1",
        "context_id": "ctx_a",
        "surface": {"arm": "withheld", "eligible": True},
    }
    conn.execute(
        """INSERT INTO shadow_predictions(
               pred_id, schema_name, ts, context_id, state_json, topk_json,
               latency_ms, actual_context_id, actual_family, actual_target,
               ranked, manual_equivalent, matched_at, horizon_ms,
               confidence, margin, entropy, receipt_json
           ) VALUES (?,?,?,?,?,?,?,?,?,?,?,?,?,?,?,?,?,?)""",
        (
            "p1",
            "os.next_context.v0",
            ts - 2,
            "ctx_a",
            json.dumps(before),
            json.dumps(topk),
            0.4,
            "ctx_b",
            "switch_app",
            "firefox",
            0,
            1,
            ts - 1,
            1000.0,
            0.7,
            0.4,
            0.6,
            json.dumps(receipt),
        ),
    )
    for h, fam in ((500, "noop"), (2000, "switch_app"), (10000, "switch_app"), (60000, "switch_app")):
        conn.execute(
            """INSERT INTO prediction_horizons(
                   pred_id, horizon_ms, opened_at, closed_at, actual_family,
                   actual_target, actual_context_id, ranked, manual_equivalent
               ) VALUES (?,?,?,?,?,?,?,?,?)""",
            (
                "p1",
                h,
                ts - 2,
                ts - 1.5 if fam == "noop" else ts - 1,
                fam,
                "" if fam == "noop" else "firefox",
                "ctx_a" if fam == "noop" else "ctx_b",
                1 if fam == "noop" else 0,
                0 if fam == "noop" else 1,
            ),
        )
    # Extra matched rows so skeptic has enough episodes
    for i in range(10):
        cid = f"c{i}"
        acid = f"c{(i + 1) % 10}"
        conn.execute(
            """INSERT INTO shadow_predictions(
                   pred_id, schema_name, ts, context_id, state_json, topk_json,
                   latency_ms, actual_context_id, actual_family, actual_target,
                   ranked, manual_equivalent, matched_at, horizon_ms, receipt_json
               ) VALUES (?,?,?,?,?,?,?,?,?,?,?,?,?,?,?)""",
            (
                f"px{i}",
                "os.next_context.v0",
                ts - 100 + i,
                cid,
                json.dumps({"app": cid}),
                json.dumps([{"context_id": acid, "p": 0.6}, {"context_id": cid, "p": 0.4}]),
                0.5,
                acid,
                "switch_app" if i % 3 else "noop",
                acid if i % 3 else "",
                0 if i % 2 == 0 else 1,
                1 if i % 2 == 0 else 0,
                ts - 99 + i,
                2000.0,
                "{}",
            ),
        )
        for h in (500, 2000, 10000, 60000):
            fam = "noop" if h == 500 and i % 3 == 0 else ("noop" if i % 3 == 0 else "switch_app")
            conn.execute(
                """INSERT INTO prediction_horizons(
                       pred_id, horizon_ms, opened_at, closed_at, actual_family,
                       actual_target, actual_context_id, ranked, manual_equivalent
                   ) VALUES (?,?,?,?,?,?,?,?,?)""",
                (
                    f"px{i}",
                    h,
                    ts - 100 + i,
                    ts - 99 + i,
                    fam,
                    "" if fam == "noop" else acid,
                    cid if fam == "noop" else acid,
                    0,
                    1 if fam != "noop" else 0,
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
    assert out["n_shadow"] >= 1
    assert out["n_horizons"] >= 4
    assert out["n_receipts"] >= 1
    assert out["top1_manual_equivalent"] >= 1
    ep = (root / "episodes" / "os_next_context.jsonl").read_text(encoding="utf-8").strip()
    row = json.loads(ep.splitlines()[0])
    assert row["schema"] == "os.context_episode.v0"
    assert row["action_family"] == "switch_app"
    assert row["action_target"] == "firefox"
    sh = json.loads((root / "episodes" / "os_next_context_shadow.jsonl").read_text().splitlines()[-1])
    # last written is first in DESC order — check any has horizons
    lines = (root / "episodes" / "os_next_context_shadow.jsonl").read_text().splitlines()
    assert any(json.loads(line).get("horizons") for line in lines)


def test_stats_missing_tables(tmp_path: Path, monkeypatch) -> None:
    monkeypatch.setenv("WORKSPACE_COPILOT_STATE_DIR", str(tmp_path))
    conn = sqlite3.connect(tmp_path / "workspace.db")
    conn.execute("CREATE TABLE meta(key TEXT, value TEXT)")
    conn.commit()
    conn.close()
    st = os_context.stats(root=tmp_path / "z")
    assert st["ok"] is False
    assert "flow tables missing" in st["error"]


def test_flow_skeptic(tmp_path: Path, monkeypatch) -> None:
    db = tmp_path / "workspace.db"
    _seed_flow_db(db)
    root = tmp_path / "z0int-home"
    monkeypatch.setenv("Z0INT_HOME", str(root))
    os_context.import_from_db(db_path=db, root=root)
    out = flow_skeptic.run_skeptic(root=root, horizon_ms=2000)
    assert out["ok"] is True
    assert out["n_total"] >= 4
    models = {m["model"] for m in out["models"]}
    assert "global_prior" in models
    assert "transition_table" in models
    assert out["winner"]
