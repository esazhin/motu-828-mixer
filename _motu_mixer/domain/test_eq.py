from _motu_mixer.domain.eq import (
    INPUT_EQ, OUTPUT_EQ, band_of, check_eq_value, eq_slot_for_bus, is_eq_prop, modes_for_band,
)
from _motu_mixer.domain.status import Refusal, RefusalKind


def test_layouts_match_the_828_tables():
    # input EQ: 10 channels (Mic 1-2, Line 3-10) x 4 bands; output EQ: 14 bus slots x 3 bands (dev_828.js)
    assert (INPUT_EQ.channels, INPUT_EQ.bands, INPUT_EQ.mode) == (10, 4, 1002)
    assert (OUTPUT_EQ.channels, OUTPUT_EQ.bands, OUTPUT_EQ.mode) == (14, 3, 1022)
    assert [INPUT_EQ.prop(k) for k in ("mode", "bypass", "freq", "gain", "q")] == [1002, 1003, 1004, 1005, 1006]
    assert [OUTPUT_EQ.prop(k) for k in ("mode", "bypass", "freq", "gain", "q")] == [1022, 1023, 1024, 1025, 1026]
    assert is_eq_prop(1004) and is_eq_prop(1026) and not is_eq_prop(1016)
    assert band_of(1025, 5) == (OUTPUT_EQ, 1, 2)          # index 5 = slot 1, band 2


def test_a_bus_uses_its_own_slot_and_a_linked_pair_its_first_like_cuemix_busEq():
    assert eq_slot_for_bus(0, linked=True) == 0       # Main 1-2
    assert eq_slot_for_bus(1, linked=True) == 0
    assert eq_slot_for_bus(4, linked=False) == 4      # Ana 3 alone
    assert eq_slot_for_bus(3, linked=True) == 2       # Ana 1-2 linked -> Ana 1's slot
    assert eq_slot_for_bus(10, linked=True) == 10     # Phones 1
    assert eq_slot_for_bus(13, linked=True) == 12     # Phones 2 (buses 12/13)
    assert eq_slot_for_bus(14, linked=True) == 13     # Reverb


def test_band_modes_follow_cuemix_first_band_peak_shelf_or_high_pass_last_band_peak_or_shelf_middle_peak():
    assert modes_for_band(INPUT_EQ, 0) == (0, 1, 3)
    assert modes_for_band(INPUT_EQ, 1) == (0,) and modes_for_band(INPUT_EQ, 2) == (0,)
    assert modes_for_band(INPUT_EQ, 3) == (0, 2)
    assert modes_for_band(OUTPUT_EQ, 0) == (0, 1, 3) and modes_for_band(OUTPUT_EQ, 1) == (0,)
    assert modes_for_band(OUTPUT_EQ, 2) == (0, 2)


def test_values_inside_the_cuemix_ranges_pass_and_come_back_typed():
    assert check_eq_value(1024, 0, 200) == 200                # Main band 0 at 200 Hz (int on the wire)
    assert check_eq_value(1025, 1, -5.6) == -5.6              # gain is signed dB, never clamped to >= 0
    assert check_eq_value(1026, 2, 0.45) == 0.45 and check_eq_value(1006, 0, 10) == 10.0
    assert check_eq_value(1023, 0, True) == 1 and check_eq_value(1023, 0, 0) == 0
    assert check_eq_value(1022, 0, 3) == 3 and check_eq_value(1022, 2, 2) == 2


def test_out_of_range_values_and_disallowed_modes_are_refused_not_clamped():
    for prop, index, value in [(1024, 0, 19), (1024, 0, 20001), (1025, 0, -20.5), (1005, 0, 21),
                               (1026, 0, 0.4), (1006, 0, 10.5), (1023, 0, 2), (1022, 1, 1), (1022, 2, 3), (1002, 3, 1)]:
        refusal = check_eq_value(prop, index, value)
        assert isinstance(refusal, Refusal) and refusal.kind is RefusalKind.VALUE_RANGE, (prop, index, value)
