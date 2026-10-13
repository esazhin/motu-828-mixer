import pytest

from _motu_mixer.domain.actions import (
    SetBusLink, SetInputLink, SetLevel, SetMasterLevel, SetMasterMute, SetMute, SetPan, SetProperty, SetRoute,
    SetRouteSlot, SetSolo, plan,
)
from _motu_mixer.domain.properties import PropertySpec, ValueType
from _motu_mixer.domain.state import MixerState
from _motu_mixer.domain.status import Refusal, RefusalKind
from _motu_mixer.domain.values import Address, MixCell, OutputPair, Pan, Report, Source, Write

LOCKED_ID = 4242
OPEN_ID = 4243
UNKNOWN_ID = 4244


class SpecTable:
    """Stands in for the property table: answers spec(id) from known specs and records every id asked."""

    def __init__(self, specs):
        self.specs = specs
        self.asked = []

    def spec(self, prop_id):
        self.asked.append(prop_id)
        return self.specs.get(prop_id)


def test_raw_property_writes_follow_the_table_writability():
    table = SpecTable({
        LOCKED_ID: PropertySpec(ValueType.BYTE, 4, False),
        OPEN_ID: PropertySpec(ValueType.BYTE, 4, True),
    })
    state = MixerState()

    locked = plan(SetProperty(LOCKED_ID, 0, 1), state, spec=table.spec)
    assert isinstance(locked, Refusal)
    assert locked.kind is RefusalKind.NOT_WRITABLE_PROPERTY
    assert f"property {LOCKED_ID} cannot be changed here" in locked.detail

    unknown = plan(SetProperty(UNKNOWN_ID, 0, 1), state, spec=table.spec)
    assert isinstance(unknown, Refusal)
    assert unknown.kind is RefusalKind.NOT_WRITABLE_PROPERTY

    writes = plan(SetProperty(OPEN_ID, 1, 1), state, spec=table.spec)
    assert [write.address for write in writes] == [Address(OPEN_ID, 1)]

    assert table.asked.count(LOCKED_ID) >= 1
    assert table.asked.count(UNKNOWN_ID) >= 1
    assert table.asked.count(OPEN_ID) >= 1


class StubTopology:
    """Stands in for the topology catalogue: a stereo layout with canned primary cells and one output pair."""

    def __init__(self, primary_cells, output_pairs):
        self.from_links_asked = []
        self.primary_cell_asked = []
        self.output_pairs_asked = []
        topology = self

        class StereoLayout:
            @classmethod
            def from_links(cls, input_links, bus_links):
                topology.from_links_asked.append((input_links, bus_links))
                return cls()

            def primary_cell(self, strip, tab):
                topology.primary_cell_asked.append((strip, tab))
                return primary_cells[(strip, tab)]

        class OutputPairs(tuple):
            def __iter__(self):
                topology.output_pairs_asked.append("iter")
                return super().__iter__()

            def __contains__(self, pair):
                topology.output_pairs_asked.append(pair)
                return super().__contains__(pair)

        self.StereoLayout = StereoLayout
        self.OUTPUT_PAIRS = OutputPairs(output_pairs)


def test_plan_maps_strips_tabs_and_output_pairs_through_the_stereo_layout():
    line_out_9_10 = OutputPair(43, 40)
    topology = StubTopology(
        primary_cells={
            (3, 5): MixCell(4, 2),  # Line 3-4 strip on the Ana 3-4 tab: first bus, first input
            (2, 4): MixCell(4, 2),  # a mono strip on a mono tab: the cell itself
        },
        output_pairs=(line_out_9_10,),
    )
    state = MixerState()
    state.apply(Report(Address(1000, 2), b"\x01"))
    state.apply(Report(Address(1001, 4), b"\x01"))

    # a level on a linked strip/tab writes exactly one cell: the one the layout names
    linked = plan(SetLevel(bus=5, input=3, db=-6.0), state, topology=topology)
    assert [write.address for write in linked] == [Address(1016, MixCell(4, 2).index)]
    assert linked[0].value.db == pytest.approx(-6.0)
    assert topology.from_links_asked == [state.links()]
    assert topology.primary_cell_asked == [(3, 5)]

    # a mono strip on a mono tab writes the cell itself
    mono = plan(SetLevel(bus=4, input=2, db=0.0), state, topology=topology)
    assert [write.address for write in mono] == [Address(1016, 178)]
    assert topology.primary_cell_asked[-1] == (2, 4)

    # a route writes the left and right slots of the output pair from the catalogue
    routed = plan(SetRoute(output_slots=(43, 40), sources=(76, 77)), state, topology=topology)
    assert [(write.address, write.value) for write in routed] == [
        (Address(5010, line_out_9_10.left), Source(76)),
        (Address(5010, line_out_9_10.right), Source(77)),
    ]
    assert topology.output_pairs_asked


class Catalogue:
    """Stands in for the topology catalogue with canned cells, master buses and output pairs."""

    def __init__(self, cells=None, masters=None, pairs=()):
        cells, masters = cells or {}, masters or {}

        class StereoLayout:
            @classmethod
            def from_links(cls, input_links, bus_links):
                return cls()

            def primary_cell(self, strip, tab):
                return cells[(strip, tab)]

            def master_bus(self, tab):
                return masters[tab]

        self.StereoLayout = StereoLayout
        self.OUTPUT_PAIRS = pairs


LINE_3, LINE_4, LINE_5 = 2, 3, 4
MAIN_1, ANA_3 = 0, 4


def _addresses_and_values(writes):
    return [(write.address, write.value) for write in writes]


def test_levels_are_written_at_the_one_cell_and_capped_at_plus_12_db():
    catalogue = Catalogue(cells={(LINE_3, ANA_3): MixCell(4, 2)})

    unity = plan(SetLevel(bus=ANA_3, input=LINE_3, db=0.0), MixerState(), topology=catalogue)
    assert [write.address for write in unity] == [Address(1016, 178)]
    assert unity[0].value.linear == pytest.approx(1.0)

    capped = plan(SetLevel(bus=ANA_3, input=LINE_3, db=15.0), MixerState(), topology=catalogue)
    assert [write.address for write in capped] == [Address(1016, 178)]
    assert capped[0].value.linear == 4.0
    assert capped[0].value.db == pytest.approx(12.0, abs=0.05)


def test_pan_is_written_at_the_one_cell_and_clamped():
    catalogue = Catalogue(cells={(LINE_3, MAIN_1): MixCell(0, 2)})

    centre = plan(SetPan(bus=MAIN_1, input=LINE_3, pan=0.5), MixerState(), topology=catalogue)
    assert _addresses_and_values(centre) == [(Address(1017, 2), Pan(0.5))]

    beyond = plan(SetPan(bus=MAIN_1, input=LINE_3, pan=1.5), MixerState(), topology=catalogue)
    assert _addresses_and_values(beyond) == [(Address(1017, 2), Pan(1.0))]


def test_each_solo_writes_its_own_bit_only():
    catalogue = Catalogue(cells={(LINE_3, ANA_3): MixCell(4, 2), (LINE_4, ANA_3): MixCell(4, 3)})

    first = plan(SetSolo(bus=ANA_3, input=LINE_3, on=True), MixerState(), topology=catalogue)
    second = plan(SetSolo(bus=ANA_3, input=LINE_4, on=True), MixerState(), topology=catalogue)
    assert _addresses_and_values(first) == [(Address(1018, 178), True)]
    assert _addresses_and_values(second) == [(Address(1018, 179), True)]

    muted = plan(SetMute(bus=ANA_3, input=LINE_3, on=True), MixerState(), topology=catalogue)
    assert _addresses_and_values(muted) == [(Address(1019, 178), True)]


def test_linking_writes_only_the_even_link_bit():
    assert _addresses_and_values(plan(SetInputLink(input=LINE_5, on=True), MixerState(), topology=Catalogue())) \
        == [(Address(1000, 4), True)]
    assert _addresses_and_values(plan(SetBusLink(bus=ANA_3, on=True), MixerState(), topology=Catalogue())) \
        == [(Address(1001, 4), True)]

    odd = plan(SetInputLink(input=5, on=True), MixerState(), topology=Catalogue())
    assert isinstance(odd, Refusal) and odd.kind is RefusalKind.ODD_LINK_INDEX
    assert "only even indexes can be linked" in odd.detail
    odd_bus = plan(SetBusLink(bus=5, on=True), MixerState(), topology=Catalogue())
    assert odd_bus.kind is RefusalKind.ODD_LINK_INDEX


def test_bus_master_is_written_at_the_master_bus_of_the_tab():
    catalogue = Catalogue(masters={ANA_3: 4, 1: 0})

    level = plan(SetMasterLevel(bus=ANA_3, db=-3.0), MixerState(), topology=catalogue)
    assert [write.address for write in level] == [Address(1027, 4)]
    assert level[0].value.db == pytest.approx(-3.0, abs=0.1)

    mute = plan(SetMasterMute(bus=1, on=True), MixerState(), topology=catalogue)
    assert _addresses_and_values(mute) == [(Address(1028, 0), True)]


ROUTING_PAIRS = (OutputPair(43, 40), OutputPair(53, 56), OutputPair(54, 51))


@pytest.mark.parametrize("slots, sources", [
    ((43, 40), (76, 77)),  # Main 1-2 mix to Line out 9-10
    ((53, 56), (47, 34)),  # Mic 1-2 to Line out 3-4
    ((43, 40), (127, 127)),  # nothing to Line out 9-10
    ((54, 51), (78, 79)),  # Ana 1-2 mix to Line out 5-6
])
def test_a_route_writes_the_left_and_right_slots_of_the_output_pair(slots, sources):
    writes = plan(SetRoute(output_slots=slots, sources=sources), MixerState(), topology=Catalogue(pairs=ROUTING_PAIRS))
    assert _addresses_and_values(writes) == [
        (Address(5010, slots[0]), Source(sources[0])),
        (Address(5010, slots[1]), Source(sources[1])),
    ]


def test_mixer_input_slots_and_strangers_to_the_output_pairs_cannot_be_routed():
    catalogue = Catalogue(pairs=ROUTING_PAIRS)

    slot_86 = plan(SetRouteSlot(slot=86, source=32), MixerState(), topology=catalogue)
    assert isinstance(slot_86, Refusal) and slot_86.kind is RefusalKind.SLOT_NOT_WRITABLE
    assert "cannot be changed here" in slot_86.detail
    raw_86 = plan(SetProperty(5010, 86, 32), MixerState(), topology=catalogue)
    assert raw_86.kind is RefusalKind.SLOT_NOT_WRITABLE
    pair_of_mixer_inputs = plan(SetRoute(output_slots=(86, 87), sources=(32, 33)), MixerState(), topology=catalogue)
    assert pair_of_mixer_inputs.kind is RefusalKind.SLOT_NOT_WRITABLE
    not_a_pair = plan(SetRoute(output_slots=(43, 53), sources=(76, 77)), MixerState(), topology=catalogue)
    assert not_a_pair.kind is RefusalKind.SLOT_NOT_WRITABLE

    single = plan(SetRouteSlot(slot=58, source=78), MixerState(), topology=catalogue)
    assert _addresses_and_values(single) == [(Address(5010, 58), Source(78))]


EVERY_KIND = [
    SetLevel(bus=4, input=2, db=-6.0), SetPan(bus=0, input=2, pan=0.5), SetMute(bus=0, input=28, on=True),
    SetSolo(bus=4, input=2, on=True), SetInputLink(input=4, on=True), SetBusLink(bus=4, on=False),
    SetMasterLevel(bus=4, db=-3.0), SetMasterMute(bus=0, on=True),
    SetRoute(output_slots=(43, 40), sources=(76, 77)), SetRouteSlot(slot=58, source=127),
    SetProperty(1016, 643, 0.5),
]
REFUSED = [
    (SetInputLink(input=5, on=True), RefusalKind.ODD_LINK_INDEX),
    (SetRouteSlot(slot=86, source=32), RefusalKind.SLOT_NOT_WRITABLE),
    (SetProperty(5004, 0, 1), RefusalKind.NOT_WRITABLE_PROPERTY),
    (SetProperty(5000, 0, -10), RefusalKind.NOT_WRITABLE_PROPERTY),
    (SetLevel(bus=16, input=44, db=0.0), RefusalKind.INDEX_RANGE),
    (SetProperty(1016, 704, 1.0), RefusalKind.INDEX_RANGE),
]


def test_every_action_plans_to_writes_or_one_of_the_planner_refusals():
    state = MixerState()
    for index in (10, 28, 30, 34, 36, 38, 40, 42):
        state.apply(Report(Address(1000, index), b"\x01"))
    for bus in (0, 2, 8, 10, 12, 14):
        state.apply(Report(Address(1001, bus), b"\x01"))

    for action in EVERY_KIND:
        writes = plan(action, state)
        assert isinstance(writes, tuple) and writes, action
        assert all(isinstance(write, Write) for write in writes)
        state.with_writes(writes)

    for action, kind in REFUSED:
        refusal = plan(action, state)
        assert isinstance(refusal, Refusal) and refusal.kind is kind, action


def test_eq_property_writes_carry_the_signed_value_and_out_of_range_ones_are_refused():
    state = MixerState()
    assert plan(SetProperty(1025, 1, -5.6), state) == (Write(Address(1025, 1), -5.6),)     # Main band 2 gain
    assert plan(SetProperty(1024, 0, 200), state) == (Write(Address(1024, 0), 200),)       # Main band 1 freq
    assert plan(SetProperty(1026, 2, 3.0), state) == (Write(Address(1026, 2), 3.0),)       # Main band 3 Q
    assert plan(SetProperty(1023, 0, True), state) == (Write(Address(1023, 0), 1),)        # bypass
    for refused in (SetProperty(1025, 1, -25.0), SetProperty(1024, 0, 5), SetProperty(1022, 1, 3)):
        outcome = plan(refused, state)
        assert isinstance(outcome, Refusal) and outcome.kind is RefusalKind.VALUE_RANGE
