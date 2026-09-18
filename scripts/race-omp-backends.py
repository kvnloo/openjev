#!/usr/bin/env python3
"""Time TypeSafe Jev, FlyForge recovery, and vLLM on one decision — parallel lanes."""

from __future__ import annotations
from concurrent.futures import ThreadPoolExecutor

import json
import os
import statistics
import subprocess
import threading
import time
import urllib.request
from pathlib import Path

N = int(os.environ.get("RACE_N", "5"))
ENV_PATH = Path.home() / ".omp" / ".env"
FLY = Path.home() / ".omp" / "agent" / "extensions" / "flyforge-recovery" / "recovery.py"
VLLM = os.environ.get("VLLM_UPSTREAM", "http://127.0.0.1:8000").rstrip("/")
TYPESAFE = os.environ.get("TYPESAFE_BASE_URL", "https://api.typesafe.ai").rstrip("/")
MODEL = os.environ.get("TYPESAFE_DEFAULT_MODEL", "jev-latest")


def load_env(path: Path) -> None:
    if not path.is_file():
        return
    for raw in path.read_text().splitlines():
        line = raw.strip()
        if not line or line.startswith("#") or "=" not in line:
            continue
        key, _, value = line.partition("=")
        os.environ.setdefault(key.strip(), value.strip().strip('"').strip("'"))


def timed(fn):
    started = time.perf_counter()
    try:
        payload = fn()
        return {"ok": True, "ms": (time.perf_counter() - started) * 1000, "detail": payload}
    except Exception as exc:  # noqa: BLE001 — race must finish
        return {"ok": False, "ms": (time.perf_counter() - started) * 1000, "error": f"{type(exc).__name__}: {exc}"}


def post(url: str, body: dict, headers: dict, timeout: float) -> dict:
    req = urllib.request.Request(
        url,
        data=json.dumps(body).encode(),
        headers={"content-type": "application/json", **headers},
        method="POST",
    )
    with urllib.request.urlopen(req, timeout=timeout) as response:
        return json.loads(response.read().decode())


def jev_lane() -> dict:
    key = os.environ.get("TYPESAFE_API_KEY", "")
    if not key:
        raise RuntimeError("TYPESAFE_API_KEY missing")
    data = post(
        f"{TYPESAFE}/v1/systemone",
        {
            "model": MODEL,
            "state": {
                "evidence": "The deployment completed at 14:02 UTC. Health checks passed in all three zones."
            },
            "questions": {
                "succeeded": {
                    "type": "noul",
                    "instructions": "Did the deployment succeed?",
                },
                "queue": {
                    "type": "choice",
                    "instructions": "Which queue should handle a missing password-reset email?",
                    "criteria": {
                        "account_access": "Account access and authentication",
                        "billing": "Billing and payment",
                        "sales": "Sales and evaluation",
                    },
                },
            },
        },
        {"Authorization": f"Bearer {key}"},
        10,
    )
    answers = data.get("answers") or {}
    return {"keys": sorted(answers), "model": data.get("model")}


_fly_lock = threading.Lock()
_fly_proc: subprocess.Popen[str] | None = None


def _fly_worker() -> subprocess.Popen[str]:
    global _fly_proc
    if _fly_proc is None or _fly_proc.poll() is not None:
        _fly_proc = subprocess.Popen(
            ["/usr/bin/python3", "-u", str(FLY), "--worker"],
            stdin=subprocess.PIPE,
            stdout=subprocess.PIPE,
            stderr=subprocess.PIPE,
            text=True,
        )
    return _fly_proc


def fly_lane() -> dict:
    payload = json.dumps(
        {
            "action": "plan",
            "harness": "omp",
            "family": "local_plasticity",
            "event": {
                "kind": "tool_error",
                "tool": "bash",
                "message": "command failed: nvidia-smi: connection refused",
                "is_error": True,
            },
            "fields": {"retry": 1, "transient": 1, "sandbox_alive": 1},
        }
    )
    with _fly_lock:
        proc = _fly_worker()
        if proc.stdin is None or proc.stdout is None:
            raise RuntimeError("fly worker pipes missing")
        proc.stdin.write(payload + "\n")
        proc.stdin.flush()
        line = proc.stdout.readline()
    if not line:
        raise RuntimeError("fly worker closed stdout")
    data = json.loads(line)
    if not data.get("ok"):
        raise RuntimeError(data.get("error") or "fly worker failed")
    return {"ok": data.get("ok"), "action": data.get("action")}


def vllm_lane() -> dict:
    data = post(
        f"{VLLM}/v1/chat/completions",
        {
            "model": os.environ.get("VLLM_MODEL", "Qwen/Qwen2.5-0.5B"),
            "messages": [
                {
                    "role": "system",
                    "content": "Answer with one letter only.\nQuestion: Did the deployment succeed?\n  A: yes\n  B: no",
                },
                {"role": "user", "content": "health checks passed in all zones"},
            ],
            "max_tokens": 8,
            "temperature": 0,
        },
        {},
        30,
    )
    choice = ((data.get("choices") or [{}])[0].get("message") or {}).get("content")
    return {"id": data.get("id"), "model": data.get("model"), "content": choice}


def summarize(name: str, runs: list[dict]) -> dict:
    ok = [row["ms"] for row in runs if row["ok"]]
    bad = [row for row in runs if not row["ok"]]
    return {
        "backend": name,
        "n": len(runs),
        "ok": len(ok),
        "fail": len(bad),
        "p50_ms": round(statistics.median(ok), 1) if ok else None,
        "mean_ms": round(statistics.mean(ok), 1) if ok else None,
        "min_ms": round(min(ok), 1) if ok else None,
        "max_ms": round(max(ok), 1) if ok else None,
        "error": bad[0].get("error") if bad else None,
    }


def main() -> int:
    load_env(ENV_PATH)
    print(f"race n={N}  jev={TYPESAFE}  vllm={VLLM}  fly={FLY}")
    rounds: list[dict[str, dict]] = []
    for index in range(N):
        with ThreadPoolExecutor(max_workers=3) as pool:
            futures = {
                "jev": pool.submit(timed, jev_lane),
                "fly": pool.submit(timed, fly_lane),
                "vllm": pool.submit(timed, vllm_lane),
            }
            row = {name: future.result() for name, future in futures.items()}
        rounds.append(row)
        status = " ".join(
            f"{name}={'ok' if row[name]['ok'] else 'FAIL'} {row[name]['ms']:.0f}ms" for name in ("jev", "fly", "vllm")
        )
        print(f"  round {index + 1}: {status}")

    report = {
        "n": N,
        "lanes": [
            summarize("jev", [row["jev"] for row in rounds]),
            summarize("fly", [row["fly"] for row in rounds]),
            summarize("vllm", [row["vllm"] for row in rounds]),
        ],
    }
    ranked = sorted((lane for lane in report["lanes"] if lane["ok"]), key=lambda lane: lane["p50_ms"] or 1e9)
    report["fastest_ok"] = ranked[0]["backend"] if ranked else None
    print(json.dumps(report, indent=2))
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
