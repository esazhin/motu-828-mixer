"""Connection status, refusals and S/PDIF loop paths."""
from dataclasses import dataclass
from enum import StrEnum

from .values import BusIndex, InputIndex, OutputSlot, Source

_SPDIF_CODES = (58, 59)


class ConnectionStatus(StrEnum):
    NO_DEVICE = "no_device"
    NO_REPLY = "no_reply"
    BUSY = "busy"
    SYNCING = "syncing"
    SYNCED = "synced"
    READ_ONLY = "read_only"
    DISCONNECTED = "disconnected"
    RELEASED = "released"


class RefusalKind(StrEnum):
    LOOP = "loop"
    READ_ONLY = "read_only"
    NOT_SYNCED = "not_synced"
    NOT_WRITABLE_PROPERTY = "not_writable_property"
    INDEX_RANGE = "index_range"
    ODD_LINK_INDEX = "odd_link_index"
    SLOT_NOT_WRITABLE = "slot_not_writable"
    MALFORMED_REQUEST = "malformed_request"
    VALUE_RANGE = "value_range"


@dataclass(frozen=True)
class PathNode:
    ref: Source | InputIndex | BusIndex | OutputSlot
    label: str


@dataclass(frozen=True)
class LoopPath:
    hops: tuple[PathNode, ...]

    def __post_init__(self):
        first, last = self.hops[0].ref, self.hops[-1].ref
        if not (isinstance(first, Source) and first.code in _SPDIF_CODES):
            raise ValueError("a loop path starts at S/PDIF in")
        if not (isinstance(last, OutputSlot) and last in _SPDIF_CODES):
            raise ValueError("a loop path ends at S/PDIF out")

    def label(self) -> str:
        return " → ".join(hop.label for hop in self.hops)


@dataclass(frozen=True)
class Refusal:
    kind: RefusalKind
    detail: str
    path: LoopPath | None = None

    def __post_init__(self):
        if self.path is not None and self.kind is not RefusalKind.LOOP:
            raise ValueError("only a loop refusal carries a path")
