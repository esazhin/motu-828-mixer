"""A stand-in 828 behind ControlPort for hermetic runs: answers the probe, replays a recorded dump on enable."""
from dataclasses import dataclass

from _motu_mixer.application.ports import PortBusy, PortGone
from _motu_mixer.domain.values import PortAddress
from _motu_mixer.infrastructure import capture
from _motu_mixer.protocol import sysex

PROBE_REPLY = sysex.HEADER + bytes([sysex.DEVICE_TO_HOST | sysex.PROBE_REQUEST, sysex.END])
BUSY = "busy"
SILENT = "silent"


@dataclass(frozen=True)
class GoneAfter:
    bytes_read: int


def gone_after(n: int) -> GoneAfter:
    return GoneAfter(n)


class Simulated828Port:
    def __init__(self, dump: bytes, echo: bool = False, fault=None,
                 classify_host_frame=sysex.classify_host_frame, encode_report=sysex.encode_report):
        self._dump = dump
        self._echo = echo
        self._fault = fault
        self._classify = classify_host_frame
        self._encode_report = encode_report
        self._outbox = bytearray()
        self._left = fault.bytes_read if isinstance(fault, GoneAfter) else None
        self.table = {}

    @classmethod
    def from_capture_text(cls, text: str, echo: bool = False, fault=None,
                          parse_amidi_hex=capture.parse_amidi_hex, **codec):
        recorded = parse_amidi_hex(text)
        # the recording starts with the unit's probe reply; enable replays only what followed it
        dump = recorded.removeprefix(PROBE_REPLY)
        return cls(dump, echo=echo, fault=fault, **codec)

    def open(self, addr: PortAddress) -> None:
        if self._fault == BUSY:
            raise PortBusy(addr.device)

    def read(self) -> bytes:
        if self._left == 0:
            raise PortGone("simulated unplug")
        chunk = bytes(self._outbox if self._left is None else self._outbox[:self._left])
        del self._outbox[:len(chunk)]
        if self._left is not None:
            self._left -= len(chunk)
        return chunk

    def write(self, frame: bytes) -> None:
        if self._left == 0:
            raise PortGone("simulated unplug")
        if self._fault == SILENT:
            return
        request = self._classify(frame)
        if request == sysex.PROBE:
            self._outbox += PROBE_REPLY
        elif request == sysex.ENABLE:
            self._outbox += self._dump
        elif isinstance(request, sysex.SetRequest):
            self.table[request.address] = request.raw
            if self._echo:
                self._outbox += self._encode_report(request.address, request.raw)

    def close(self) -> None:
        pass
