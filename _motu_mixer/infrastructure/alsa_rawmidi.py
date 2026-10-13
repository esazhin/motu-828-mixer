"""ALSA rawmidi transport for '828 COM': the port found by name in `amidi -l`, opened through libasound."""
import ctypes
import errno
import logging
import signal
import subprocess
import time
from contextlib import contextmanager

from _motu_mixer.application.ports import PortBusy, PortGone
from _motu_mixer.domain.properties import spec
from _motu_mixer.domain.values import Address, PortAddress, Report
from _motu_mixer.protocol.sysex import (
    SysexSplitter, decode, decode_value, enable_frame, encode_set, encode_value, probe_frame,
)

READ_BUFFER = 64 * 1024
# The kernel's rawmidi maximum. The 828 sends its whole state (~80 KB) in one burst after enable and never repeats
# it unasked; the 4 KB default overran on the real unit while the reader thread waited for the GIL.
INPUT_BUFFER = 1 << 20

log = logging.getLogger(__name__)


class AmidiPortLocator:
    def __init__(self, runner=subprocess.run):
        self._run = runner

    def locate(self, name: str) -> PortAddress | None:
        try:
            listing = self._run(["amidi", "-l"], capture_output=True, text=True, timeout=5)
        except (OSError, subprocess.TimeoutExpired):
            return None
        if listing.returncode != 0:
            return None
        # lines read 'Dir Device Name', and the name may contain spaces
        for line in listing.stdout.splitlines():
            columns = line.split(None, 2)
            if len(columns) == 3 and columns[2].strip() == name:
                return PortAddress(columns[1])
        return None


def load_libasound():
    lib = ctypes.CDLL("libasound.so.2")
    handle_ref = ctypes.POINTER(ctypes.c_void_p)
    lib.snd_rawmidi_open.argtypes = [handle_ref, handle_ref, ctypes.c_char_p, ctypes.c_int]
    lib.snd_rawmidi_nonblock.argtypes = [ctypes.c_void_p, ctypes.c_int]
    lib.snd_rawmidi_read.argtypes = [ctypes.c_void_p, ctypes.c_void_p, ctypes.c_size_t]
    lib.snd_rawmidi_read.restype = ctypes.c_ssize_t
    lib.snd_rawmidi_write.argtypes = [ctypes.c_void_p, ctypes.c_char_p, ctypes.c_size_t]
    lib.snd_rawmidi_write.restype = ctypes.c_ssize_t
    lib.snd_rawmidi_close.argtypes = [ctypes.c_void_p]
    lib.snd_rawmidi_params_malloc.argtypes = [ctypes.POINTER(ctypes.c_void_p)]
    lib.snd_rawmidi_params_current.argtypes = [ctypes.c_void_p, ctypes.c_void_p]
    lib.snd_rawmidi_params_set_buffer_size.argtypes = [ctypes.c_void_p, ctypes.c_void_p, ctypes.c_size_t]
    lib.snd_rawmidi_params.argtypes = [ctypes.c_void_p, ctypes.c_void_p]
    lib.snd_rawmidi_params_free.argtypes = [ctypes.c_void_p]
    lib.snd_rawmidi_params_free.restype = None
    return lib


class AlsaRawMidiPort:
    def __init__(self, lib=None):
        self._lib = lib if lib is not None else load_libasound()
        self._in = self._out = None
        self._buffer = ctypes.create_string_buffer(READ_BUFFER)

    def open(self, addr: PortAddress) -> None:
        handle_in, handle_out = ctypes.c_void_p(), ctypes.c_void_p()
        rc = self._lib.snd_rawmidi_open(ctypes.byref(handle_in), ctypes.byref(handle_out), addr.device.encode(), 0)
        if rc == -errno.EBUSY:
            raise PortBusy(addr.device)
        if rc < 0:
            raise PortGone(f"{addr.device}: error {rc}")
        self._in, self._out = handle_in, handle_out
        self._lib.snd_rawmidi_nonblock(handle_in, 1)
        self._grow_input_buffer(handle_in)

    def _grow_input_buffer(self, handle) -> None:
        params = ctypes.c_void_p()
        if self._lib.snd_rawmidi_params_malloc(ctypes.byref(params)) < 0:
            log.warning("rawmidi: no params object; input buffer left at its default")
            return
        try:
            rc = self._lib.snd_rawmidi_params_current(handle, params)
            if rc >= 0:
                rc = self._lib.snd_rawmidi_params_set_buffer_size(handle, params, INPUT_BUFFER)
            if rc >= 0:
                rc = self._lib.snd_rawmidi_params(handle, params)
            if rc < 0:
                log.warning("rawmidi: input buffer not raised to %d bytes (error %d); a dump may overrun", INPUT_BUFFER, rc)
        finally:
            self._lib.snd_rawmidi_params_free(params)

    def read(self) -> bytes:
        n = self._lib.snd_rawmidi_read(self._in, self._buffer, READ_BUFFER)
        if n == -errno.EAGAIN:
            return b""
        if n < 0:
            raise PortGone(f"read error {n}")
        return self._buffer.raw[:n]

    def write(self, frame: bytes) -> None:
        if self._lib.snd_rawmidi_write(self._out, frame, len(frame)) < 0:
            raise PortGone("write error")

    def close(self) -> None:
        for handle in (self._in, self._out):
            if handle is not None:
                self._lib.snd_rawmidi_close(handle)
        self._in = self._out = None


# The only cells a live write check may touch: unused and inaudible (PROTOCOL-SPEC §9).
LIVE_ALLOWED = frozenset({
    Address(1016, 643),  # kiMixFader, Reverb 1 x ADAT B 8
    Address(1019, 643),  # kiMixMute, the same cell
    Address(1027, 15),  # koBusFader, Reverb 2
    Address(1028, 15),  # koBusMute, Reverb 2
    Address(5010, 40),  # kFPGAPatch, Line out 10
})


class CellNotAllowed(ValueError):
    pass


def read_cell(connect, address: Address, timeout=5.0) -> bytes | None:
    """Reads one cell in a fresh session: probe, enable, then the dump until the cell is reported."""
    port = connect()
    try:
        port.write(probe_frame())
        port.write(enable_frame())
        splitter, deadline = SysexSplitter(), time.monotonic() + timeout
        while time.monotonic() < deadline:
            chunk = port.read()
            if not chunk:
                time.sleep(0.001)
            for frame in splitter.feed(chunk):
                report = decode(frame)
                if isinstance(report, Report) and report.address == address:
                    return report.raw
        return None
    finally:
        port.close()


def write_cell(connect, address: Address, value) -> None:
    port = connect()
    try:
        port.write(encode_set(address, value))
    finally:
        port.close()


def _interrupted(signum, frame):
    raise KeyboardInterrupt(signal.Signals(signum).name)


@contextmanager
def snapshot_restore(connect, address: Address):
    """Yields the cell's raw value and writes it back on the way out: on success, failure, SIGINT or SIGTERM."""
    if address not in LIVE_ALLOWED:
        raise CellNotAllowed("cell not in PROTOCOL-SPEC §9")
    before = read_cell(connect, address)
    if before is None:
        raise RuntimeError(f"cell {address} not reported, nothing written")
    handlers = {sig: signal.signal(sig, _interrupted) for sig in (signal.SIGINT, signal.SIGTERM)}
    try:
        yield before
    finally:
        for sig in handlers:
            signal.signal(sig, signal.SIG_IGN)
        try:
            write_cell(connect, address, decode_value(address.prop, before))
        finally:
            for sig, handler in handlers.items():
                signal.signal(sig, handler)


def run_write_check(name: str, proves: str, connect, address: Address, value, settle=0.5, sleep=time.sleep) -> str:
    """Writes one allowed cell, reads it back in a fresh session, restores it and reads the restore back."""
    with snapshot_restore(connect, address) as before:
        write_cell(connect, address, value)
        sleep(settle)
        after = read_cell(connect, address)
    restored = read_cell(connect, address)
    passed = after == encode_value(spec(address.prop).value_type, value) and restored == before
    return f"{name} {'passed' if passed else 'failed'}: {proves}"
