"""Evolver-blind battery interfaces.

Search code gets open rows.  Sealed rows live behind a scoring object and only
aggregate evidence comes back.  This is not cryptographic isolation, but it
makes the repository boundary explicit and testable: candidate generation has
no reason to receive sealed examples at all.
"""

from __future__ import annotations

import hashlib
import json
from dataclasses import asdict, dataclass
from typing import Any, Sequence


@dataclass(frozen=True)
class BatteryManifest:
    battery_id: str
    n_rows: int
    n_sessions: int
    split: str = "sealed"
    schema: str = "z0int.battery_manifest.v1"

    def to_dict(self) -> dict[str, Any]:
        return asdict(self)


def _manifest(rows: Sequence[dict[str, Any]], *, split: str = "sealed") -> BatteryManifest:
    scoped = [r for r in rows if r.get("split") == split]
    sessions = sorted({str(r.get("session_id") or "") for r in scoped if r.get("session_id")})
    # Fingerprint structure/provenance without serializing private features into
    # the public manifest.  This is an identity, not a proof of data contents.
    material = json.dumps({"split": split, "n": len(scoped), "sessions": sessions}, sort_keys=True)
    bid = hashlib.sha256(material.encode("utf-8")).hexdigest()[:24]
    return BatteryManifest(battery_id=bid, n_rows=len(scoped), n_sessions=len(sessions), split=split)


class RoutineSealedGate:
    """Score frozen routine candidates without exposing sealed rows to mining."""

    def __init__(self, rows: Sequence[dict[str, Any]]) -> None:
        self.__rows = tuple(dict(r) for r in rows if r.get("split") == "sealed")
        self.manifest = _manifest(self.__rows)

    def credit(self, candidates, *, config=None):
        from .routines import credit_sealed

        return credit_sealed(self.__rows, candidates, config=config)


class CascadeSealedGate:
    """Score one frozen cascade policy without exposing sealed rows to search."""

    def __init__(self, rows: Sequence[dict[str, Any]]) -> None:
        self.__rows = tuple(dict(r) for r in rows if r.get("split") == "sealed")
        self.manifest = _manifest(self.__rows)

    def credit(self, policy, *, config=None):
        from .cascade import credit_sealed

        return credit_sealed(self.__rows, policy, config=config)
