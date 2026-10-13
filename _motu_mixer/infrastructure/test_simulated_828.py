from pathlib import Path

import pytest

from _motu_mixer.application.ports import PortBusy, PortGone
from _motu_mixer.domain.values import Address, Gain, PortAddress, Report
from _motu_mixer.infrastructure.capture import parse_amidi_hex
from _motu_mixer.infrastructure.simulated_828 import BUSY, Simulated828Port, gone_after
from _motu_mixer.protocol.sysex import (
    ENABLE, OTHER, PROBE, SetRequest, SysexSplitter, decode, enable_frame, encode_report, encode_set, probe_frame,
)

PROBE_REPLY = bytes.fromhex("f0 00 00 3b 00 01 41 f7")
DUMP = b"\xf0dump frame one\xf7\xf0dump frame two\xf7"
ADAT_B_8_REVERB_1 = Address(1016, 643)
MINUS_6_DB = bytes.fromhex("00804dce")
ECHO = b"\xf0echo of 1016/643\xf7"


def drain(port):
    received = b""
    while chunk := port.read():
        received += chunk
    return received


def test_simulator_answers_each_host_frame_as_the_codec_classifies_it():
    classified, reported = [], []
    kinds = {
        b"probe": PROBE,
        b"enable": ENABLE,
        b"set 1016/643": SetRequest(ADAT_B_8_REVERB_1, MINUS_6_DB),
        b"foreign": OTHER,
    }

    def classify_host_frame(frame):
        classified.append(frame)
        return kinds[frame]

    def encode_report(address, raw):
        reported.append((address, raw))
        return ECHO

    def simulator(echo):
        port = Simulated828Port(
            DUMP, echo=echo, classify_host_frame=classify_host_frame, encode_report=encode_report,
        )
        port.open(PortAddress("hw:4,0,1"))
        return port

    port = simulator(echo=True)

    # the probe is answered with the probe reply, enable with the capture
    port.write(b"probe")
    assert drain(port) == PROBE_REPLY
    port.write(b"enable")
    assert drain(port) == DUMP

    # a set updates the table and, with echo on, is echoed through the codec
    port.write(b"set 1016/643")
    assert port.table[ADAT_B_8_REVERB_1] == MINUS_6_DB
    assert reported == [(ADAT_B_8_REVERB_1, MINUS_6_DB)]
    assert drain(port) == ECHO

    # anything else is ignored
    port.write(b"foreign")
    assert drain(port) == b""
    assert classified == [b"probe", b"enable", b"set 1016/643", b"foreign"]

    # with echo off a set still updates the table, but nothing comes back
    reported.clear()
    quiet = simulator(echo=False)
    quiet.write(b"set 1016/643")
    assert quiet.table[ADAT_B_8_REVERB_1] == MINUS_6_DB
    assert drain(quiet) == b""
    assert reported == []


def test_simulator_built_from_a_capture_text_replays_the_parsed_bytes_on_enable():
    from _motu_mixer.protocol.sysex import enable_frame, probe_frame

    capture_text = "F0 00 00 3B 00 01 41 F7\n\nF0 00 00 3B 00 01 40 00 00 00 00 00 00 F7\n"
    two_frames = bytes.fromhex("f0 00 00 3b 00 01 40 00 00 00 00 00 00 f7 f0 00 00 3b 00 01 40 00 01 00 00 00 00 f7")
    parsed = []

    def parse_amidi_hex(text):
        parsed.append(text)
        return two_frames

    port = Simulated828Port.from_capture_text(capture_text, echo=False, parse_amidi_hex=parse_amidi_hex)
    port.open(PortAddress("hw:4,0,1"))

    # the capture's text goes to the parser as given
    assert parsed == [capture_text]

    # enable replays exactly the bytes the parser returned
    port.write(probe_frame())
    assert drain(port) == PROBE_REPLY
    port.write(enable_frame())
    assert drain(port) == two_frames


CAPTURE = Path(__file__).resolve().parents[2] / "fixtures" / "828-property-dump-2026-10-12.hex"
HW_4_0_1 = PortAddress("hw:4,0,1")


def recorded_simulator(**options):
    port = Simulated828Port.from_capture_text(CAPTURE.read_text(), **options)
    port.open(HW_4_0_1)
    return port


def test_recorded_capture_answers_probe_enable_and_writes_with_each_fault():
    set_minus_6_db = encode_set(ADAT_B_8_REVERB_1, Gain.from_db(-6.0))

    # without echo: probe reply, the 5,970-frame dump, then a write lands in the table silently
    port = recorded_simulator(echo=False)
    port.write(probe_frame())
    assert drain(port) == PROBE_REPLY
    port.write(enable_frame())
    dump = SysexSplitter().feed(drain(port))
    assert len(dump) == 5970
    assert all(isinstance(decode(frame), Report) for frame in dump)
    port.write(set_minus_6_db)
    assert port.table[ADAT_B_8_REVERB_1] == MINUS_6_DB
    assert drain(port) == b""

    # with echo the unit reports the written value back
    echoing = recorded_simulator(echo=True)
    echoing.write(set_minus_6_db)
    assert [decode(f) for f in SysexSplitter().feed(drain(echoing))] == [Report(ADAT_B_8_REVERB_1, MINUS_6_DB)]

    # a port held elsewhere refuses to open
    busy = Simulated828Port.from_capture_text(CAPTURE.read_text(), fault=BUSY)
    with pytest.raises(PortBusy):
        busy.open(HW_4_0_1)

    # an unplug after n bytes: exactly n bytes arrive, then the port is gone for reads and writes
    unplugged = recorded_simulator(fault=gone_after(100))
    unplugged.write(enable_frame())
    assert len(unplugged.read()) == 100
    with pytest.raises(PortGone):
        unplugged.read()
    with pytest.raises(PortGone):
        unplugged.write(probe_frame())


def test_simulator_echoes_a_set_through_the_codec_only_when_echo_is_on():
    def classify_host_frame(frame):
        return SetRequest(ADAT_B_8_REVERB_1, MINUS_6_DB)

    echoes = []

    def encode_report(address, raw):
        echoes.append((address, raw))
        return ECHO

    for echo, expected in ((True, ECHO), (False, b"")):
        port = Simulated828Port(DUMP, echo=echo, classify_host_frame=classify_host_frame, encode_report=encode_report)
        port.open(HW_4_0_1)
        port.write(b"any set frame")
        assert port.table == {ADAT_B_8_REVERB_1: MINUS_6_DB}
        assert drain(port) == expected
    assert echoes == [(ADAT_B_8_REVERB_1, MINUS_6_DB)]


def test_simulator_from_capture_text_replays_the_dump_without_the_recorded_probe_reply():
    dump = b"\xf0frame\xf7"
    port = Simulated828Port.from_capture_text(
        "the capture text", parse_amidi_hex=lambda text: PROBE_REPLY + dump,
        classify_host_frame=lambda frame: ENABLE,
    )
    port.open(HW_4_0_1)
    port.write(b"enable")
    assert drain(port) == dump


def reaches_the_codec():
    port = recorded_simulator(echo=True)
    port.write(encode_set(ADAT_B_8_REVERB_1, Gain.from_db(-6.0)))
    assert drain(port) == encode_report(ADAT_B_8_REVERB_1, MINUS_6_DB)


def reaches_the_capture_parser():
    port = recorded_simulator()
    port.write(enable_frame())
    assert drain(port) == parse_amidi_hex(CAPTURE.read_text())[len(PROBE_REPLY):]


@pytest.mark.parametrize("supplier", [
    pytest.param(reaches_the_codec, id="codec"),
    pytest.param(reaches_the_capture_parser, id="capture_parser"),
])
def test_simulator_from_the_recorded_capture_is_wired_to(supplier):
    supplier()
