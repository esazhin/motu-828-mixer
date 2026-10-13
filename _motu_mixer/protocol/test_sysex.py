import random
from pathlib import Path

import pytest

from _motu_mixer.domain.properties import PropertySpec, ValueType
from _motu_mixer.domain.values import Address, Gain, Pan, Report, Source
from _motu_mixer.protocol.sysex import (
    ENABLE, OTHER, PROBE, Ignored, ProbeReply, SetRequest, SysexSplitter, classify_host_frame, decode,
    decode_value, enable_frame, encode_report, encode_set, pack7, probe_frame, unpack7,
)

TEST_ID = 4242  # 0x1092


class SpecTable:
    """Stands in for the property table: answers spec(id) from known specs and records every id asked."""

    def __init__(self, specs):
        self.specs = specs
        self.asked = []

    def spec(self, prop_id):
        self.asked.append(prop_id)
        return self.specs.get(prop_id)


def test_value_encoding_follows_the_type_the_property_table_gives():
    table = SpecTable({TEST_ID: PropertySpec(ValueType.BYTE, 4, True)})

    # typed BYTE: the value goes out as one byte with len 1 -> body 10 92 00 03 00 01 01, packed
    frame = encode_set(Address(TEST_ID, 3), True, spec=table.spec)
    assert frame == bytes.fromhex("f0 00 00 3b 00 01 00 02 10 12 00 03 00 01 01 f7")
    assert table.asked == [TEST_ID]

    table.asked.clear()
    assert decode_value(TEST_ID, b"\x01", spec=table.spec) == 1
    assert table.asked == [TEST_ID]

    # an id the table does not know cannot be encoded
    with pytest.raises(ValueError):
        encode_set(Address(6000, 0), True, spec=table.spec)


CAPTURE = Path(__file__).resolve().parents[2] / "fixtures" / "828-property-dump-2026-10-12.hex"
PROBE_REPLY = bytes.fromhex("f0 00 00 3b 00 01 41 f7")
A2_FADER_FRAME = bytes.fromhex("f0 00 00 3b 00 01 00 0a 03 78 02 03 00 04 00 05 00 4d 4e f7")
A3_MUTE_FRAME = bytes.fromhex("f0 00 00 3b 00 01 00 0a 03 7b 02 03 00 01 01 f7")
LINE_3_MAIN_1_FADER = bytes.fromhex("f0 00 00 3b 00 01 40 02 03 78 00 02 01 00 00 00 00 f7")


def capture_bytes():
    return bytes.fromhex(CAPTURE.read_text())


def split_in_chunks(data, sizes):
    splitter, frames, start = SysexSplitter(), [], 0
    sizes = iter(sizes)
    while start < len(data):
        size = next(sizes)
        frames += splitter.feed(data[start:start + size])
        start += size
    return frames


def test_capture_splits_and_decodes_to_the_units_known_settings():
    frames = split_in_chunks(capture_bytes(), iter(lambda: 2048, None))
    assert len(frames) == 5971
    decoded = [decode(frame) for frame in frames]
    assert decoded[0] == ProbeReply()
    assert all(isinstance(d, Report) for d in decoded[1:])

    latest = {d.address: d.raw for d in decoded[1:]}

    def value(prop, index):
        return decode_value(prop, latest[Address(prop, index)])

    assert value(21, 0) == "828E"
    assert value(2, 0) == "828E000000"
    assert value(8, 0) == 9
    # Line 3 on Main 1 (cell 2): 0.0 dB, hard left
    assert value(1016, 2) == 1.0
    assert value(1017, 2) == 0.0
    # Line 7 on Ana 1 (bus 2, input 6 = cell 94): muted
    assert value(1019, 2 * 44 + 6) == 1
    # S/PDIF out L/R fed by the Ana 1-2 mix
    assert (value(5010, 58), value(5010, 59)) == (78, 79)
    assert [value(1001, b) for b in range(16)] == [1, 0, 1, 0, 0, 0, 0, 0, 1, 0, 1, 0, 1, 0, 1, 0]


def test_splitter_reassembles_chunks_of_any_size_and_drops_broken_frames():
    data = capture_bytes()
    whole = SysexSplitter().feed(data)
    rng = random.Random(828)
    assert split_in_chunks(data, iter(lambda: rng.randint(1, 300), None)) == whole
    assert split_in_chunks(data, iter(lambda: 1, None)) == whole

    # a frame split across two chunks comes out once, whole
    splitter = SysexSplitter()
    assert splitter.feed(LINE_3_MAIN_1_FADER[:9]) == []
    assert splitter.feed(LINE_3_MAIN_1_FADER[9:]) == [LINE_3_MAIN_1_FADER]

    # a truncated frame with no end byte, then a valid one: only the valid one
    minus_3_db = encode_report(Address(1016, 2), Gain.from_db(-3.0).to_824().to_bytes(4, "big"))
    splitter = SysexSplitter()
    assert splitter.feed(LINE_3_MAIN_1_FADER[:12] + minus_3_db) == [minus_3_db]
    report = decode(minus_3_db)
    assert report.address == Address(1016, 2)
    assert abs(decode_value(1016, report.raw) - Gain.from_db(-3.0).linear) < 1e-6

    # bytes arriving mid-frame, before any F0, are dropped; frames keep their order
    assert SysexSplitter().feed(LINE_3_MAIN_1_FADER[5:] + PROBE_REPLY + A3_MUTE_FRAME) == [
        PROBE_REPLY, A3_MUTE_FRAME,
    ]


def test_decode_tells_reports_probe_replies_and_everything_else_apart():
    assert decode(LINE_3_MAIN_1_FADER) == Report(Address(1016, 2), bytes.fromhex("01000000"))
    assert decode(PROBE_REPLY) == ProbeReply()
    # another manufacturer, a host-to-device frame, and a body too short to address anything
    assert decode(bytes.fromhex("f0 7e 7f 06 02 f7")) == Ignored()
    assert decode(A2_FADER_FRAME) == Ignored()
    assert decode(bytes.fromhex("f0 00 00 3b 00 01 40 00 03 f7")) == Ignored()


def test_encode_set_builds_the_verified_host_frames():
    assert encode_set(Address(1016, 643), Gain.from_db(-6.0)) == A2_FADER_FRAME
    assert encode_set(Address(1019, 643), True) == A3_MUTE_FRAME
    # the len field counts the value bytes: pan 4, source 1, string 32 NUL-padded
    for address, value, raw in [
        (Address(1017, 2), Pan(0.5), bytes.fromhex("00800000")),
        (Address(5010, 58), Source(78), bytes([78])),
        (Address(3, 0), "828", b"828".ljust(32, b"\0")),
    ]:
        body = unpack7(encode_set(address, value)[7:-1])
        assert body[4:6] == len(raw).to_bytes(2, "big")
        assert body[6:] == raw
    assert all(b < 0x80 for b in A2_FADER_FRAME[1:-1])


def test_pack7_and_unpack7_are_inverse():
    rng = random.Random(7)
    for _ in range(1000):
        data = bytes(rng.randrange(256) for _ in range(rng.randrange(40)))
        packed = pack7(data)
        assert all(b < 0x80 for b in packed)
        assert unpack7(packed) == data


def test_probe_and_enable_frames_are_the_protocol_requests():
    assert probe_frame() == bytes.fromhex("f0 00 00 3b 00 01 01 f7")
    assert enable_frame() == bytes.fromhex("f0 00 00 3b 00 01 02 f7")


def test_host_frames_classify_and_reports_round_trip_for_the_simulator():
    assert classify_host_frame(probe_frame()) == PROBE
    assert classify_host_frame(enable_frame()) == ENABLE
    assert classify_host_frame(A2_FADER_FRAME) == SetRequest(Address(1016, 643), bytes.fromhex("00804dce"))
    assert classify_host_frame(encode_set(Address(1019, 643), True)) == SetRequest(Address(1019, 643), b"\x01")
    for junk in (PROBE_REPLY, LINE_3_MAIN_1_FADER, bytes.fromhex("f0 7e 7f 06 01 f7"), b"\xf0\xf7", b""):
        assert classify_host_frame(junk) == OTHER

    for address, raw in [
        (Address(1016, 643), bytes.fromhex("00804dce")),
        (Address(1019, 643), b"\x01"),
        (Address(21, 0), b"828E\0"),
    ]:
        echo = encode_report(address, raw)
        assert echo[6] == 0x40
        assert decode(echo) == Report(address, raw)


def test_values_encode_and_decode_by_each_type_the_table_gives():
    table = SpecTable({
        1: PropertySpec(ValueType.F824, 1, True),
        2: PropertySpec(ValueType.BYTE, 1, True),
        3: PropertySpec(ValueType.I16, 1, True),
        4: PropertySpec(ValueType.I32, 1, True),
        5: PropertySpec(ValueType.STRING, 1, True),
    })
    cases = [
        (1, Gain(4.0), bytes.fromhex("04000000"), 4.0),
        (2, Source(127), b"\x7f", 127),
        (3, 9, b"\x00\x09", 9),
        (4, -2, bytes.fromhex("fffffffe"), -2),
        (5, "828E", b"828E".ljust(32, b"\0"), "828E"),
    ]
    for prop, value, raw, decoded in cases:
        body = unpack7(encode_set(Address(prop, 0), value, spec=table.spec)[7:-1])
        assert body[6:] == raw
        assert decode_value(prop, raw, spec=table.spec) == decoded
    assert table.asked == [p for p, *_ in cases for _ in range(2)]
