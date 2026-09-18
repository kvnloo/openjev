"""Authorized verified-loop task family with worktree + checkpoint resume.

Family: ``coding.bounded_worktree_patch``

Reference shape of live context-recovery:
  resolve requirements → bounded reversible patch in isolated worktree →
  independent verifier → durable checkpoint (restart-safe).

Does **not**:
  - flip z0int-bridge execution live
  - set verified_success from execution_completed alone
  - auto-merge or push
"""

from __future__ import annotations

import hashlib
import json
import os
import shutil
import subprocess
import time
import uuid
from dataclasses import asdict, dataclass, field
from pathlib import Path
from typing import Any, Literal

from . import paths
from .aodl import (
    AodlBindingConfig,
    AodlBudgets,
    assert_basic_aodl_invariants,
    compile_aodl,
)
from .cascade import CascadePolicy, StagePolicy
from .context_resolve import (
    ContextPacket,
    InformationNeed,
    attach_context_to_aodl,
    resolve_context,
)

FAMILY_ID = "coding.bounded_worktree_patch"
CHECKPOINT_SCHEMA = "z0int.task_checkpoint.v1"
CAPABILITY_ID = FAMILY_ID

TaskStatus = Literal[
    "authorized",
    "resolved",
    "worktree_ready",
    "patched",
    "execution_completed",
    "verified",
    "failed",
    "abandoned",
]


@dataclass
class PatchSpec:
    """Bounded, reversible file edit. Worker claim is not proof."""

    relative_path: str
    find: str
    replace: str
    description: str = ""

    def to_dict(self) -> dict[str, Any]:
        return asdict(self)

    @classmethod
    def from_dict(cls, d: Mappingish) -> "PatchSpec":
        return cls(
            relative_path=str(d["relative_path"]),
            find=str(d["find"]),
            replace=str(d["replace"]),
            description=str(d.get("description") or ""),
        )


# typing helper without importing Mapping everywhere in from_dict
Mappingish = dict[str, Any]


@dataclass
class TaskCheckpoint:
    schema: str = CHECKPOINT_SCHEMA
    task_id: str = ""
    family_id: str = FAMILY_ID
    status: TaskStatus = "authorized"
    authorized_at: float = 0.0
    updated_at: float = 0.0
    base_repo: str | None = None
    base_ref: str | None = None
    worktree_path: str | None = None
    branch: str | None = None
    requirement_paths: list[str] = field(default_factory=list)
    patch: dict[str, Any] | None = None
    context_packet_path: str | None = None
    aodl_path: str | None = None
    aodl_source_hash: str | None = None
    verifier_kind: str = "content_predicate"
    execution_completed: bool | None = None
    verified_success: bool | None = None
    last_error: str | None = None
    measurements: dict[str, Any] = field(default_factory=dict)
    notes: list[str] = field(default_factory=list)

    def to_dict(self) -> dict[str, Any]:
        return asdict(self)

    @classmethod
    def from_dict(cls, d: dict[str, Any]) -> "TaskCheckpoint":
        known = {f.name for f in cls.__dataclass_fields__.values()}  # type: ignore[attr-defined]
        # dataclass fields
        fields = {k: d[k] for k in d if k in {
            "schema", "task_id", "family_id", "status", "authorized_at", "updated_at",
            "base_repo", "base_ref", "worktree_path", "branch", "requirement_paths",
            "patch", "context_packet_path", "aodl_path", "aodl_source_hash",
            "verifier_kind", "execution_completed", "verified_success", "last_error",
            "measurements", "notes",
        }}
        return cls(**fields)  # type: ignore[arg-type]


def _tasks_dir() -> Path:
    d = paths.home() / "state" / "tasks"
    d.mkdir(parents=True, exist_ok=True)
    return d


def checkpoint_path(task_id: str) -> Path:
    return _tasks_dir() / f"{task_id}.json"


def save_checkpoint(cp: TaskCheckpoint) -> Path:
    cp.updated_at = time.time()
    path = checkpoint_path(cp.task_id)
    path.write_text(json.dumps(cp.to_dict(), indent=2, ensure_ascii=False) + "\n", encoding="utf-8")
    return path


def load_checkpoint(task_id: str) -> TaskCheckpoint:
    path = checkpoint_path(task_id)
    if not path.is_file():
        raise FileNotFoundError(f"no checkpoint for task_id={task_id}")
    return TaskCheckpoint.from_dict(json.loads(path.read_text(encoding="utf-8")))


def list_checkpoints() -> list[dict[str, Any]]:
    out = []
    for p in sorted(_tasks_dir().glob("*.json")):
        try:
            d = json.loads(p.read_text(encoding="utf-8"))
        except (OSError, json.JSONDecodeError):
            continue
        out.append({
            "task_id": d.get("task_id"),
            "status": d.get("status"),
            "family_id": d.get("family_id"),
            "verified_success": d.get("verified_success"),
            "execution_completed": d.get("execution_completed"),
            "path": str(p),
        })
    return out


def _run_git(args: list[str], *, cwd: Path | None = None, check: bool = True) -> subprocess.CompletedProcess[str]:
    return subprocess.run(
        ["git", *args],
        cwd=str(cwd) if cwd else None,
        capture_output=True,
        text=True,
        check=check,
    )


def _family_cascade() -> CascadePolicy:
    # Shadow-capable cascade for the reference family (not a live bridge flip).
    return CascadePolicy(
        capability_id=CAPABILITY_ID,
        stages=(
            StagePolicy("context_resolve", 0.0),
            StagePolicy("worktree_patch", 0.0),
            StagePolicy("independent_verifier", 0.0, terminal=True),
        ),
        final_stage="independent_verifier",
        min_success_rate=0.99,
        max_success_regression=0.01,
        objective="premium_tokens_per_verified_success",
        status="promoted",
    )


def compile_family_aodl() -> dict[str, Any]:
    return compile_aodl(
        capability_id=CAPABILITY_ID,
        cascade=_family_cascade(),
        routines=(),
        config=AodlBindingConfig(
            harness_id="omp",
            executor_id="z0int-runtime",
            verifier_id="independent-content-verifier",
            receipt_store_id="decision-receipts",
            include_routine_slot=False,
            budgets=AodlBudgets(tokens=8000, premium_tokens=0, attention=1),
            precision_floor=0.95,
            source="z0int.task_loop",
        ),
    )


def authorize_task(
    *,
    base_repo: Path | str,
    patch: PatchSpec,
    requirement_paths: list[str] | None = None,
    task_id: str | None = None,
    base_ref: str | None = None,
) -> TaskCheckpoint:
    """Create an authorized checkpoint. Does not execute."""
    repo = Path(base_repo).expanduser().resolve()
    if not (repo / ".git").exists() and not (repo / ".git").is_file():
        # allow bare or worktree
        if not (repo / ".git").exists():
            raise ValueError(f"base_repo is not a git repo: {repo}")
    tid = task_id or f"twp-{uuid.uuid4().hex[:12]}"
    ref = base_ref
    if ref is None:
        try:
            ref = _run_git(["rev-parse", "HEAD"], cwd=repo).stdout.strip()
        except subprocess.CalledProcessError as exc:
            raise ValueError(f"cannot resolve HEAD in {repo}: {exc.stderr}") from exc
    cp = TaskCheckpoint(
        task_id=tid,
        family_id=FAMILY_ID,
        status="authorized",
        authorized_at=time.time(),
        updated_at=time.time(),
        base_repo=str(repo),
        base_ref=ref,
        requirement_paths=list(requirement_paths or []),
        patch=patch.to_dict(),
        notes=["authorized; not executed; verified_success=null"],
    )
    save_checkpoint(cp)
    return cp


def step_resolve(cp: TaskCheckpoint, *, allow_qmd: bool = False) -> TaskCheckpoint:
    if cp.status not in {"authorized", "resolved", "failed"}:
        # allow re-resolve from authorized/resolved
        if cp.status not in {"authorized", "resolved"}:
            raise ValueError(f"cannot resolve from status={cp.status}")
    t0 = time.perf_counter()
    needs: list[InformationNeed] = []
    root = Path(cp.base_repo) if cp.base_repo else None
    for i, path in enumerate(cp.requirement_paths):
        needs.append(InformationNeed(id=f"req{i}", description=path, kind="exact_path", path=path))
    if cp.patch and cp.patch.get("relative_path"):
        needs.append(
            InformationNeed(
                id="target",
                description=str(cp.patch["relative_path"]),
                kind="exact_path",
                path=str(cp.patch["relative_path"]),
            )
        )
    if not needs:
        needs.append(
            InformationNeed(
                id="family",
                description=f"authorized family {FAMILY_ID}",
                kind="natural_language",
            )
        )
    packet = resolve_context(
        needs=needs,
        task_id=cp.task_id,
        project_root=root,
        allow_qmd=allow_qmd,
        allow_memory=False,
        use_cache=True,
    )
    task_dir = _tasks_dir() / cp.task_id
    task_dir.mkdir(parents=True, exist_ok=True)
    pkt_path = task_dir / "context_packet.json"
    pkt_path.write_text(json.dumps(packet.to_dict(), indent=2, ensure_ascii=False) + "\n", encoding="utf-8")

    doc = compile_family_aodl()
    assert_basic_aodl_invariants(doc)
    doc = attach_context_to_aodl(doc, packet, trace_id=cp.task_id)
    assert_basic_aodl_invariants(doc)
    # intent sourceHash must survive attach
    aodl_path = task_dir / "aodl.json"
    aodl_path.write_text(json.dumps(doc, indent=2, ensure_ascii=False) + "\n", encoding="utf-8")

    cp.context_packet_path = str(pkt_path)
    cp.aodl_path = str(aodl_path)
    cp.aodl_source_hash = str(doc["provenance"]["sourceHash"])
    cp.status = "resolved"
    cp.measurements["resolve_wall_ms"] = (time.perf_counter() - t0) * 1000.0
    cp.measurements["unresolved_gaps"] = list(packet.unresolved_gaps)
    cp.notes.append(f"resolved; gaps={len(packet.unresolved_gaps)}; evidence={len(packet.evidence)}")
    save_checkpoint(cp)
    return cp


def step_worktree(cp: TaskCheckpoint, *, worktrees_root: Path | str | None = None) -> TaskCheckpoint:
    if cp.status not in {"resolved", "worktree_ready", "patched", "failed"}:
        if cp.status != "resolved" and cp.status != "worktree_ready":
            raise ValueError(f"need resolved before worktree; got {cp.status}")
    if not cp.base_repo or not cp.base_ref:
        raise ValueError("checkpoint missing base_repo/base_ref")
    repo = Path(cp.base_repo)
    root = Path(worktrees_root) if worktrees_root else (paths.home() / "state" / "worktrees")
    root.mkdir(parents=True, exist_ok=True)
    branch = cp.branch or f"z0int/{cp.task_id}"
    wt = Path(cp.worktree_path) if cp.worktree_path else (root / cp.task_id)
    if wt.exists() and (wt / ".git").exists():
        cp.worktree_path = str(wt)
        cp.branch = branch
        cp.status = "worktree_ready"
        cp.notes.append("worktree already present; resumed")
        save_checkpoint(cp)
        return cp
    if wt.exists():
        raise ValueError(f"worktree path exists but is not a git worktree: {wt}")
    # create branch from base_ref without checking out main
    _run_git(["branch", branch, cp.base_ref], cwd=repo, check=False)  # may already exist
    try:
        _run_git(["worktree", "add", str(wt), branch], cwd=repo, check=True)
    except subprocess.CalledProcessError as exc:
        # try force path if branch exists elsewhere
        cp.last_error = (exc.stderr or exc.stdout or str(exc))[:500]
        cp.status = "failed"
        save_checkpoint(cp)
        raise
    cp.worktree_path = str(wt)
    cp.branch = branch
    cp.status = "worktree_ready"
    cp.notes.append(f"worktree ready at {wt}")
    save_checkpoint(cp)
    return cp


def step_apply_patch(cp: TaskCheckpoint) -> TaskCheckpoint:
    if cp.status not in {"worktree_ready", "patched", "execution_completed"}:
        raise ValueError(f"need worktree_ready before patch; got {cp.status}")
    if not cp.worktree_path or not cp.patch:
        raise ValueError("missing worktree or patch")
    wt = Path(cp.worktree_path)
    spec = PatchSpec.from_dict(cp.patch)
    target = wt / spec.relative_path
    if not target.is_file():
        cp.status = "failed"
        cp.last_error = f"target missing: {target}"
        cp.execution_completed = False
        save_checkpoint(cp)
        raise FileNotFoundError(cp.last_error)
    text = target.read_text(encoding="utf-8")
    if spec.find not in text:
        cp.status = "failed"
        cp.last_error = "patch find-string not present (not applied)"
        cp.execution_completed = False
        save_checkpoint(cp)
        raise ValueError(cp.last_error)
    if text.count(spec.find) != 1:
        cp.status = "failed"
        cp.last_error = f"patch find-string not unique (count={text.count(spec.find)})"
        cp.execution_completed = False
        save_checkpoint(cp)
        raise ValueError(cp.last_error)
    new_text = text.replace(spec.find, spec.replace, 1)
    target.write_text(new_text, encoding="utf-8")
    # commit inside worktree for durability (still no merge)
    try:
        _run_git(["add", "--", spec.relative_path], cwd=wt)
        _run_git(
            ["-c", "user.email=z0int@local", "-c", "user.name=z0int-task-loop",
             "commit", "-m", f"z0int task {cp.task_id}: bounded patch"],
            cwd=wt,
        )
    except subprocess.CalledProcessError as exc:
        cp.last_error = f"commit failed: {(exc.stderr or '')[:300]}"
        # still mark patched on disk
    cp.status = "patched"
    cp.execution_completed = True  # worker finished applying; NOT verified
    cp.verified_success = None
    cp.notes.append("patch applied; execution_completed=true; verified_success=null")
    save_checkpoint(cp)
    return cp


def step_verify(cp: TaskCheckpoint) -> TaskCheckpoint:
    """Independent verifier: content predicate on worktree, not worker claim."""
    if not cp.worktree_path or not cp.patch:
        raise ValueError("missing worktree or patch for verify")
    if cp.status not in {"patched", "execution_completed", "verified", "failed"}:
        # allow verify after patch only
        if cp.status != "patched" and cp.execution_completed is not True:
            raise ValueError(f"cannot verify from status={cp.status}")
    wt = Path(cp.worktree_path)
    spec = PatchSpec.from_dict(cp.patch)
    target = wt / spec.relative_path
    ok = False
    detail = ""
    try:
        text = target.read_text(encoding="utf-8")
        if spec.replace in text and spec.find not in text:
            ok = True
            detail = "replace present and find absent"
        else:
            detail = "predicate failed: replace missing or find still present"
    except OSError as exc:
        detail = f"read failed: {exc}"
    # Also ensure base repo still has OLD content (isolation / no silent main edit)
    base_ok = True
    if cp.base_repo:
        base_target = Path(cp.base_repo) / spec.relative_path
        if base_target.is_file():
            base_text = base_target.read_text(encoding="utf-8")
            if spec.replace in base_text and spec.find not in base_text:
                # main already has replace — may be ok if same checkout; flag
                if Path(cp.base_repo).resolve() == wt.resolve():
                    base_ok = False
                    detail += "; base==worktree (not isolated)"
    cp.execution_completed = True
    cp.verified_success = bool(ok and base_ok)
    cp.status = "verified" if cp.verified_success else "failed"
    cp.measurements["verify_detail"] = detail
    cp.notes.append(f"verify {cp.verified_success}: {detail}")
    if not cp.verified_success:
        cp.last_error = detail
    save_checkpoint(cp)
    return cp


def run_until(
    cp: TaskCheckpoint,
    *,
    until: TaskStatus = "verified",
    allow_qmd: bool = False,
    worktrees_root: Path | str | None = None,
) -> TaskCheckpoint:
    """Advance checkpoint through the pipeline until status or failure."""
    order = ["authorized", "resolved", "worktree_ready", "patched", "verified"]
    # map status to next step
    while True:
        if cp.status == "failed":
            return cp
        if cp.status == until or (
            until == "verified" and cp.status == "verified"
        ):
            return cp
        if cp.status == "authorized":
            cp = step_resolve(cp, allow_qmd=allow_qmd)
            if until == "resolved":
                return cp
            continue
        if cp.status == "resolved":
            cp = step_worktree(cp, worktrees_root=worktrees_root)
            if until == "worktree_ready":
                return cp
            continue
        if cp.status == "worktree_ready":
            cp = step_apply_patch(cp)
            if until == "patched":
                return cp
            continue
        if cp.status == "patched" or cp.status == "execution_completed":
            cp = step_verify(cp)
            return cp
        # verified / abandoned
        return cp


def resume_task(task_id: str, **kwargs: Any) -> TaskCheckpoint:
    cp = load_checkpoint(task_id)
    return run_until(cp, **kwargs)


def make_fixture_repo(root: Path) -> tuple[Path, PatchSpec]:
    """Create a tiny git repo with a deliberate defect for the reference family."""
    root = root.expanduser().resolve()
    root.mkdir(parents=True, exist_ok=True)
    if not (root / ".git").exists():
        _run_git(["init"], cwd=root)
        _run_git(["-c", "user.email=z0int@local", "-c", "user.name=z0int", "commit", "--allow-empty", "-m", "init"], cwd=root)
    req = root / "REQUIREMENT.md"
    req.write_text(
        "# Requirement\n\nTarget must print READY not BROKEN.\nVerifier: content predicate independent of worker.\n",
        encoding="utf-8",
    )
    src = root / "app.py"
    src.write_text("STATUS = \"BROKEN\"\nprint(STATUS)\n", encoding="utf-8")
    _run_git(["add", "REQUIREMENT.md", "app.py"], cwd=root)
    _run_git(
        ["-c", "user.email=z0int@local", "-c", "user.name=z0int", "commit", "-m", "fixture broken"],
        cwd=root,
        check=False,
    )
    patch = PatchSpec(
        relative_path="app.py",
        find='STATUS = "BROKEN"',
        replace='STATUS = "READY"',
        description="context-recovery reference: flip BROKEN→READY",
    )
    return root, patch
