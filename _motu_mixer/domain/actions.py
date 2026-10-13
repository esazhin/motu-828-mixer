"""Mixer actions and the planner that turns one action into the property writes CueMix would make."""
from dataclasses import dataclass

from . import properties, topology as _topology
from .properties import PropertyId as P, ValueType
from .status import Refusal, RefusalKind
from .eq import check_eq_value, is_eq_prop
from .values import Address, BusIndex, Gain, InputIndex, OutputPair, OutputSlot, Pan, Source, Write


@dataclass(frozen=True)
class SetLevel:
    bus: int
    input: int
    db: float | str


@dataclass(frozen=True)
class SetPan:
    bus: int
    input: int
    pan: float


@dataclass(frozen=True)
class SetMute:
    bus: int
    input: int
    on: bool


@dataclass(frozen=True)
class SetSolo:
    bus: int
    input: int
    on: bool


@dataclass(frozen=True)
class SetInputLink:
    input: int
    on: bool


@dataclass(frozen=True)
class SetBusLink:
    bus: int
    on: bool


@dataclass(frozen=True)
class SetMasterLevel:
    bus: int
    db: float | str


@dataclass(frozen=True)
class SetMasterMute:
    bus: int
    on: bool


@dataclass(frozen=True)
class SetRoute:
    output_slots: tuple[int, int]
    sources: tuple[int, int]


@dataclass(frozen=True)
class SetRouteSlot:
    slot: int
    source: int


@dataclass(frozen=True)
class SetProperty:
    prop: int
    index: int
    value: float | int


MixerAction = (SetLevel | SetPan | SetMute | SetSolo | SetInputLink | SetBusLink | SetMasterLevel
               | SetMasterMute | SetRoute | SetRouteSlot | SetProperty)

_CELL_ACTIONS = {
    SetLevel: (P.MIX_FADER, lambda a: Gain.from_db(a.db).clamped()),
    SetPan: (P.MIX_PAN, lambda a: Pan(a.pan)),
    SetMute: (P.MIX_MUTE, lambda a: bool(a.on)),
    SetSolo: (P.MIX_SOLO, lambda a: bool(a.on)),
}


def _slot_refusal(slot: OutputSlot) -> Refusal | None:
    if slot.writable:
        return None
    return Refusal(RefusalKind.SLOT_NOT_WRITABLE, f"slot {slot} feeds a mixer input and cannot be changed here")


def _link(prop, index, on) -> tuple[Write, ...] | Refusal:
    if index % 2:
        return Refusal(RefusalKind.ODD_LINK_INDEX, f"index {index}: only even indexes can be linked")
    return (Write(Address(prop, index), bool(on)),)


def _route(action, topology) -> tuple[Write, ...] | Refusal:
    slots = [OutputSlot(s) for s in action.output_slots]
    for slot in slots:
        if refusal := _slot_refusal(slot):
            return refusal
    pair = OutputPair(*slots)
    if pair not in topology.OUTPUT_PAIRS:
        return Refusal(RefusalKind.SLOT_NOT_WRITABLE, f"slots {pair.left}/{pair.right} are not an output pair")
    left, right = (Source(code) for code in action.sources)
    return (Write(Address(P.PATCH, pair.left), left), Write(Address(P.PATCH, pair.right), right))


def _raw_value(prop, value_type, value):
    if prop == P.PATCH:
        return Source(int(value))
    if value_type is ValueType.F824:
        return Pan(value) if prop == P.MIX_PAN else Gain(max(float(value), 0.0)).clamped()
    return bool(value) if value_type is ValueType.BYTE else int(value)


def _property(action, spec) -> tuple[Write, ...] | Refusal:
    entry = spec(action.prop)
    if entry is None or not entry.writable:
        return Refusal(RefusalKind.NOT_WRITABLE_PROPERTY, f"property {action.prop} cannot be changed here")
    if not 0 <= action.index < entry.length:
        return Refusal(RefusalKind.INDEX_RANGE, f"property {action.prop} has no index {action.index}")
    if action.prop == P.PATCH and (refusal := _slot_refusal(OutputSlot(action.index))):
        return refusal
    if action.prop in (P.INPUT_LINK, P.BUS_LINK):
        return _link(action.prop, action.index, action.value)
    if is_eq_prop(action.prop):
        value = check_eq_value(action.prop, action.index, action.value)
        return value if isinstance(value, Refusal) else (Write(Address(action.prop, action.index), value),)
    return (Write(Address(action.prop, action.index), _raw_value(action.prop, entry.value_type, action.value)),)


def _plan(action, state, spec, topology):
    if isinstance(action, SetProperty):
        return _property(action, spec)
    if isinstance(action, SetRoute):
        return _route(action, topology)
    if isinstance(action, SetRouteSlot):
        slot = OutputSlot(action.slot)
        return _slot_refusal(slot) or (Write(Address(P.PATCH, slot), Source(action.source)),)
    if isinstance(action, SetInputLink):
        return _link(P.INPUT_LINK, InputIndex(action.input), action.on)
    if isinstance(action, SetBusLink):
        return _link(P.BUS_LINK, BusIndex(action.bus), action.on)
    layout = topology.StereoLayout.from_links(*state.links())
    if isinstance(action, SetMasterLevel):
        return (Write(Address(P.BUS_FADER, layout.master_bus(BusIndex(action.bus))),
                      Gain.from_db(action.db).clamped()),)
    if isinstance(action, SetMasterMute):
        return (Write(Address(P.BUS_MUTE, layout.master_bus(BusIndex(action.bus))), bool(action.on)),)
    prop, value = _CELL_ACTIONS[type(action)]
    cell = layout.primary_cell(InputIndex(action.input), BusIndex(action.bus))
    return (Write(Address(prop, cell.index), value(action)),)


def plan(action: MixerAction, state, spec=properties.spec, topology=_topology) -> tuple[Write, ...] | Refusal:
    """The writes for one action, or why it cannot be made; a stereo pair is written at its one primary cell."""
    try:
        return _plan(action, state, spec, topology)
    except ValueError as error:
        return Refusal(RefusalKind.INDEX_RANGE, str(error))
