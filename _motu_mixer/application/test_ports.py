from _motu_mixer.application.ports import (
    BytesSink, ChangeOutcome, Clock, ControlPort, DeviceEvents, DevicePump, PortBusy, PortGone, PortLocator,
    Snapshot, StatePublisher,
)
from _motu_mixer.domain.status import ConnectionStatus, LoopPath, PathNode, Refusal, RefusalKind
from _motu_mixer.domain.values import BusIndex, OutputSlot, PortAddress, Source


class Locator:
    def locate(self, name):
        return PortAddress("hw:4,0,1")


class Port:
    def open(self, addr): ...
    def read(self): return b""
    def write(self, frame): ...
    def close(self): ...


class Sink:
    def on_bytes(self, chunk): ...
    def on_port_gone(self): ...


class Pump:
    def start(self, port, sink): ...
    def stop(self): ...


class Publisher:
    def publish_state(self, snapshot): ...
    def publish_delta(self, deltas): ...
    def publish_status(self, status, detail, loops): ...
    def publish_meters(self, levels): ...


class ManualClock:
    def monotonic(self): return 0.0
    async def sleep(self, seconds): ...


def test_objects_with_the_right_methods_satisfy_each_port():
    pairs = [(Locator(), PortLocator), (Port(), ControlPort), (Sink(), BytesSink), (Pump(), DevicePump),
             (Publisher(), StatePublisher), (ManualClock(), Clock)]
    for obj, port in pairs:
        assert isinstance(obj, port)
        assert not isinstance(object(), port)
    assert not isinstance(Sink(), ControlPort)
    assert not isinstance(Port(), DevicePump)
    assert issubclass(PortBusy, Exception) and issubclass(PortGone, Exception)


def test_change_outcomes_events_and_snapshot_carry_their_fields():
    accepted = ChangeOutcome.Accepted((b"\xf0\xf7",))
    assert accepted.sent == (b"\xf0\xf7",)
    refusal = Refusal(RefusalKind.READ_ONLY, "API version 10, expected 9")
    assert ChangeOutcome.Refused(refusal).refusal == refusal
    assert DeviceEvents(probe_replied=True).probe_replied

    loop = LoopPath((PathNode(Source(58), "S/PDIF in"), PathNode(BusIndex(2), "Ana 1-2"),
                     PathNode(OutputSlot(58), "S/PDIF out")))
    state = object()
    snapshot = Snapshot(state=state, status=ConnectionStatus.READ_ONLY,
                        read_only_detail="API version 10, expected 9", loops=frozenset({loop}))
    assert snapshot.state is state
    assert snapshot.status is ConnectionStatus.READ_ONLY
    assert snapshot.read_only_detail == "API version 10, expected 9"
    assert snapshot.loops == frozenset({loop})
