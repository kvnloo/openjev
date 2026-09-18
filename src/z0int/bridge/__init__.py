"""Resident z0int bridge worker (hot-swappable). Host adapter is the OMP shim."""

from .protocol import BRIDGE_PROTOCOL, compute_build_id

__all__ = ["BRIDGE_PROTOCOL", "compute_build_id"]
