import pytest

from _motu_mixer.domain.properties import GATE_ARRAYS, PropertySpec, ValueType, spec
from _motu_mixer.domain.state import MixerState
from _motu_mixer.domain.values import (
    Address, BusIndex, Delta, DeviceIdentity, Gain, MixCell, OpticalConfig, OpticalMode, OutputSlot, Pan,
    Report, Source, Write,
)

TEST_ID = 4242


class SpecTable:
    """Stands in for the property table: answers spec(id) from known specs and records every id asked."""

    def __init__(self, specs):
        self.specs = specs
        self.asked = []

    def spec(self, prop_id):
        self.asked.append(prop_id)
        return self.specs.get(prop_id)


def test_state_types_reads_and_judges_completeness_by_the_property_table():
    table = SpecTable({
        TEST_ID: PropertySpec(ValueType.BYTE, 2, False),
        1016: PropertySpec(ValueType.F824, 704, True),
    })
    state = MixerState(spec=table.spec, gate_arrays=(TEST_ID,))

    # completeness over the gate arrays follows the length the table gives
    assert not state.arrays_complete()
    state.apply(Report(Address(TEST_ID, 0), b"\x01"))
    assert not state.arrays_complete()
    state.apply(Report(Address(TEST_ID, 1), b"\x00"))
    assert state.arrays_complete()
    assert TEST_ID in table.asked

    # a typed read decodes the raw value by the type the table gives
    table.asked.clear()
    state.apply(Report(Address(1016, 2), bytes.fromhex("01000000")))
    assert state.gain(MixCell(0, 2)).linear == 1.0
    assert 1016 in table.asked

    # an id the table does not know is still stored raw
    unknown = Address(6000, 0)
    assert state.apply(Report(unknown, b"\x05\x06")) == Delta(unknown, None, b"\x05\x06")
    assert state.apply(Report(unknown, b"\x05\x06")) is None
    assert state.arrays_complete()


LINE_3_MAIN_1 = Address(1016, 2)
LINE_3_ANA_3 = Address(1016, 178)
ZERO_DB = bytes.fromhex("01000000")
MINUS_10_DB = bytes.fromhex("0050f44e")


def _identity_reports():
    return [
        Report(Address(21, 0), b"828E\0"),
        Report(Address(8, 0), b"\x00\x09"),
        Report(Address(3, 0), b"828".ljust(32, b"\0")),
        Report(Address(2, 0), b"828E000000"),
    ]


def _every_gate_array_index(raw=b"\x00"):
    for prop in GATE_ARRAYS:
        for index in range(spec(prop).length):
            yield Report(Address(prop, index), raw)


def test_the_same_report_again_changes_nothing():
    state = MixerState()
    assert state.apply(Report(LINE_3_MAIN_1, ZERO_DB)) is not None
    assert state.apply(Report(LINE_3_MAIN_1, ZERO_DB)) is None
    assert state.gain(MixCell(0, 2)) == Gain(1.0)


def test_a_later_report_overwrites_an_earlier_one():
    state = MixerState()
    state.apply(Report(LINE_3_MAIN_1, ZERO_DB))
    assert state.apply(Report(LINE_3_MAIN_1, MINUS_10_DB)) == Delta(LINE_3_MAIN_1, ZERO_DB, MINUS_10_DB)
    assert state.gain(MixCell(0, 2)).db == pytest.approx(-10.0, abs=0.01)


def test_an_echo_of_a_write_changes_nothing():
    state = MixerState().with_writes((Write(LINE_3_ANA_3, Gain.from_db(-6.0)),))
    assert state.raw(LINE_3_ANA_3) == bytes.fromhex("00804dce")
    assert state.apply(Report(LINE_3_ANA_3, bytes.fromhex("00804dce"))) is None


def test_apply_with_writes_and_reads_as_the_service_relies_on():
    state = MixerState()
    cell = MixCell(4, 2)
    # nothing has arrived in a fresh state: every read is None
    assert state.gain(cell) is None and state.pan(cell) is None
    assert state.muted(cell) is None and state.soloed(cell) is None
    assert state.master_gain(BusIndex(4)) is None and state.master_muted(BusIndex(4)) is None
    assert state.source(OutputSlot(58)) is None and state.raw(LINE_3_ANA_3) is None
    assert state.identity() is None and state.optical_config() is None

    assert state.apply(Report(LINE_3_ANA_3, b"\0\0\0\0")) == Delta(LINE_3_ANA_3, None, b"\0\0\0\0")
    assert state.apply(Report(LINE_3_ANA_3, ZERO_DB)) == Delta(LINE_3_ANA_3, b"\0\0\0\0", ZERO_DB)
    assert state.apply(Report(LINE_3_ANA_3, ZERO_DB)) is None

    after = state.with_writes((
        Write(LINE_3_ANA_3, Gain.from_db(-6.0)),
        Write(Address(1017, cell.index), Pan(0.25)),
        Write(Address(1019, cell.index), True),
        Write(Address(5010, 58), Source(78)),
    ))
    assert after is not state
    assert state.gain(cell) == Gain(1.0) and state.muted(cell) is None
    assert after.gain(cell).db == pytest.approx(-6.0, abs=0.01)
    assert after.pan(cell) == Pan(0.25)
    assert after.muted(cell) is True
    assert after.source(OutputSlot(58)) == Source(78)


def test_reads_are_typed_by_the_property_table():
    state = MixerState()
    state.apply(Report(Address(1016, 2), ZERO_DB))
    state.apply(Report(Address(1017, 2), b"\0\0\0\0"))
    state.apply(Report(Address(1019, 90), b"\x01"))
    state.apply(Report(Address(1027, 14), ZERO_DB))
    state.apply(Report(Address(5010, 58), bytes([78])))
    state.apply(Report(Address(6000, 0), b"\x05" * 128))
    assert state.gain(MixCell(0, 2)) == Gain(1.0)
    assert state.pan(MixCell(0, 2)) == Pan(0.0)
    assert state.muted(MixCell(2, 2)) is True
    assert state.master_gain(BusIndex(14)) == Gain(1.0)
    assert state.source(OutputSlot(58)) == Source(78)
    assert state.raw(Address(6000, 0)) == b"\x05" * 128


def test_identity_optical_and_completeness_follow_what_has_arrived():
    state = MixerState()
    reports = _identity_reports()
    for report in reports[:3]:
        state.apply(report)
    assert state.identity() is None
    state.apply(reports[3])
    assert state.identity() == DeviceIdentity("828E", "828E000000", "", 9)

    state.apply(Report(Address(5006, 0), b"\x00"))
    state.apply(Report(Address(5006, 1), b"\x00"))
    assert state.optical_config() is None
    state.apply(Report(Address(5025, 0), b"\x00"))
    assert state.optical_config() == OpticalConfig(OpticalMode.ADAT, OpticalMode.ADAT, 0)
    state.apply(Report(Address(5006, 1), b"\x01"))
    assert state.optical_config().output_mode is OpticalMode.TOSLINK

    for report in _every_gate_array_index():
        if report.address != Address(5010, 127):
            state.apply(report)
    assert not state.arrays_complete()
    state.apply(Report(Address(5010, 127), bytes([127])))
    assert state.arrays_complete()


def test_a_plain_signed_824_write_encodes_like_the_wire_eq_gain_and_q():
    # 2026-10-12: _encode assumed every 8.24 value was a Gain or Pan, so a valid EQ gain write (-7.5 dB, a float)
    # crashed the WebSocket/HTTP change path with AttributeError.
    from _motu_mixer.domain.values import Write
    state = MixerState().with_writes((Write(Address(1025, 3), -7.5), Write(Address(1026, 3), 2.25)))
    assert state.raw(Address(1025, 3)) == (-7.5 * (1 << 24)).__round__().to_bytes(4, "big", signed=True)
    assert state.raw(Address(1026, 3)) == round(2.25 * (1 << 24)).to_bytes(4, "big", signed=True)
