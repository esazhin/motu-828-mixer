from pathlib import Path

from _motu_mixer.domain.meters import BUS_METER_INDEX, INPUT_METER_INDEX, METERS_PROP, MeterLevels, meter_levels
from _motu_mixer.domain.topology import LOCAL_BASE, LOCAL_INPUTS
from _motu_mixer.infrastructure.capture import parse_amidi_hex
from _motu_mixer.protocol.sysex import Report, SysexSplitter, decode

FIXTURE = Path(__file__).resolve().parents[2] / "fixtures" / "828-property-dump-2026-10-12.hex"


def _captured_meter_frames():
    frames = SysexSplitter().feed(parse_amidi_hex(FIXTURE.read_text()))
    reports = [decode(f) for f in frames]
    return [r.raw for r in reports if isinstance(r, Report) and r.address.prop == METERS_PROP]


def test_the_table_names_one_meter_per_mixer_input_and_per_bus():
    assert len(INPUT_METER_INDEX) == 44 and len(BUS_METER_INDEX) == 16
    assert INPUT_METER_INDEX[0:10] == tuple(range(92, 102))     # Mic 1-2, Line 3-10: post-FX taps
    assert INPUT_METER_INDEX[10:12] == (58, 59)                  # S/PDIF in
    assert INPUT_METER_INDEX[28:42] == tuple(range(0, 14))       # Computer 1-14
    assert INPUT_METER_INDEX[42:44] == (56, 57)                  # reverb return
    assert BUS_METER_INDEX[0:14] == tuple(range(76, 90))         # each bus's mix output


def test_on_the_real_capture_the_post_fx_taps_track_their_own_raw_inputs_and_no_other():
    # The real 828's own meter frames (property 6000, 128 bytes). The table meters Mic 1 - Line 10 at the post-FX
    # tap (what CueMix's strips show). Post-FX includes the input dynamics, so it may sit below the raw input: in
    # this capture Line 7/8 read a steady 2.5 dB under their raw taps in some frames. Measured: the right table
    # disagrees by more than one step in 123 of 21,750 comparisons (0.6 %); shifted by one channel, in 14,967 (69 %).
    frames = _captured_meter_frames()
    assert frames and {len(f) for f in frames} == {128}
    total = len(frames) * 10

    def disagreements(index_of):
        return sum(abs(raw[index_of(i)] - raw[LOCAL_BASE + LOCAL_INPUTS[i]]) > 1 for raw in frames for i in range(10))

    assert disagreements(lambda i: INPUT_METER_INDEX[i]) < 0.01 * total
    assert disagreements(lambda i: INPUT_METER_INDEX[(i + 1) % 10]) > 0.5 * total


def test_levels_are_minus_half_a_db_per_step_and_silence_is_none():
    raw = bytes([255] * 128)
    raw = raw[:92] + bytes([0, 66]) + raw[94:]       # Mic 1 at 0.0 dBFS, Mic 2 at -33.0 dBFS
    levels = meter_levels(raw)
    assert isinstance(levels, MeterLevels)
    assert levels.inputs[0] == 0.0 and levels.inputs[1] == -33.0
    assert levels.inputs[2] is None                   # 255 = nothing
    assert all(level is None for level in levels.buses)


def test_a_short_frame_gives_none_for_the_indices_it_does_not_reach():
    levels = meter_levels(bytes([20] * 60))
    assert levels.inputs[28] == -10.0                 # Computer 1, index 0
    assert levels.inputs[10] == -10.0                 # S/PDIF L, index 58
    assert levels.inputs[0] is None                   # Mic 1, index 92, beyond the frame
