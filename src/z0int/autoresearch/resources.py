from __future__ import annotations

import os
import time
from dataclasses import dataclass
from typing import Any


@dataclass
class ResourceLimits:
    gpu_util_pause_above: float = 35.0
    gpu_memory_free_min_mb: float = 2500.0
    cpu_load_pause_above: float = 0.70
    interactive_grace_seconds: float = 15.0


DEFAULTS = ResourceLimits()


def _load_avg_ratio() -> float:
    try:
        load1, _, _ = os.getloadavg()
        n = os.cpu_count() or 1
        return float(load1) / float(n)
    except Exception:
        return 0.0


def _gpu_stats() -> tuple[float | None, float | None]:
    """Return (util_pct, free_mb) or (None, None)."""
    try:
        import subprocess

        out = subprocess.check_output(
            [
                "nvidia-smi",
                "--query-gpu=utilization.gpu,memory.free",
                "--format=csv,noheader,nounits",
            ],
            text=True,
            timeout=2,
        )
        line = out.strip().splitlines()[0]
        util_s, free_s = [x.strip() for x in line.split(",")]
        return float(util_s), float(free_s)
    except Exception:
        return None, None


def interactive_recent(grace: float | None = None) -> bool:
    """True if an interactive harness touch file is fresh."""
    from z0int import paths

    g = grace if grace is not None else DEFAULTS.interactive_grace_seconds
    marker = paths.home() / "runtime" / "interactive.touch"
    if not marker.is_file():
        # also bridge heart recent
        heart = paths.home() / "stream" / "bridge_heart.jsonl"
        if heart.is_file():
            age = time.time() - heart.stat().st_mtime
            return age < g
        return False
    return (time.time() - marker.stat().st_mtime) < g


def should_pause(limits: ResourceLimits | None = None) -> dict[str, Any]:
    lim = limits or DEFAULTS
    reasons: list[str] = []
    if interactive_recent(lim.interactive_grace_seconds):
        reasons.append("interactive_traffic")
    cpu = _load_avg_ratio()
    if cpu > lim.cpu_load_pause_above:
        reasons.append(f"cpu_load:{cpu:.2f}")
    util, free_mb = _gpu_stats()
    if util is not None and util > lim.gpu_util_pause_above:
        reasons.append(f"gpu_util:{util}")
    if free_mb is not None and free_mb < lim.gpu_memory_free_min_mb:
        reasons.append(f"gpu_mem_free:{free_mb}")
    return {
        "pause": bool(reasons),
        "reasons": reasons,
        "cpu_load_ratio": cpu,
        "gpu_util": util,
        "gpu_free_mb": free_mb,
    }
