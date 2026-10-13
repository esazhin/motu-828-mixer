"""The write gate: writes are allowed only to a fully reported unit matching the verified 828E / API 9."""
from dataclasses import dataclass

from .status import Refusal, RefusalKind

VERIFIED_MODEL = "828E"
VERIFIED_API = 9


@dataclass(frozen=True)
class GateOpen:
    pass


def write_gate(state) -> GateOpen | Refusal:
    identity = state.identity()
    optical = state.optical_config()
    if identity is None or optical is None or not state.arrays_complete():
        return Refusal(RefusalKind.NOT_SYNCED, "the unit's full report has not arrived yet")
    if identity.model != VERIFIED_MODEL:
        return Refusal(RefusalKind.READ_ONLY, f"model {identity.model}, expected {VERIFIED_MODEL}")
    if identity.api != VERIFIED_API:
        return Refusal(RefusalKind.READ_ONLY, f"API version {identity.api}, expected {VERIFIED_API}")
    if not optical.verified:
        return Refusal(RefusalKind.READ_ONLY, "unverified optical configuration")
    return GateOpen()
