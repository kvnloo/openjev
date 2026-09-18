"""Immutable candidate artifact import (z0int.candidate_artifact.v1).

Import installs under ~/.z0int/specialists/<artifact_id>/ with status=candidate.
Import alone NEVER promotes. Production registry must point at immutable ids
and only consume promotion.status == \"promoted\".
"""

from __future__ import annotations

import hashlib
import json
import re
import shutil
import time
from pathlib import Path
from typing import Any

from . import paths

SCHEMA = "z0int.candidate_artifact.v1"
ALLOWED_RUNTIMES = frozenset(
    {
        "z0int.backend.mushroom.v1",
        "z0int.backend.nanojev.v1",
        "z0int.backend.local_plasticity.v1",
        "z0int.backend.cascade.v1",
        "z0int.backend.routine.v1",
    }
)

_AID_RE = re.compile(r"^sha256:[0-9a-f]{64}$")


def specialists_root() -> Path:
    paths.ensure_layout()
    return paths.home() / "specialists"


def _sha256_file(path: Path) -> str:
    h = hashlib.sha256()
    with path.open("rb") as f:
        for chunk in iter(lambda: f.read(1024 * 1024), b""):
            h.update(chunk)
    return h.hexdigest()


def _normalize_artifact_id(raw: str, file_hashes: list[str]) -> str:
    if raw.startswith("sha256:") and _AID_RE.match(raw):
        return raw
    # derive from sorted file hashes if missing/invalid
    blob = "|".join(sorted(file_hashes)).encode()
    return "sha256:" + hashlib.sha256(blob).hexdigest()


def load_manifest(path: Path | str) -> dict[str, Any]:
    p = Path(path)
    data = json.loads(p.read_text(encoding="utf-8"))
    if not isinstance(data, dict):
        raise ValueError("manifest must be a JSON object")
    return data


def validate_manifest(manifest: dict[str, Any], *, base_dir: Path | None = None) -> list[str]:
    """Return list of error strings; empty = ok."""
    errs: list[str] = []
    if manifest.get("schema") != SCHEMA:
        errs.append(f"schema must be {SCHEMA}, got {manifest.get('schema')!r}")
    runtime = manifest.get("runtime")
    if runtime not in ALLOWED_RUNTIMES:
        errs.append(f"unknown runtime: {runtime!r}")
    files = manifest.get("files")
    if not isinstance(files, list) or not files:
        errs.append("files must be a non-empty list")
        return errs
    base = base_dir or Path(".")
    digests: list[str] = []
    for entry in files:
        if not isinstance(entry, dict):
            errs.append("file entry must be object")
            continue
        rel = entry.get("path")
        want = entry.get("sha256")
        if not rel or not want:
            errs.append("file entry needs path + sha256")
            continue
        fp = base / str(rel)
        if not fp.is_file():
            errs.append(f"missing file: {rel}")
            continue
        got = _sha256_file(fp)
        digests.append(got)
        if got != str(want).lower().removeprefix("sha256:"):
            # accept bare hex or sha256:hex
            want_hex = str(want).lower().removeprefix("sha256:")
            if got != want_hex:
                errs.append(f"hash mismatch: {rel} expected {want_hex[:12]}… got {got[:12]}…")
    aid = manifest.get("artifact_id")
    if aid and not _AID_RE.match(str(aid)):
        # soft: will recompute on import if invalid
        if not str(aid).startswith("sha256:"):
            errs.append(f"artifact_id should be sha256:<64hex>, got {aid!r}")
    promo = manifest.get("promotion") or {}
    if promo.get("status") == "promoted":
        errs.append("producer must not emit promotion.status=promoted (import forces candidate)")
    if not manifest.get("capability_id"):
        errs.append("capability_id required")
    producer = manifest.get("producer") or {}
    if not producer.get("repo") or not producer.get("revision"):
        errs.append("producer.repo and producer.revision required")
    return errs


def inspect_manifest(path: Path | str) -> dict[str, Any]:
    p = Path(path).resolve()
    base = p.parent
    manifest = load_manifest(p)
    errs = validate_manifest(manifest, base_dir=base)
    return {
        "schema": "z0int.artifact_inspect.v1",
        "path": str(p),
        "ok": not errs,
        "errors": errs,
        "manifest": {
            "schema": manifest.get("schema"),
            "artifact_id": manifest.get("artifact_id"),
            "capability_id": manifest.get("capability_id"),
            "runtime": manifest.get("runtime"),
            "producer": manifest.get("producer"),
            "promotion": manifest.get("promotion"),
            "n_files": len(manifest.get("files") or []),
        },
    }


def _safe_id_dir(artifact_id: str) -> str:
    # filesystem-safe: sha256_<hex>
    return artifact_id.replace(":", "_")


def import_manifest(path: Path | str, *, force: bool = False) -> dict[str, Any]:
    p = Path(path).resolve()
    base = p.parent
    manifest = load_manifest(p)
    errs = validate_manifest(manifest, base_dir=base)
    # strip promoted if present after reporting
    hard = [e for e in errs if "promoted" not in e]
    if hard:
        return {"ok": False, "errors": errs}

    file_hashes = []
    for entry in manifest["files"]:
        fp = base / entry["path"]
        file_hashes.append(_sha256_file(fp))
    artifact_id = _normalize_artifact_id(str(manifest.get("artifact_id") or ""), file_hashes)

    dest_root = specialists_root() / _safe_id_dir(artifact_id)
    if dest_root.exists() and not force:
        return {
            "ok": True,
            "already_installed": True,
            "artifact_id": artifact_id,
            "path": str(dest_root),
            "promotion": {"status": "candidate"},
        }

    if dest_root.exists():
        shutil.rmtree(dest_root)
    dest_root.mkdir(parents=True)

    installed_files = []
    for entry in manifest["files"]:
        src = base / entry["path"]
        rel = Path(entry["path"]).name  # flatten to artifact dir
        dst = dest_root / rel
        shutil.copy2(src, dst)
        installed_files.append({"path": rel, "sha256": _sha256_file(dst)})

    installed = {
        "schema": SCHEMA,
        "artifact_id": artifact_id,
        "producer": dict(manifest.get("producer") or {}),
        "capability_id": manifest["capability_id"],
        "family": manifest.get("family"),
        "runtime": manifest["runtime"],
        "files": installed_files,
        "training": manifest.get("training") or {},
        "claims": manifest.get("claims") or {},
        # FORCE candidate — import never promotes
        "promotion": {"status": "candidate", "imported_at": time.time()},
        "imported_from": str(p),
        "imported_at": time.time(),
    }
    (dest_root / "manifest.json").write_text(json.dumps(installed, indent=2) + "\n", encoding="utf-8")
    # index
    _update_index(artifact_id, installed)
    return {
        "ok": True,
        "already_installed": False,
        "artifact_id": artifact_id,
        "path": str(dest_root),
        "promotion": installed["promotion"],
    }


def _index_path() -> Path:
    return specialists_root() / "index.json"


def _update_index(artifact_id: str, installed: dict[str, Any]) -> None:
    idx_path = _index_path()
    if idx_path.is_file():
        try:
            idx = json.loads(idx_path.read_text(encoding="utf-8"))
        except Exception:
            idx = {"schema": "z0int.specialists_index.v1", "artifacts": {}}
    else:
        idx = {"schema": "z0int.specialists_index.v1", "artifacts": {}}
    arts = idx.setdefault("artifacts", {})
    arts[artifact_id] = {
        "artifact_id": artifact_id,
        "capability_id": installed.get("capability_id"),
        "runtime": installed.get("runtime"),
        "promotion": installed.get("promotion"),
        "dir": _safe_id_dir(artifact_id),
        "producer": installed.get("producer"),
    }
    idx_path.write_text(json.dumps(idx, indent=2) + "\n", encoding="utf-8")


def list_artifacts() -> list[dict[str, Any]]:
    root = specialists_root()
    out: list[dict[str, Any]] = []
    # prefer index
    idx_path = _index_path()
    if idx_path.is_file():
        try:
            idx = json.loads(idx_path.read_text(encoding="utf-8"))
            for row in (idx.get("artifacts") or {}).values():
                if isinstance(row, dict):
                    # re-read manifest for freshness
                    d = root / str(row.get("dir") or _safe_id_dir(row["artifact_id"]))
                    man = d / "manifest.json"
                    if man.is_file():
                        try:
                            out.append(json.loads(man.read_text(encoding="utf-8")))
                            continue
                        except Exception:
                            pass
                    out.append(row)
            return out
        except Exception:
            pass
    for child in sorted(root.iterdir()) if root.is_dir() else []:
        man = child / "manifest.json"
        if man.is_file():
            try:
                out.append(json.loads(man.read_text(encoding="utf-8")))
            except Exception:
                continue
    return out


def get_artifact(artifact_id: str) -> dict[str, Any] | None:
    d = specialists_root() / _safe_id_dir(artifact_id)
    man = d / "manifest.json"
    if not man.is_file():
        return None
    return json.loads(man.read_text(encoding="utf-8"))
