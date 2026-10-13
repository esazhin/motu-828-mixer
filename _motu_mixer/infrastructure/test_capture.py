from pathlib import Path

from _motu_mixer.infrastructure.capture import parse_amidi_hex
from _motu_mixer.protocol.sysex import SysexSplitter

CAPTURE = Path(__file__).resolve().parents[2] / "fixtures" / "828-property-dump-2026-10-12.hex"
PROBE_REPLY = bytes.fromhex("f0 00 00 3b 00 01 41 f7")


def test_parser_returns_every_frame_of_a_capture_in_file_order_skipping_blank_lines():
    text = "\nF0 00 00 3B 00 01 41 F7\n\n  \nF0 00 00 3B 00 01 40 00 01 00 00 00 00 F7\n"
    assert parse_amidi_hex(text) == PROBE_REPLY + bytes.fromhex("f0 00 00 3b 00 01 40 00 01 00 00 00 00 f7")


def test_recorded_capture_parses_to_the_probe_reply_then_the_full_dump():
    text = CAPTURE.read_text()
    frames = SysexSplitter().feed(parse_amidi_hex(text))
    assert len(frames) == 5971
    assert frames[0] == PROBE_REPLY
    assert frames == [bytes.fromhex(line) for line in text.splitlines() if line.strip()]
