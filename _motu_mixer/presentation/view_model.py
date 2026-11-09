"""The one translation between the page's JSON and the service: action parsing and every message the page receives."""
import math

from _motu_mixer.application.ports import ChangeOutcome
from _motu_mixer.domain import properties, topology as _topology
from _motu_mixer.domain.actions import (
    SetBusLink, SetChain, SetInputLink, SetLevel, SetMasterLevel, SetMasterMute, SetMute, SetPan, SetProperty,
    SetRoute, SetRouteSlot, SetSolo,
)
from _motu_mixer.domain import chain as _chain, preamp
from _motu_mixer.domain.eq import INPUT_EQ, OUTPUT_EQ
from _motu_mixer.domain.properties import PropertyId as P
from _motu_mixer.domain.protocol_bounds import OPEN_ITEMS
from _motu_mixer.domain.status import Refusal, RefusalKind
from _motu_mixer.domain.values import Address, BusIndex, Gain, InputIndex, MixCell, OutputSlot, Source
from _motu_mixer.protocol.sysex import decode_value


class _Malformed(Exception):
    pass


def _is_int(value):
    return isinstance(value, int) and not isinstance(value, bool)


def _is_number(value):
    return isinstance(value, (int, float)) and not isinstance(value, bool)


def _int(obj, name):
    value = obj.get(name)
    if not _is_int(value):
        raise _Malformed(f"'{name}' must be an integer")
    return value


def _flag(obj, name):
    value = obj.get(name)
    if not isinstance(value, bool):
        raise _Malformed(f"'{name}' must be true or false")
    return value


def _number(obj, name):
    value = obj.get(name)
    if not _is_number(value):
        raise _Malformed(f"'{name}' must be a number")
    return value


def _db(obj, name="db"):
    return "-inf" if obj.get(name) == "-inf" else _number(obj, name)


def _int_pair(obj, name):
    value = obj.get(name)
    if not (isinstance(value, list) and len(value) == 2 and all(_is_int(v) for v in value)):
        raise _Malformed(f"'{name}' must be a list of two integers")
    return tuple(value)


def _bus(obj):
    return BusIndex(_int(obj, "bus"))


def _input(obj):
    return InputIndex(_int(obj, "input"))


def _source(code):
    return Source(code).code


def _property(obj):
    prop, index = _int(obj, "prop"), _int(obj, "index")
    value = obj.get("value")
    if not (_is_number(value) or isinstance(value, bool)):
        raise _Malformed("'value' must be a number or true/false")
    entry = properties.spec(prop)
    if index < 0 or (entry is not None and index >= entry.length):
        raise ValueError(f"property {prop} has no index {index}")
    return SetProperty(prop=prop, index=index, value=value)


def _chain_action(obj):
    on = _flag(obj, "on")
    stages = obj.get("stages", [])
    if not isinstance(stages, list):
        raise _Malformed("'stages' must be a list of {bus, carrier}")
    parsed = []
    for stage in stages:
        if not isinstance(stage, dict):
            raise _Malformed("'stages' must be a list of {bus, carrier}")
        parsed.append(_chain.Stage(bus=_int(stage, "bus"), carrier=_int(stage, "carrier")))
    return SetChain(on=on, stages=tuple(parsed))


_PARSERS = {
    "set_chain": _chain_action,
    "set_level": lambda o: SetLevel(bus=_bus(o), input=_input(o), db=_db(o)),
    "set_pan": lambda o: SetPan(bus=_bus(o), input=_input(o), pan=_number(o, "pan")),
    "set_mute": lambda o: SetMute(bus=_bus(o), input=_input(o), on=_flag(o, "on")),
    "set_solo": lambda o: SetSolo(bus=_bus(o), input=_input(o), on=_flag(o, "on")),
    "set_input_link": lambda o: SetInputLink(input=_input(o), on=_flag(o, "on")),
    "set_bus_link": lambda o: SetBusLink(bus=_bus(o), on=_flag(o, "on")),
    "set_master_level": lambda o: SetMasterLevel(bus=_bus(o), db=_db(o)),
    "set_master_mute": lambda o: SetMasterMute(bus=_bus(o), on=_flag(o, "on")),
    "set_route": lambda o: SetRoute(output_slots=tuple(OutputSlot(s) for s in _int_pair(o, "output_slots")),
                                    sources=tuple(_source(s) for s in _int_pair(o, "sources"))),
    "set_route_slot": lambda o: SetRouteSlot(slot=OutputSlot(_int(o, "slot")), source=_source(_int(o, "source"))),
    "set_property": _property,
}


def parse_action(obj):
    """A typed mixer action, or the refusal saying why the request cannot be one."""
    if not isinstance(obj, dict) or obj.get("kind") not in _PARSERS:
        kind = obj.get("kind") if isinstance(obj, dict) else None
        return Refusal(RefusalKind.MALFORMED_REQUEST, f"unknown action kind {kind!r}")
    try:
        return _PARSERS[obj["kind"]](obj)
    except _Malformed as error:
        return Refusal(RefusalKind.MALFORMED_REQUEST, str(error))
    except ValueError as error:
        return Refusal(RefusalKind.INDEX_RANGE, f"{error}: that does not exist")


def _db_value(gain):
    if gain is None:
        return None
    return "-inf" if gain.is_silent else gain.db


def _linear(gain):
    return None if gain is None else gain.linear


def _bit(value):
    return None if value is None else int(value)


def _loops(loops):
    return [[hop.label for hop in loop.hops] for loop in sorted(loops, key=lambda loop: loop.label())]


def _layout(state, topology):
    layout = topology.StereoLayout.from_links(*state.links())
    tabs, strips = layout.tabs(), layout.strips()
    routed = sorted(set(_routing(state).values()))
    return {
        "tabs": [{"label": tab.label, "bus": tab.first_bus, "stereo": tab.is_stereo,
                  "pan": [strip.first_input for strip in strips if layout.pan_visible(strip.first_input, tab.first_bus)]}
                 for tab in tabs],
        "strips": [{"label": strip.label, "input": strip.first_input, "pan_visible": strip.pan_visible}
                   for strip in strips],
        "output_pairs": [{"label": label, "left": pair.left, "right": pair.right}
                         for pair, label in topology.OUTPUT_PAIRS.items()],
        "sources": [{"label": label, "left": pair.left.code, "right": pair.right.code}
                    for pair, label in topology.SOURCE_OPTIONS.items()],
        "source_labels": {str(code): topology.classify(Source(code)).label for code in routed},
    }


def _pan_value(pan):
    return None if pan is None else pan.value


# each column of the state body: the state read that fills it, and how each value is written out
_CELL_COLUMNS = {
    "fader": ("gain", _linear),
    "fader_db": ("gain", _db_value),
    "pan": ("pan", _pan_value),
    "mute": ("muted", _bit),
    "solo": ("soloed", _bit),
}
_MASTER_COLUMNS = {
    "fader": ("master_gain", _linear),
    "fader_db": ("master_gain", _db_value),
    "mute": ("master_muted", _bit),
}
_DEVICE_FIELDS = ("model", "serial", "api", "firmware")
_CELLS = tuple(MixCell(index // 44, index % 44) for index in range(704))
_BUSES = tuple(BusIndex(b) for b in range(16))


def _columns(state, columns, keys):
    return {name: [write(getattr(state, read)(key)) for key in keys] for name, (read, write) in columns.items()}


_EQ_FIELDS = ("mode", "bypass", "freq", "gain", "q")


def _eq(state):
    """Both EQs, decoded per field (freq Hz, gain dB, q, mode, bypass); None where a cell has not been reported."""
    def column(prop, length):
        values = []
        for index in range(length):
            raw = state.raw(Address(prop, index))
            values.append(None if raw is None else decode_value(prop, raw))
        return values
    return {name: {field: column(layout.prop(field), layout.length) for field in _EQ_FIELDS}
            for name, layout in (("input", INPUT_EQ), ("output", OUTPUT_EQ))}


# the analog input block, one byte per channel (domain/preamp.py); None where a value has not been reported
_INPUT_COLUMNS = {
    "gain": preamp.GAIN, "phase": preamp.PHASE, "pad": preamp.PAD, "phantom": preamp.PHANTOM,
    "jack": preamp.JACK_DETECT, "return_detect": preamp.RETURN_DETECT,
}


def _inputs(state):
    def column(prop):
        values = []
        for index in range(properties.spec(prop).length):
            raw = state.raw(Address(prop, index))
            values.append(None if raw is None else decode_value(prop, raw))
        return values
    return {name: column(prop) for name, prop in _INPUT_COLUMNS.items()}


def _fed_pairs(state, bus):
    """Labels of the output pairs fed by this stereo bus pair's mix today (what a stage takes over)."""
    left, right = Source(_topology.bus_mix_source(bus)), Source(_topology.bus_mix_source(bus + 1))
    return [label for pair, label in _topology.OUTPUT_PAIRS.items()
            if state.source(pair.left) == left and state.source(pair.right) == right]


def _live_anywhere(state, inp):
    for bus in range(16):
        cell = MixCell(bus, inp)
        gain = state.gain(cell)
        if gain is not None and not gain.is_silent and not state.muted(cell):
            return True
    return False


def _chain_json(state):
    """The recognised chain, and the stage and carrier pairs a chain may use with what each would take over.
    The chain is the 828's own numbering (domain/chain.py), so it reads the real catalogue."""
    recognised = _chain.recognise(state)
    return {
        "on": recognised.on,
        "stages": [{"bus": s.bus, "carrier": s.carrier, "label": s.label, "carrier_label": s.carrier_label}
                   for s in recognised.stages],
        "max_stages": _chain.MAX_STAGES,
        "stage_options": [{"bus": b, "label": _chain.pair_label(_topology.BUS_NAMES, b), "feeds": _fed_pairs(state, b)}
                          for b in _chain.STAGE_BUSES],
        "carrier_options": [{"input": i, "label": _chain.pair_label(_topology.INPUT_NAMES, i),
                             "live": _live_anywhere(state, i) or _live_anywhere(state, i + 1)}
                            for i in _chain.CARRIER_INPUTS],
    }


def _device(state):
    identity = state.identity()
    return {field: getattr(identity, field, None) for field in _DEVICE_FIELDS}


def _links(state):
    input_links, bus_links = state.links()
    return {"inputs": [int(v) for v in input_links], "buses": [int(v) for v in bus_links]}


def _routing(state):
    return {str(slot): source.code for slot in range(128) if (source := state.source(OutputSlot(slot))) is not None}


def snapshot_json(snapshot, topology=_topology):
    """The /api/state body: device, links, every cell, bus masters, routing, loops, policy and the page layout."""
    state = snapshot.state
    return {
        "status": str(snapshot.status),
        "read_only_reason": snapshot.read_only_detail,
        "device": _device(state),
        "links": _links(state),
        "cells": _columns(state, _CELL_COLUMNS, _CELLS),
        "bus_master": _columns(state, _MASTER_COLUMNS, _BUSES),
        "routing": _routing(state),
        "eq": _eq(state),
        "inputs": _inputs(state),
        "chain": _chain_json(state),
        "loops": _loops(snapshot.loops),
        "policy": {item.name: item.behaviour for item in OPEN_ITEMS},
        "layout": _layout(state, topology),
    }


def state_message(snapshot, topology=_topology):
    return {"type": "state", **snapshot_json(snapshot, topology)}


def _gain_change(name):
    def change(index, value):
        gain = Gain(max(value, 0.0))
        return {"prop": name, "index": index, "value": gain.linear, "db": _db_value(gain)}
    return change


def _plain_change(name):
    return lambda index, value: {"prop": name, "index": index, "value": value}


def _route_change(index, value):
    return {"prop": "routing", "index": index, "value": value, "label": _topology.classify(Source(value)).label}


_DISPLAYED = {
    P.MIX_FADER: _gain_change("fader"),
    P.MIX_PAN: _plain_change("pan"),
    P.MIX_MUTE: _plain_change("mute"),
    P.MIX_SOLO: _plain_change("solo"),
    P.BUS_FADER: _gain_change("master_fader"),
    P.BUS_MUTE: _plain_change("master_mute"),
    P.INPUT_LINK: _plain_change("input_link"),
    P.BUS_LINK: _plain_change("bus_link"),
    P.PATCH: _route_change,
    **{layout.prop(field): _plain_change(f"{prefix}_eq_{field}")
       for prefix, layout in (("in", INPUT_EQ), ("out", OUTPUT_EQ)) for field in _EQ_FIELDS},
    **{prop: _plain_change(f"in_{name}") for name, prop in _INPUT_COLUMNS.items()},
}


def delta_message(deltas):
    """The displayed changes among these deltas, or None when none of them is shown on the page."""
    changes = [_DISPLAYED[d.address.prop](d.address.index, decode_value(d.address.prop, d.new))
               for d in deltas if d.address.prop in _DISPLAYED]
    return {"type": "delta", "changes": changes} if changes else None


def meters_message(levels):
    """Live levels in dBFS (null = nothing), per mixer input 0-43 and per bus 0-15 (domain/meters.py)."""
    return {"type": "meters", "inputs": list(levels.inputs), "buses": list(levels.buses)}


def status_message(status, detail, loops):
    return {"type": "status", "status": str(status), "read_only_reason": detail, "loops": _loops(loops)}


def outcome_json(outcome):
    if isinstance(outcome, ChangeOutcome.Accepted):
        return {"type": "accepted", "accepted": True, "sent": [frame.hex(" ") for frame in outcome.sent]}
    refusal = outcome.refusal
    return {
        "type": "refused", "accepted": False, "kind": str(refusal.kind), "reason": f"Refused: {refusal.detail}",
        "path": None if refusal.path is None else [hop.label for hop in refusal.path.hops],
    }
