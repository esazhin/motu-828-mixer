"""What the application needs from the device, the clock and the clients, as structural Protocols."""
from dataclasses import dataclass
from typing import Any, Protocol, runtime_checkable

from _motu_mixer.domain.status import ConnectionStatus, LoopPath, Refusal
from _motu_mixer.domain.values import Delta, PortAddress


class PortBusy(Exception):
    """The control port is held by another program."""


class PortGone(Exception):
    """The control port vanished (unplugged or closed underneath us)."""


@runtime_checkable
class PortLocator(Protocol):
    def locate(self, name: str) -> PortAddress | None: ...


@runtime_checkable
class ControlPort(Protocol):
    def open(self, addr: PortAddress) -> None: ...
    def read(self) -> bytes: ...
    def write(self, frame: bytes) -> None: ...
    def close(self) -> None: ...


@runtime_checkable
class BytesSink(Protocol):
    def on_bytes(self, chunk: bytes) -> None: ...
    def on_port_gone(self) -> None: ...


@runtime_checkable
class DevicePump(Protocol):
    def start(self, port: ControlPort, sink: BytesSink) -> None: ...
    def stop(self) -> None: ...


@runtime_checkable
class Clock(Protocol):
    def monotonic(self) -> float: ...
    async def sleep(self, seconds: float) -> None: ...


@dataclass(frozen=True)
class Snapshot:
    state: Any
    status: ConnectionStatus
    read_only_detail: str | None
    loops: frozenset[LoopPath]


@runtime_checkable
class StatePublisher(Protocol):
    def publish_state(self, snapshot: Snapshot) -> None: ...
    def publish_delta(self, deltas: tuple[Delta, ...]) -> None: ...
    def publish_status(self, status: ConnectionStatus, detail: str | None, loops: frozenset[LoopPath]) -> None: ...
    def publish_meters(self, levels) -> None: ...


class ChangeOutcome:
    @dataclass(frozen=True)
    class Accepted:
        sent: tuple[bytes, ...]

    @dataclass(frozen=True)
    class Refused:
        refusal: Refusal


@dataclass(frozen=True)
class DeviceEvents:
    probe_replied: bool
    settled: bool = False   # the unit's whole state has arrived (SYNCED or READ_ONLY)
