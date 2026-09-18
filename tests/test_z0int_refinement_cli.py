from __future__ import annotations

import json
import os
import subprocess
import sys
import time
from dataclasses import asdict
from pathlib import Path

from test_z0int_refinement import CAP, cascade, cohort, config, parent
from z0int.routines import RoutineRegistry

ROOT = Path(__file__).resolve().parents[1]


def invoke(*args):
    return subprocess.run([sys.executable, "-m", "z0int.refinement", *map(str, args)], cwd=ROOT,
                          env={**os.environ, "PYTHONPATH": str(ROOT / "src")}, capture_output=True, text=True)


def write_json(path, value):
    path.write_text(json.dumps(value))


def fresh_rows(phase):
    rows = cohort(phase, 0, 96)
    now = time.time()
    for row in rows:
        for field in ("session_started_at", "features_at", "decision_at", "observed_at"):
            row[field] = now
    return rows


def write_rows(path, rows):
    path.write_text("".join(json.dumps(row) + "\n" for row in rows))


def test_complete_cli_lifecycle(tmp_path):
    write_json(tmp_path / "parent.json", parent().to_dict())
    write_json(tmp_path / "config.json", asdict(config()))
    write_json(tmp_path / "cascade.json", cascade().to_dict())
    write_rows(tmp_path / "train.jsonl", cohort("train", 100))
    write_rows(tmp_path / "dev.jsonl", cohort("dev", 200))
    p = tmp_path / "proposal.json"
    args = ["propose", "--parent", tmp_path / "parent.json", "--config", tmp_path / "config.json",
            "--train", tmp_path / "train.jsonl", "--dev", tmp_path / "dev.jsonl", "--output", p]
    result = invoke(*args)
    assert result.returncode == 0, result.stderr + result.stdout
    original = p.read_bytes()
    assert invoke(*args).returncode == 2
    assert p.read_bytes() == original  # create-only output
    store = tmp_path / "trial.sqlite3"
    for phase in ("sealed", "future"):
        rows_path = tmp_path / f"{phase}.jsonl"
        write_rows(rows_path, fresh_rows(phase))
        credit = tmp_path / f"{phase}-credit.json"
        result = invoke("score", "--proposal", p, "--input", rows_path, "--phase", phase,
                        "--store", store, "--output", credit)
        assert result.returncode == 0, result.stdout + result.stderr
        assert json.loads(credit.read_text())["passed"]
    registry = tmp_path / "registry.jsonl"
    RoutineRegistry([parent()]).to_jsonl(registry)
    out = tmp_path / "new-registry.jsonl"
    args = ["activate", "--proposal", p, "--registry", registry, "--store", store, "--output", out]
    assert invoke(*args).returncode == 2  # approval is not inferred
    assert not out.exists()
    assert invoke(*args, "--approve").returncode == 0
    assert RoutineRegistry.from_jsonl(out).decide(CAP, {"prev_family": "EDIT", "tool_ok": True, "phase": "batch"}).matched
    shadow = tmp_path / "shadow.json"
    assert invoke("shadow-plan", "--proposal", p, "--cascade", tmp_path / "cascade.json", "--output", shadow).returncode == 0
    assert not json.loads(shadow.read_text())["plan"]["deployment"]["trafficEligible"]
    rollback = tmp_path / "rollback.jsonl"
    assert invoke("rollback", "--proposal-id", json.loads(p.read_text())["proposal_id"],
                  "--registry", out, "--output", rollback).returncode == 0
    assert all(c.status == "demoted" for c in RoutineRegistry.from_jsonl(rollback).candidates)


def test_invalid_cli_input_does_not_echo_private_text(tmp_path):
    private = "SECRET-CONTENT-MUST-NOT-APPEAR"
    (tmp_path / "proposal.json").write_text(private)
    result = invoke("shadow-plan", "--proposal", tmp_path / "proposal.json", "--cascade", tmp_path / "missing.json", "--output", tmp_path / "out.json")
    assert result.returncode == 2
    assert private not in result.stdout + result.stderr
    assert not (tmp_path / "out.json").exists()


def test_demo_reproduces_repair_and_rollback(tmp_path):
    dest = tmp_path / "demo"
    result = subprocess.run([sys.executable, str(ROOT / "examples/refinement/demo.py"), "--output", str(dest)],
                            cwd=ROOT, env={**os.environ, "PYTHONPATH": str(ROOT / "src")}, capture_output=True, text=True)
    assert result.returncode == 0, result.stdout + result.stderr
    report = json.loads((dest / "report.json").read_text())
    assert report["replay_after_demotion"]["fallback_calls"] == 384
    assert report["replay_after_repair"]["fallback_calls"] == 192
    assert report["replay_after_rollback"]["fallback_calls"] == 384
    assert report["real_token_savings"] is None
    assert not report["production_traffic_enabled"]
