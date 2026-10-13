import json
from pathlib import Path
from typing import NamedTuple

import pytest

from _motu_mixer.application.ports import ChangeOutcome, Snapshot
from _motu_mixer.domain.actions import (
    SetBusLink, SetInputLink, SetLevel, SetMasterLevel, SetMasterMute, SetMute, SetPan, SetProperty, SetRoute,
    SetRouteSlot, SetSolo,
)
from _motu_mixer.domain.state import MixerState
from _motu_mixer.domain.status import ConnectionStatus, LoopPath, PathNode, Refusal, RefusalKind
from _motu_mixer.domain.values import Address, Delta, OutputPair, OutputSlot, Report, Source, SourcePair
from _motu_mixer.presentation.view_model import (
    delta_message, outcome_json, parse_action, snapshot_json, state_message, status_message,
)
from _motu_mixer.protocol.sysex import SysexSplitter, decode

CAPTURE = Path(__file__).resolve().parents[2] / "fixtures" / "828-property-dump-2026-10-12.hex"
MINUS_6_DB_FRAME = bytes.fromhex("f0 00 00 3b 00 01 00 0a 03 78 02 03 00 04 00 05 00 4d 4e f7")
SPDIF_LOOP = LoopPath((PathNode(Source(58), "S/PDIF in"), PathNode(OutputSlot(58), "S/PDIF out")))


class Tab(NamedTuple):
    label: str
    first_bus: int
    is_stereo: bool


class Strip(NamedTuple):
    label: str
    first_input: int
    pan_visible: bool


class Classified(NamedTuple):
    label: str


class StubTopology:
    """Stands in for the topology catalogue: two tabs, three strips, canned pairs and labels; records each ask."""

    def __init__(self):
        self.from_links_asked = []
        self.pan_visible_asked = []
        self.classified = []
        topology = self

        class StereoLayout:
            @classmethod
            def from_links(cls, input_links, bus_links):
                topology.from_links_asked.append((input_links, bus_links))
                return cls()

            def tabs(self):
                return (Tab("Main 1-2", 0, True), Tab("Ana 3", 4, False))

            def strips(self):
                return (Strip("Line 3", 2, True), Strip("Line 5-6", 4, False), Strip("S/PDIF L-R", 10, False))

            def pan_visible(self, strip, tab):
                topology.pan_visible_asked.append((strip, tab))
                return (strip, tab) == (2, 0)

        self.StereoLayout = StereoLayout
        self.OUTPUT_PAIRS = {OutputPair(43, 40): "Line out 9-10", OutputPair(58, 59): "S/PDIF out"}
        self.SOURCE_OPTIONS = {
            SourcePair(Source(76), Source(77)): "Main 1-2 mix",
            SourcePair(Source(127), Source(127)): "nothing",
        }

    def classify(self, source):
        self.classified.append(source)
        return Classified(f"stub label {source.code}")


def test_snapshot_layout_mirrors_the_tabs_strips_pairs_and_labels_of_the_topology():
    topology = StubTopology()
    state = MixerState()
    for address, raw in (
        (Address(1000, 4), b"\x01"),
        (Address(1001, 0), b"\x01"),
        (Address(5010, 43), bytes([125])),
        (Address(5010, 40), bytes([77])),
    ):
        state.apply(Report(address, raw))
    snapshot = Snapshot(state=state, status=ConnectionStatus.SYNCED, read_only_detail=None, loops=frozenset())

    layout = snapshot_json(snapshot, topology=topology)["layout"]

    assert topology.from_links_asked == [state.links()]
    assert layout["tabs"] == [
        {"label": "Main 1-2", "bus": 0, "stereo": True, "pan": [2]},
        {"label": "Ana 3", "bus": 4, "stereo": False, "pan": []},
    ]
    assert layout["strips"] == [
        {"label": "Line 3", "input": 2, "pan_visible": True},
        {"label": "Line 5-6", "input": 4, "pan_visible": False},
        {"label": "S/PDIF L-R", "input": 10, "pan_visible": False},
    ]
    assert set(topology.pan_visible_asked) == {(s, t) for s in (2, 4, 10) for t in (0, 4)}
    assert layout["output_pairs"] == [
        {"label": "Line out 9-10", "left": 43, "right": 40},
        {"label": "S/PDIF out", "left": 58, "right": 59},
    ]
    assert layout["sources"] == [
        {"label": "Main 1-2 mix", "left": 76, "right": 77},
        {"label": "nothing", "left": 127, "right": 127},
    ]
    assert {Source(125), Source(77)} <= set(topology.classified)
    assert layout["source_labels"] == {"125": "stub label 125", "77": "stub label 77"}


@pytest.mark.parametrize("body, action", [
    ({"kind": "set_level", "bus": 4, "input": 2, "db": -6.0}, SetLevel(bus=4, input=2, db=-6.0)),
    ({"kind": "set_level", "bus": 4, "input": 2, "db": "-inf"}, SetLevel(bus=4, input=2, db="-inf")),
    ({"kind": "set_pan", "bus": 0, "input": 2, "pan": 1.5}, SetPan(bus=0, input=2, pan=1.5)),
    ({"kind": "set_mute", "bus": 14, "input": 27, "on": True}, SetMute(bus=14, input=27, on=True)),
    ({"kind": "set_solo", "bus": 4, "input": 3, "on": False}, SetSolo(bus=4, input=3, on=False)),
    ({"kind": "set_input_link", "input": 4, "on": True}, SetInputLink(input=4, on=True)),
    ({"kind": "set_bus_link", "bus": 4, "on": True}, SetBusLink(bus=4, on=True)),
    ({"kind": "set_master_level", "bus": 4, "db": -3}, SetMasterLevel(bus=4, db=-3)),
    ({"kind": "set_master_mute", "bus": 0, "on": True}, SetMasterMute(bus=0, on=True)),
    ({"kind": "set_route", "output_slots": [43, 40], "sources": [76, 77]},
     SetRoute(output_slots=(43, 40), sources=(76, 77))),
    ({"kind": "set_route_slot", "slot": 86, "source": 32}, SetRouteSlot(slot=86, source=32)),
    ({"kind": "set_property", "prop": 5004, "index": 0, "value": 1}, SetProperty(prop=5004, index=0, value=1)),
])
def test_each_action_kind_parses_to_its_typed_action(body, action):
    assert parse_action(body) == action


@pytest.mark.parametrize("body", [
    {"kind": "set_level", "bus": 16, "input": 44, "db": -6.0},
    {"kind": "set_mute", "bus": 0, "input": 44, "on": True},
    {"kind": "set_master_mute", "bus": -1, "on": True},
    {"kind": "set_route_slot", "slot": 128, "source": 32},
    {"kind": "set_route_slot", "slot": 40, "source": 128},
    {"kind": "set_route", "output_slots": [43, 40], "sources": [76, 200]},
    {"kind": "set_property", "prop": 1016, "index": 704, "value": 1.0},
    {"kind": "set_property", "prop": 5004, "index": -1, "value": 1},
])
def test_an_index_outside_the_unit_is_refused_as_out_of_range(body):
    refusal = parse_action(body)
    assert isinstance(refusal, Refusal)
    assert refusal.kind is RefusalKind.INDEX_RANGE
    assert "does not exist" in refusal.detail


@pytest.mark.parametrize("body", [
    {"kind": "foo"},
    {"bus": 4},
    ["set_level"],
    {"kind": "set_level", "bus": 4, "input": 2},
    {"kind": "set_level", "bus": 4, "input": 2, "db": "loud"},
    {"kind": "set_mute", "bus": 4, "input": 2, "on": "yes"},
    {"kind": "set_pan", "bus": "4", "input": 2, "pan": 0.5},
    {"kind": "set_route", "output_slots": [43], "sources": [76, 77]},
    {"kind": "set_property", "prop": 1016, "index": 2},
])
def test_an_unknown_kind_or_a_missing_or_ill_typed_field_is_refused_as_malformed(body):
    refusal = parse_action(body)
    assert isinstance(refusal, Refusal)
    assert refusal.kind is RefusalKind.MALFORMED_REQUEST


def test_a_request_for_bus_16_input_44_is_told_the_cell_does_not_exist():
    refusal = parse_action({"kind": "set_level", "bus": 16, "input": 44, "db": 0.0})
    assert refusal.kind is RefusalKind.INDEX_RANGE
    assert "does not exist" in refusal.detail


def capture_state():
    state = MixerState()
    for frame in SysexSplitter().feed(bytes.fromhex(CAPTURE.read_text())):
        report = decode(frame)
        if isinstance(report, Report):
            state.apply(report)
    return state


def test_the_captured_snapshot_serializes_every_section_with_ten_tabs_and_all_sixteen_masters():
    snapshot = Snapshot(state=capture_state(), status=ConnectionStatus.SYNCED, read_only_detail=None,
                        loops=frozenset({SPDIF_LOOP}))

    body = snapshot_json(snapshot)

    assert set(body) >= {"status", "read_only_reason", "device", "links", "cells", "bus_master", "routing", "loops",
                         "policy", "layout"}
    json.dumps(body)
    assert body["status"] == "synced"
    assert body["device"] == {"model": "828E", "serial": "828E000000", "api": 9, "firmware": body["device"]["firmware"]}
    assert [len(body["links"]["inputs"]), len(body["links"]["buses"])] == [44, 16]
    assert {name: len(values) for name, values in body["cells"].items()} == {
        "fader": 704, "fader_db": 704, "pan": 704, "mute": 704, "solo": 704}
    assert body["cells"]["fader"][2] == 1.0 and body["cells"]["pan"][2] == 0.0
    assert body["routing"]["58"] == 78 and body["routing"]["59"] == 79
    assert body["loops"] == [["S/PDIF in", "S/PDIF out"]]
    assert [tab["label"] for tab in body["layout"]["tabs"]] == [
        "Main 1-2", "Ana 1-2", "Ana 3", "Ana 4", "Ana 5", "Ana 6", "Ana 7-8", "Phones 1-2", "Phones 3-4", "Reverb 1-2"]
    assert len(body["layout"]["strips"]) == 36

    # the Reverb 1-2 master reads 0.0 dB, and every master is present
    reverb = next(tab for tab in body["layout"]["tabs"] if tab["label"] == "Reverb 1-2")
    assert body["bus_master"]["fader"][reverb["bus"]] == 1.0
    assert body["bus_master"]["fader_db"][reverb["bus"]] == 0.0
    assert all(len(values) == 16 and None not in values for values in body["bus_master"].values())

    assert state_message(snapshot) == {"type": "state", **body}


def test_an_empty_state_serializes_with_unknown_values_as_null():
    body = snapshot_json(Snapshot(state=MixerState(), status=ConnectionStatus.NO_DEVICE, read_only_detail=None,
                                  loops=frozenset()))
    json.dumps(body)
    assert body["device"] == {"model": None, "serial": None, "api": None, "firmware": None}
    assert body["cells"]["fader"][0] is None and body["routing"] == {}


def test_only_displayed_properties_make_a_delta_message_and_faders_carry_their_db():
    meters = (Delta(Address(6000, 0), b"\x00", b"\x01"),)
    assert delta_message(meters) is None

    fader = Delta(Address(1016, 2), bytes.fromhex("01000000"), bytes.fromhex("00804dce"))
    silent = Delta(Address(1027, 4), bytes.fromhex("01000000"), bytes.fromhex("00000000"))
    route = Delta(Address(5010, 43), bytes([76]), bytes([125]))
    message = delta_message(meters + (fader, silent, route))

    assert message["type"] == "delta"
    first, master, routing = message["changes"]
    assert first["prop"] == "fader" and first["index"] == 2
    assert first["db"] == pytest.approx(-6.0, abs=0.01)
    assert master == {"prop": "master_fader", "index": 4, "value": 0.0, "db": "-inf"}
    assert routing == {"prop": "routing", "index": 43, "value": 125, "label": "source 125 (unknown)"}


def test_status_and_outcome_messages_carry_their_type_and_the_path_labels():
    assert status_message(ConnectionStatus.READ_ONLY, "API version 10, expected 9", frozenset({SPDIF_LOOP})) == {
        "type": "status", "status": "read_only", "read_only_reason": "API version 10, expected 9",
        "loops": [["S/PDIF in", "S/PDIF out"]],
    }

    accepted = outcome_json(ChangeOutcome.Accepted(sent=(MINUS_6_DB_FRAME,)))
    assert accepted["type"] == "accepted" and accepted["accepted"] is True
    assert [bytes.fromhex(frame) for frame in accepted["sent"]] == [MINUS_6_DB_FRAME]

    looped = outcome_json(ChangeOutcome.Refused(Refusal(RefusalKind.LOOP, "S/PDIF in → S/PDIF out", SPDIF_LOOP)))
    assert looped == {"type": "refused", "accepted": False, "kind": "loop",
                      "reason": "Refused: S/PDIF in → S/PDIF out", "path": ["S/PDIF in", "S/PDIF out"]}
    plain = outcome_json(ChangeOutcome.Refused(Refusal(RefusalKind.NOT_WRITABLE_PROPERTY,
                                                       "property 5004 cannot be changed here")))
    assert plain["reason"] == "Refused: property 5004 cannot be changed here" and plain["path"] is None


def test_meters_message_carries_each_input_and_bus_level_in_dbfs_or_null():
    from _motu_mixer.domain.meters import meter_levels
    from _motu_mixer.presentation.view_model import meters_message

    raw = bytearray([255] * 128)
    raw[92], raw[58], raw[76] = 66, 0, 24             # Mic 1 -33 dBFS, S/PDIF L 0 dBFS, Main 1 mix -12 dBFS
    message = meters_message(meter_levels(bytes(raw)))
    assert message["type"] == "meters"
    assert len(message["inputs"]) == 44 and len(message["buses"]) == 16
    assert message["inputs"][0] == -33.0 and message["inputs"][10] == 0.0 and message["inputs"][1] is None
    assert message["buses"][0] == -12.0 and message["buses"][1] is None


def test_the_state_and_deltas_carry_both_eqs_decoded():
    from _motu_mixer.application.ports import Snapshot
    from _motu_mixer.domain.state import MixerState
    from _motu_mixer.domain.status import ConnectionStatus
    from _motu_mixer.domain.values import Address, Delta, Report
    from _motu_mixer.presentation.view_model import delta_message, snapshot_json
    from _motu_mixer.protocol.sysex import encode_value
    from _motu_mixer.domain.properties import ValueType

    state = MixerState()
    reports = [  # the live Main EQ of 2026-10-12: peak 200 Hz -3.0 dB Q 6, peak 353 Hz -5.6 dB Q 2, shelf 3543 Hz
        (1022, 2, ValueType.BYTE, 2), (1023, 0, ValueType.BYTE, 0), (1024, 0, ValueType.I32, 200),
        (1025, 1, ValueType.F824, -5.6), (1026, 0, ValueType.F824, 6.0), (1005, 7, ValueType.F824, 4.5),
    ]
    for prop, index, value_type, value in reports:
        state.apply(Report(Address(prop, index), encode_value(value_type, value)))
    body = snapshot_json(Snapshot(state=state, status=ConnectionStatus.SYNCED, read_only_detail=None, loops=frozenset()))
    out, inp = body["eq"]["output"], body["eq"]["input"]
    assert set(out) == {"mode", "bypass", "freq", "gain", "q"} and len(out["freq"]) == 42 and len(inp["freq"]) == 40
    assert out["mode"][2] == 2 and out["bypass"][0] == 0 and out["freq"][0] == 200
    assert abs(out["gain"][1] - -5.6) < 1e-6 and out["q"][0] == 6.0 and abs(inp["gain"][7] - 4.5) < 1e-6
    assert out["freq"][1] is None                                   # not reported yet
    raw = encode_value(ValueType.F824, -3.0)
    message = delta_message((Delta(Address(1025, 0), None, raw),))
    assert message["changes"] == [{"prop": "out_eq_gain", "index": 0, "value": -3.0}]
    message = delta_message((Delta(Address(1004, 4), None, encode_value(ValueType.I32, 1000)),))
    assert message["changes"] == [{"prop": "in_eq_freq", "index": 4, "value": 1000}]
