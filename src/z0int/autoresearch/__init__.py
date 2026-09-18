"""Verified Trajectory Superoptimizer (autoresearchd).

V0 mutation axis: context acquisition / minimization only.
Only verified_success=true trajectories are eligible.
"""

from .daemon import pause, resume, run_daemon, run_once, status
from .queue import enqueue_trace, list_queue
from .schema import ExperimentContract, ReplayResult

__all__ = [
    "ExperimentContract",
    "ReplayResult",
    "enqueue_trace",
    "list_queue",
    "run_once",
    "run_daemon",
    "pause",
    "resume",
    "status",
]
