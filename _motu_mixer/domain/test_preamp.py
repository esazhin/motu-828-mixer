from _motu_mixer.domain.preamp import (
    GAIN, GAIN_CHANNELS, PAD, PHANTOM, PHASE, RETURNS_START, check_preamp_value, gain_range, is_preamp_prop,
)
from _motu_mixer.domain.status import Refusal, RefusalKind


def test_gain_polarity_pad_and_48v_are_the_writable_input_block():
    assert all(is_preamp_prop(p) for p in (GAIN, PHASE, PAD, PHANTOM))
    assert not is_preamp_prop(5005) and not is_preamp_prop(1016)


def test_gain_channels_are_the_two_mics_eight_lines_and_two_insert_returns():
    assert GAIN_CHANNELS[:2] == ("Mic 1", "Mic 2") and GAIN_CHANNELS[2] == "Line 3" and GAIN_CHANNELS[9] == "Line 10"
    assert GAIN_CHANNELS[RETURNS_START:] == ("Return 1", "Return 2")
    assert gain_range(0) == gain_range(1) == (0, 74)
    assert all(gain_range(i) == (0, 20) for i in range(2, 12))


def test_gain_is_a_whole_db_within_the_channel_range():
    assert check_preamp_value(GAIN, 0, 47) == 47
    assert check_preamp_value(GAIN, 1, 74) == 74
    assert check_preamp_value(GAIN, 9, 20.0) == 20
    assert check_preamp_value(GAIN, 11, 0) == 0
    for index, value in ((0, 75), (2, 21), (9, -1), (3, 12.5), (0, True), (10, 21)):
        outcome = check_preamp_value(GAIN, index, value)
        assert isinstance(outcome, Refusal) and outcome.kind is RefusalKind.VALUE_RANGE, (index, value)
    assert "Line 3" in check_preamp_value(GAIN, 2, 21).detail


def test_polarity_pad_and_48v_are_bits():
    assert check_preamp_value(PHASE, 4, True) == 1 and check_preamp_value(PHASE, 4, 0) == 0
    assert check_preamp_value(PAD, 1, 1) == 1 and check_preamp_value(PAD, 0, False) == 0
    assert check_preamp_value(PHANTOM, 0, 1) == 1 and check_preamp_value(PHANTOM, 1, False) == 0
    for value in (2, -1, 0.5):
        for prop in (PAD, PHASE, PHANTOM):
            outcome = check_preamp_value(prop, 0, value)
            assert isinstance(outcome, Refusal) and outcome.kind is RefusalKind.VALUE_RANGE
    assert "48V" in check_preamp_value(PHANTOM, 0, 2).detail
