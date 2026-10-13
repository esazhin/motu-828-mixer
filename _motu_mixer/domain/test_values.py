import math

import pytest

from _motu_mixer.domain.values import (
    BusIndex, DeviceIdentity, Gain, InputIndex, MixCell, OpticalConfig, OpticalMode, OutputPair,
    OutputSlot, Pan, Source,
)


def test_gain_minus_6_db_encodes_as_8_24():
    assert Gain.from_db(-6.0).to_824() == 0x00804DCE
    assert Gain.from_824(0x00804DCE).db == pytest.approx(-6.0, abs=1e-6)
    assert Gain.from_db("-inf").is_silent
    assert Gain.from_db("-inf").db == -math.inf


def test_gain_clamped_caps_at_plus_12_db():
    assert Gain(5.0).clamped().linear == 4.0
    assert Gain(1.0).clamped().linear == 1.0
    with pytest.raises(ValueError):
        Gain(-0.1)


def test_pan_is_clamped_to_unit_range():
    assert Pan(1.5).value == 1.0
    assert Pan(-0.5).value == 0.0
    assert Pan(Pan.CENTRE).value == 0.5


def test_bus_and_input_indexes_reject_out_of_range():
    with pytest.raises(ValueError):
        BusIndex(16)
    with pytest.raises(ValueError):
        InputIndex(44)
    assert BusIndex(15) == 15 and InputIndex(43) == 43


def test_mix_cell_index_is_bus_times_44_plus_input():
    assert MixCell(14, 27).index == 643
    assert MixCell(0, 2).index == 2


def test_mixer_input_slots_are_not_writable():
    assert not OutputSlot(86).writable
    assert OutputSlot(58).writable and OutputSlot(120).writable
    with pytest.raises(ValueError):
        OutputPair(86, 87)
    with pytest.raises(ValueError):
        Source(128)


def test_identity_strings_stop_at_the_first_nul():
    identity = DeviceIdentity("828E\0junk", "828E000000\0", "1.1.8+2458f7dace00", 9)
    assert (identity.model, identity.serial) == ("828E", "828E000000")


def test_optical_config_is_verified_only_for_adat_without_expander():
    assert OpticalConfig(OpticalMode.ADAT, OpticalMode.ADAT, 0).verified
    assert not OpticalConfig(OpticalMode.TOSLINK, OpticalMode.ADAT, 0).verified
    assert not OpticalConfig(OpticalMode.ADAT, OpticalMode.ADAT, 1).verified
