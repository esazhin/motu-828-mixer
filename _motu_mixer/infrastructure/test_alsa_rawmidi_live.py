"""Checks against the real 828 on '828 COM'. Opt-in: MOTU_LIVE=1 python -m pytest -m live, with the service stopped or released.

Every write goes to a cell in LIVE_ALLOWED and is restored on the way out, whether the check passes, fails or is interrupted.
"""
import os
import signal
import time

import pytest

from _motu_mixer.domain.values import Address, Gain, Report
from _motu_mixer.infrastructure.alsa_rawmidi import (
    AlsaRawMidiPort, AmidiPortLocator, CellNotAllowed, read_cell, run_write_check, snapshot_restore,
)
from _motu_mixer.protocol.sysex import ProbeReply, SysexSplitter, decode, enable_frame, encode_set, probe_frame

pytestmark = [
    pytest.mark.live,
    pytest.mark.skipif(os.environ.get("MOTU_LIVE") != "1", reason="needs the real 828 (MOTU_LIVE=1)"),
]

FADER_643 = Address(1016, 643)
MUTE_643 = Address(1019, 643)


@pytest.fixture
def connect():
    addr = AmidiPortLocator().locate("828 COM")
    if addr is None:
        pytest.skip("no '828 COM' listed by amidi -l")

    def open_port():
        port = AlsaRawMidiPort()
        port.open(addr)
        return port

    return open_port


def reports_for(port, seconds):
    splitter, frames, deadline = SysexSplitter(), [], time.monotonic() + seconds
    while time.monotonic() < deadline:
        chunk = port.read()
        if not chunk:
            time.sleep(0.001)
        frames += [decode(frame) for frame in splitter.feed(chunk)]
    return frames


def test_live_probe_gets_one_reply_within_a_second(connect):
    port = connect()
    try:
        port.write(probe_frame())
        assert [m for m in reports_for(port, 1.0) if isinstance(m, ProbeReply)] == [ProbeReply()]
    finally:
        port.close()


def test_live_enable_dumps_every_cell(connect):
    port = connect()
    try:
        port.write(probe_frame())
        port.write(enable_frame())
        cells = {m.address for m in reports_for(port, 4.0) if isinstance(m, Report)}
        assert len(cells) >= 3796
    finally:
        port.close()


@pytest.mark.parametrize("name, proves, address, value", [
    ("W1", "fader path verified", FADER_643, Gain.from_db(-6.0)),
    ("W2", "mute byte path verified", MUTE_643, 1),
    ("W3", "bus master fader path verified", Address(1027, 15), Gain.from_db(-6.0)),
    ("W3", "bus master mute path verified", Address(1028, 15), 1),
    ("W5", "routing write path verified", Address(5010, 40), 127),
])
def test_live_write_check(connect, name, proves, address, value):
    report = run_write_check(name, proves, connect, address, value)
    print(report)
    assert report == f"{name} passed: {proves}"


def test_live_echo_of_a_host_write_is_recorded(connect):
    with snapshot_restore(connect, FADER_643):
        port = connect()
        try:
            port.write(probe_frame())
            port.write(enable_frame())
            reports_for(port, 4.0)
            port.write(encode_set(FADER_643, Gain.from_db(-6.0)))
            echoed = any(isinstance(m, Report) and m.address == FADER_643 for m in reports_for(port, 1.0))
        finally:
            port.close()
    print(f"echo of host writes: {'yes' if echoed else 'no'}")


def test_live_burst_of_50_writes_ends_at_the_final_value(connect):
    with snapshot_restore(connect, FADER_643):
        port = connect()
        try:
            for step in range(50):
                port.write(encode_set(FADER_643, Gain.from_db(-55.0 + step)))
                time.sleep(0.02)
        finally:
            port.close()
        time.sleep(0.5)
        final = read_cell(connect, FADER_643)
    assert final == Gain.from_db(-6.0).to_824().to_bytes(4, "big", signed=True)


def test_live_cell_outside_the_spec_list_is_refused_before_anything_is_sent():
    def never_connect():
        raise AssertionError("nothing may be sent")

    with pytest.raises(CellNotAllowed, match="cell not in PROTOCOL-SPEC §9"):
        run_write_check("W1", "fader path verified", never_connect, Address(1016, 2 * 44 + 4), Gain.from_db(-6.0))


def test_live_interrupted_write_check_restores_the_cell(connect):
    before = read_cell(connect, FADER_643)

    def interrupted(seconds):
        signal.raise_signal(signal.SIGINT)

    with pytest.raises(KeyboardInterrupt):
        run_write_check("W1", "fader path verified", connect, FADER_643, Gain.from_db(-6.0), sleep=interrupted)
    time.sleep(0.5)
    assert read_cell(connect, FADER_643) == before
