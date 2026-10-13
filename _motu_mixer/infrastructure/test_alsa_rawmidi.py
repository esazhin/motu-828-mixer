import ctypes
import random
import signal
import subprocess
import threading
from pathlib import Path
from types import SimpleNamespace

import pytest

from _motu_mixer.application.ports import PortBusy, PortGone
from _motu_mixer.domain.topology import BUS_NAMES, INPUT_NAMES
from _motu_mixer.domain.values import Address, Gain, MixCell, PortAddress
from _motu_mixer.infrastructure.alsa_rawmidi import (
    AlsaRawMidiPort, AmidiPortLocator, CellNotAllowed, read_cell, run_write_check,
)
from _motu_mixer.protocol.sysex import enable_frame, encode_report, encode_set, probe_frame

COM_PORT = PortAddress("hw:4,0,1")
IN_HANDLE, OUT_HANDLE = 0x1111, 0x2222
PROBE = bytes.fromhex("f0 00 00 3b 00 01 01 f7")
ENABLE = bytes.fromhex("f0 00 00 3b 00 01 02 f7")


def fake_libasound(open_rc=0, reads=(), write_rc=None):
    """Stands in for libasound: answers each rawmidi call with the given return codes and records the call."""
    calls = []
    reads = list(reads)

    def value(arg):
        return getattr(arg, "value", arg)

    def target(ref):
        return ref._obj if hasattr(ref, "_obj") else ref.contents

    def snd_rawmidi_open(in_ref, out_ref, name, mode):
        calls.append(("open", value(name), value(mode)))
        if open_rc == 0:
            target(in_ref).value = IN_HANDLE
            target(out_ref).value = OUT_HANDLE
        return open_rc

    def snd_rawmidi_nonblock(handle, flag):
        calls.append(("nonblock", value(handle), value(flag)))
        return 0

    def snd_rawmidi_read(handle, buf, size):
        answer = reads.pop(0)
        calls.append(("read", value(handle)))
        if isinstance(answer, bytes):
            assert len(answer) <= value(size)
            ctypes.memmove(buf, answer, len(answer))
            return len(answer)
        return answer

    def snd_rawmidi_write(handle, data, size):
        size = value(size)
        calls.append(("write", value(handle), ctypes.string_at(data, size), size))
        return size if write_rc is None else write_rc

    def snd_rawmidi_close(handle):
        calls.append(("close", value(handle)))
        return 0

    # input-buffer sizing through the params API (snd_rawmidi_params_*)
    params = {}

    def snd_rawmidi_params_malloc(ref):
        target(ref).value = 0x3333
        return 0

    def snd_rawmidi_params_current(handle, p):
        params["size"] = 4096
        return 0

    def snd_rawmidi_params_set_buffer_size(handle, p, size):
        params["size"] = value(size)
        return 0

    def snd_rawmidi_params(handle, p):
        calls.append(("input buffer", value(handle), params["size"]))
        return 0

    def snd_rawmidi_params_free(p):
        return None

    lib = SimpleNamespace(
        snd_rawmidi_open=snd_rawmidi_open, snd_rawmidi_nonblock=snd_rawmidi_nonblock,
        snd_rawmidi_read=snd_rawmidi_read, snd_rawmidi_write=snd_rawmidi_write,
        snd_rawmidi_close=snd_rawmidi_close,
        snd_rawmidi_params_malloc=snd_rawmidi_params_malloc, snd_rawmidi_params_current=snd_rawmidi_params_current,
        snd_rawmidi_params_set_buffer_size=snd_rawmidi_params_set_buffer_size, snd_rawmidi_params=snd_rawmidi_params,
        snd_rawmidi_params_free=snd_rawmidi_params_free,
    )
    return lib, calls


def test_rawmidi_port_drives_libasound_and_maps_each_return_code():
    # open: the device name, mode 0, the input made non-blocking, then its kernel buffer raised to the 1 MiB
    # maximum, so the ~80 KB dump after enable fits even while the reader is briefly starved (the 4 KB default
    # overran on the real 828: /proc/asound/card4/midi0 'Overruns: 4', 2026-10-12)
    lib, calls = fake_libasound(reads=[b"\xf0\x00\x00\x3b\x00\x01\x41\xf7", -11, -19])
    port = AlsaRawMidiPort(lib=lib)
    assert port.open(COM_PORT) is None
    assert calls == [("open", b"hw:4,0,1", 0), ("nonblock", IN_HANDLE, 1), ("input buffer", IN_HANDLE, 1 << 20)]

    # read: n bytes, nothing pending, unplugged
    assert port.read() == b"\xf0\x00\x00\x3b\x00\x01\x41\xf7"
    assert port.read() == b""
    with pytest.raises(PortGone):
        port.read()
    assert [c for c in calls if c[0] == "read"] == [("read", IN_HANDLE)] * 3

    # write: one call per whole frame on the output handle, its length accepted
    del calls[:]
    assert port.write(PROBE) is None
    assert port.write(ENABLE) is None
    assert calls == [("write", OUT_HANDLE, PROBE, len(PROBE)), ("write", OUT_HANDLE, ENABLE, len(ENABLE))]

    # close: each handle once, however often close is called
    del calls[:]
    port.close()
    port.close()
    assert sorted(calls) == [("close", IN_HANDLE), ("close", OUT_HANDLE)]

    # open answered -EBUSY: held by another client
    lib, calls = fake_libasound(open_rc=-16)
    with pytest.raises(PortBusy):
        AlsaRawMidiPort(lib=lib).open(COM_PORT)
    assert [c[0] for c in calls] == ["open"]

    # open answered any other negative errno: the device is absent
    for errno in (-2, -19):
        lib, calls = fake_libasound(open_rc=errno)
        with pytest.raises(PortGone):
            AlsaRawMidiPort(lib=lib).open(COM_PORT)
        assert [c[0] for c in calls] == ["open"]

    # write answered negative: the device is gone
    lib, calls = fake_libasound(write_rc=-19)
    port = AlsaRawMidiPort(lib=lib)
    port.open(COM_PORT)
    with pytest.raises(PortGone):
        port.write(PROBE)


LISTING_2026_10_12 = """Dir Device    Name
IO  hw:1,0,0  UM-ONE MIDI 1
IO  hw:4,0,0  828 MIDI
IO  hw:4,0,1  828 COM
IO  hw:5,0,0  RD-2000 MIDI 1
"""


def amidi_runner(answer):
    """Stands in for running amidi: records the command and its timeout, then gives the canned listing or failure."""
    asked = []

    def run(args, **kwargs):
        asked.append((list(args), kwargs.get("timeout")))
        if isinstance(answer, BaseException):
            raise answer
        stdout, returncode = answer
        if not (kwargs.get("text") or kwargs.get("encoding") or kwargs.get("universal_newlines")):
            stdout = stdout.encode()
        return SimpleNamespace(args=args, returncode=returncode, stdout=stdout, stderr="" if isinstance(stdout, str) else b"")

    return run, asked


def test_amidi_locator_finds_the_device_column_of_the_first_line_named_exactly_as_asked():
    cases = {
        "the listing of 2026-10-12": ((LISTING_2026_10_12, 0), PortAddress("hw:4,0,1")),
        "the unit moved to card 5": (
            ("Dir Device    Name\nIO  hw:4,0,0  RD-2000 MIDI 1\nIO  hw:5,0,0  828 MIDI\nIO  hw:5,0,1  828 COM\n", 0),
            PortAddress("hw:5,0,1"),
        ),
        "only the DIN port": (("Dir Device    Name\nIO  hw:4,0,0  828 MIDI\n", 0), None),
        "a longer name is not the same name": (
            ("Dir Device    Name\nIO  hw:3,0,1  828 COM 2\nIO  hw:4,0,1  828 COM  \n", 0),
            PortAddress("hw:4,0,1"),
        ),
        "two units": (
            ("Dir Device    Name\nIO  hw:4,0,1  828 COM\nIO  hw:6,0,1  828 COM\n", 0),
            PortAddress("hw:4,0,1"),
        ),
        "amidi failing": (("", 1), None),
        "amidi not installed": (FileNotFoundError("amidi"), None),
        "amidi hanging": (subprocess.TimeoutExpired(["amidi", "-l"], 5), None),
    }
    for case, (answer, expected) in cases.items():
        run, asked = amidi_runner(answer)
        assert AmidiPortLocator(runner=run).locate("828 COM") == expected, case
        assert asked == [(["amidi", "-l"], 5)], case


CAPTURE = Path(__file__).resolve().parents[2] / "fixtures" / "828-property-dump-2026-10-12.hex"
PROBE_REPLY = bytes.fromhex("f0 00 00 3b 00 01 41 f7")
FADER_643_MINUS_6_DB = bytes.fromhex("f0 00 00 3b 00 01 00 0a 03 78 02 03 00 04 00 05 00 4d 4e f7")


def chunks_of(data, size):
    return [data[i:i + size] for i in range(0, len(data), size)]


def test_rawmidi_port_carries_the_units_conversation_byte_for_byte():
    capture = bytes.fromhex(CAPTURE.read_text())
    assert capture.startswith(PROBE_REPLY)
    dump = capture[len(PROBE_REPLY):]
    cell = Address(1016, 643)
    reported_minus_6 = encode_report(cell, Gain.from_db(-6.0).to_824().to_bytes(4, "big", signed=True))
    burst = [encode_set(cell, Gain.from_db(-60.0 + step)) for step in range(50)]
    reported_final = encode_report(cell, Gain.from_db(-11.0).to_824().to_bytes(4, "big", signed=True))
    dump_chunks = chunks_of(dump, 4096)

    lib, calls = fake_libasound(reads=[PROBE_REPLY, *dump_chunks, *dump_chunks, reported_minus_6, reported_final])
    port = AlsaRawMidiPort(lib=lib)
    port.open(COM_PORT)

    def received(count):
        return b"".join(port.read() for _ in range(count))

    # probe, one reply
    port.write(PROBE)
    assert port.read() == PROBE_REPLY

    # enable, the full dump; enable again, the full dump again
    port.write(ENABLE)
    assert received(len(dump_chunks)) == dump
    port.write(ENABLE)
    assert received(len(dump_chunks)) == dump

    # a SetProperty frame with its len field, then the unit's report of the new value
    port.write(FADER_643_MINUS_6_DB)
    assert port.read() == reported_minus_6

    # a burst of 50 writes to one cell, then the unit's report of the final value
    for frame in burst:
        port.write(frame)
    assert port.read() == reported_final

    written = [c[2] for c in calls if c[0] == "write"]
    assert written == [PROBE, ENABLE, ENABLE, FADER_643_MINUS_6_DB, *burst]
    assert [c[0] for c in calls if c[0] in ("write", "read")] == (
        ["write", "read", "write"] + ["read"] * len(dump_chunks) + ["write"] + ["read"] * len(dump_chunks)
        + ["write", "read"] + ["write"] * 50 + ["read"]
    )


def test_locator_finds_828_com_by_name_whatever_card_it_is_on():
    moved_to_card_5 = (
        "Dir Device    Name\n"
        "IO  hw:1,0,0  UM-ONE MIDI 1\n"
        "IO  hw:4,0,0  RD-2000 MIDI 1\n"
        "IO  hw:5,0,0  828 MIDI\n"
        "IO  hw:5,0,1  828 COM\n"
    )
    two_units = "Dir Device    Name\nIO  hw:4,0,0  828 MIDI\nIO  hw:4,0,1  828 COM\nIO  hw:6,0,0  828 MIDI\nIO  hw:6,0,1  828 COM\n"
    cases = [
        ((LISTING_2026_10_12, 0), "828 COM", PortAddress("hw:4,0,1")),
        ((moved_to_card_5, 0), "828 COM", PortAddress("hw:5,0,1")),
        ((two_units, 0), "828 COM", PortAddress("hw:4,0,1")),
        ((moved_to_card_5, 0), "828 MIDI", PortAddress("hw:5,0,0")),
        (("Dir Device    Name\nIO  hw:1,0,0  UM-ONE MIDI 1\n", 0), "828 COM", None),
        (("", 1), "828 COM", None),
        (FileNotFoundError("amidi"), "828 COM", None),
        (subprocess.TimeoutExpired(["amidi", "-l"], 5), "828 COM", None),
    ]
    for answer, name, expected in cases:
        run, asked = amidi_runner(answer)
        locator = AmidiPortLocator(runner=run)
        assert locator.locate(name) == expected, (name, expected)
        assert asked == [(["amidi", "-l"], 5)]


def test_port_maps_each_libasound_answer_into_open_read_write_close():
    lib, calls = fake_libasound(reads=[b"\xf0\x00", -11, b"\x00\x3b\x00\x01\x41\xf7", -11, -19])
    port = AlsaRawMidiPort(lib=lib)
    assert port.open(PortAddress("hw:5,0,1")) is None
    assert calls == [("open", b"hw:5,0,1", 0), ("nonblock", IN_HANDLE, 1), ("input buffer", IN_HANDLE, 1 << 20)]

    # the pump reads from its own thread until the port is gone
    got = []

    def pump():
        try:
            while True:
                got.append(port.read())
        except PortGone:
            got.append("gone")

    reader = threading.Thread(target=pump)
    reader.start()
    reader.join(timeout=2)
    assert got == [b"\xf0\x00", b"", b"\x00\x3b\x00\x01\x41\xf7", b"", "gone"]

    # one whole frame per write call
    del calls[:]
    frame = encode_set(Address(1016, 643), Gain.from_db(-6.0))
    assert port.write(frame) is None
    assert calls == [("write", OUT_HANDLE, frame, len(frame))]

    del calls[:]
    port.close()
    port.close()
    assert sorted(calls) == [("close", IN_HANDLE), ("close", OUT_HANDLE)]

    # a busy or vanished port: the session may still close it, which touches nothing
    for rc, raised in ((-16, PortBusy), (-19, PortGone), (-2, PortGone)):
        lib, calls = fake_libasound(open_rc=rc)
        port = AlsaRawMidiPort(lib=lib)
        with pytest.raises(raised):
            port.open(COM_PORT)
        port.close()
        assert [c[0] for c in calls] == ["open"]

    # a write after unplug
    lib, calls = fake_libasound(write_rc=-19)
    port = AlsaRawMidiPort(lib=lib)
    port.open(COM_PORT)
    with pytest.raises(PortGone):
        port.write(frame)


def test_port_passes_the_units_stream_through_unchanged_in_any_chunking():
    capture = bytes.fromhex(CAPTURE.read_text())
    meter = encode_report(Address(6000, 0), b"\x00\x10")
    panel_change = encode_report(Address(1016, 2), Gain.from_db(-10.0).to_824().to_bytes(4, "big", signed=True))
    echo = encode_report(Address(1016, 643), Gain.from_db(-6.0).to_824().to_bytes(4, "big", signed=True))
    cut_short = encode_report(Address(1016, 2), b"\x01\x00\x00\x00")[:-3]
    stream = capture + meter * 16 + panel_change + cut_short + echo + meter

    rng = random.Random(828)
    chunks, start = [], 0
    while start < len(stream):
        size = rng.choice([1, 3, 7, 64, 500, 2048, 65536])
        chunks.append(stream[start:start + size])
        start += size
    assert any(chunk[-1] != 0xF7 for chunk in chunks)

    lib, calls = fake_libasound(reads=[x for chunk in chunks for x in (chunk, -11)])
    port = AlsaRawMidiPort(lib=lib)
    port.open(COM_PORT)
    received = b"".join(port.read() for _ in range(2 * len(chunks)))

    assert received == stream
    assert not [c for c in calls if c[0] == "write"]


def test_port_writes_the_session_frames_and_returns_the_units_answers():
    capture = bytes.fromhex(CAPTURE.read_text())
    reply, dump = capture[:len(PROBE_REPLY)], capture[len(PROBE_REPLY):]
    dump_chunks = chunks_of(dump, 65536)
    lib, calls = fake_libasound(reads=[reply, *dump_chunks])
    port = AlsaRawMidiPort(lib=lib)
    port.open(COM_PORT)

    port.write(probe_frame())
    assert port.read() == PROBE_REPLY
    port.write(enable_frame())
    assert b"".join(port.read() for _ in dump_chunks) == dump
    port.write(FADER_643_MINUS_6_DB)

    assert [c[2] for c in calls if c[0] == "write"] == [PROBE, ENABLE, FADER_643_MINUS_6_DB]


def unit_sessions(*reads_per_session):
    """Stands in for the 828 behind libasound: each connect is a fresh session answering with its canned reads."""
    pending, sessions = list(reads_per_session), []

    def connect():
        lib, calls = fake_libasound(reads=pending.pop(0))
        sessions.append(calls)
        port = AlsaRawMidiPort(lib=lib)
        port.open(COM_PORT)
        return port

    def written():
        return [[c[2] for c in calls if c[0] == "write"] for calls in sessions]

    return connect, written


def dump_reporting(address, raw):
    """A dump fragment: another cell first, then the asked cell split across two reads."""
    other = encode_report(Address(1016, 2), b"\x01\x00\x00\x00")
    wanted = encode_report(address, raw)
    return [PROBE_REPLY + other + wanted[:5], wanted[5:]]


def test_mute_write_check_sets_reads_back_and_restores_the_cell():
    cell = Address(1019, 643)
    connect, written = unit_sessions(
        dump_reporting(cell, b"\x00"), [], dump_reporting(cell, b"\x01"), [], dump_reporting(cell, b"\x00"),
    )
    handlers = signal.getsignal(signal.SIGINT), signal.getsignal(signal.SIGTERM)

    report = run_write_check("W2", "mute byte path verified", connect, cell, 1, sleep=lambda s: None)

    assert report == "W2 passed: mute byte path verified"
    mute_on = bytes.fromhex("f0 00 00 3b 00 01 00 0a 03 7b 02 03 00 01 01 f7")
    assert written() == [[PROBE, ENABLE], [mute_on], [PROBE, ENABLE], [encode_set(cell, 0)], [PROBE, ENABLE]]
    assert (signal.getsignal(signal.SIGINT), signal.getsignal(signal.SIGTERM)) == handlers

    # a cell that does not read back as written fails the check, and is still restored
    connect, written = unit_sessions(
        dump_reporting(cell, b"\x00"), [], dump_reporting(cell, b"\x00"), [], dump_reporting(cell, b"\x00"),
    )
    assert run_write_check("W2", "mute byte path verified", connect, cell, 1, sleep=lambda s: None) == (
        "W2 failed: mute byte path verified"
    )
    assert written()[3] == [encode_set(cell, 0)]


def test_write_check_interrupted_after_its_write_still_restores_the_cell():
    cell = Address(1016, 643)
    silent = bytes(4)
    connect, written = unit_sessions(dump_reporting(cell, silent), [], [], dump_reporting(cell, silent))
    handlers = signal.getsignal(signal.SIGINT), signal.getsignal(signal.SIGTERM)

    def terminated_while_settling(seconds):
        signal.raise_signal(signal.SIGTERM)

    with pytest.raises(KeyboardInterrupt):
        run_write_check("W1", "fader path verified", connect, cell, Gain.from_db(-6.0), sleep=terminated_while_settling)

    assert written() == [[PROBE, ENABLE], [FADER_643_MINUS_6_DB], [encode_set(cell, 0.0)]]
    assert read_cell(connect, cell) == silent
    assert (signal.getsignal(signal.SIGINT), signal.getsignal(signal.SIGTERM)) == handlers


def test_write_check_refuses_a_cell_outside_the_spec_list_before_sending_anything():
    line_5_on_ana_1 = Address(1016, MixCell(BUS_NAMES.index("Ana 1"), INPUT_NAMES.index("Line 5")).index)
    connected = []

    with pytest.raises(CellNotAllowed, match="cell not in PROTOCOL-SPEC §9"):
        run_write_check("W1", "fader path verified", lambda: connected.append(1), line_5_on_ana_1, Gain.from_db(-6.0))

    assert connected == []
