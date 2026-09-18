from __future__ import annotations

from dataclasses import asdict, dataclass, field
from typing import Any, Literal

EXPERIMENT_SCHEMA = "z0int.experiment_contract.v1"
REPLAY_SCHEMA = "z0int.replay_result.v1"
MutationAxis = Literal["context_policy"]


@dataclass
class ExperimentContract:
    experiment_id: str
    task_snapshot_ids: list[str]
    hypothesis: str
    control_treatment_hash: str
    challenger_treatment_hash: str
    mutation_axis: MutationAxis = "context_policy"
    verifier: dict[str, str] = field(default_factory=dict)
    primary_metric: str = "time_to_verified_completion_ms"
    quality_margin: float = 0.0
    budgets: dict[str, float] = field(default_factory=lambda: {"max_wall_seconds": 600, "max_gpu_seconds": 300})
    schema: str = EXPERIMENT_SCHEMA

    def to_dict(self) -> dict[str, Any]:
        return asdict(self)


@dataclass
class ReplayResult:
    experiment_id: str
    task_snapshot_id: str
    arm: Literal["champion", "challenger", "A0", "B0", "A1", "B1", "C"]
    treatment_hash: str
    execution_completed: bool
    verified_success: bool | None
    verifier_id: str
    wall_ms: int
    frontier_tokens: int = 0
    gpu_ms: int = 0
    cpu_ms: int = 0
    schema: str = REPLAY_SCHEMA

    def to_dict(self) -> dict[str, Any]:
        return asdict(self)
