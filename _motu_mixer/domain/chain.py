"""The monitor chain (docs/monitor-chain.md): Main → carrier → stage → … → Main jacks, one more 3-band EQ per stage.

A stage is an Ana bus pair whose output EQ becomes one more stage of the Main path; a carrier is the mixer-input pair
that brings the previous stage's mix into it (patch slots 76 + input, W7). Every slot a chain touches has one native
source the firmware gives it, so switching off needs no stored state: the hardware takes precedence as it was."""
from dataclasses import dataclass

from . import topology
from .properties import PropertyId as P
from .status import Refusal, RefusalKind
from .topology import BUS_NAMES, INPUT_NAMES
from .values import Address, BusIndex, Gain, InputIndex, MixCell, OutputSlot, Source, Write

MAIN_BUS = 0
STAGE_BUSES = (2, 4, 6, 8)                      # first bus of Ana 1-2, 3-4, 5-6, 7-8
CARRIER_INPUTS = tuple(range(10, 42, 2))        # first input of each borrowable pair: never Mic/Line 1-10
REVERB_RETURN = 42                              # never a carrier: its slots feed the reverb, so the mix would carry the effect (W8)
MAIN_JACKS = tuple(topology.LOCAL_BASE + o for o in topology.LOCAL_OUTPUT_LINES[:2])   # slots 37, 39
MAX_STAGES = len(STAGE_BUSES)
_MIXER_INPUT_BASE = 76
_FEEDS = (MAIN_BUS,) + STAGE_BUSES


@dataclass(frozen=True)
class Stage:
    bus: int        # first bus of the Ana pair
    carrier: int    # first input of the carrier pair

    def __post_init__(self):
        object.__setattr__(self, "bus", int(BusIndex(self.bus)))
        object.__setattr__(self, "carrier", int(InputIndex(self.carrier)))

    @property
    def label(self) -> str:
        return pair_label(BUS_NAMES, self.bus)

    @property
    def carrier_label(self) -> str:
        return pair_label(INPUT_NAMES, self.carrier)


@dataclass(frozen=True)
class ChainState:
    on: bool
    stages: tuple[Stage, ...]


def pair_label(names, first: int) -> str:
    left, right = names[first], names[first + 1]
    stem, _, a = left.rpartition(" ")
    return f"{stem} {a}-{right.rpartition(' ')[2]}"


def native_source(slot: int) -> int:
    """What the firmware feeds a mixer-input slot (76-119) or a Main jack when nothing is chained (§5 slot map)."""
    if slot in MAIN_JACKS:
        return topology.bus_mix_source(MAIN_BUS + MAIN_JACKS.index(slot))
    i = slot - _MIXER_INPUT_BASE
    if not 0 <= i < 44:
        raise ValueError(f"slot {slot} has no native source")
    if i < 10:
        return topology.local_input_source(i)
    if i < 12:
        return topology.SPDIF_IN[i - 10]
    if i < 20:
        return topology.ADAT_A[i - 12]
    if i < 28:
        return topology.ADAT_B[i - 20]
    if i < 42:
        return i - 28
    return topology.REVERB_SOURCES[i - 42]


def validate(stages) -> Refusal | None:
    def refuse(why):
        return Refusal(RefusalKind.CHAIN, why)
    if not stages:
        return refuse("a chain needs at least one stage")
    if len(stages) > MAX_STAGES:
        return refuse(f"at most {MAX_STAGES} stages: the Ana pairs")
    for stage in stages:
        if stage.bus not in STAGE_BUSES:
            return refuse(f"{BUS_NAMES[stage.bus]} cannot be a stage: stages are the Ana pairs")
        if stage.carrier in (REVERB_RETURN, REVERB_RETURN + 1):
            return refuse(f"{INPUT_NAMES[stage.carrier]} cannot carry a stage: the built-in reverb is applied on its path")
        if stage.carrier not in CARRIER_INPUTS:
            return refuse(f"{INPUT_NAMES[stage.carrier]} cannot carry a stage: carriers are stereo pairs from S/PDIF to Computer 13-14")
    buses, carriers = [s.bus for s in stages], [s.carrier for s in stages]
    if len(set(buses)) < len(buses):
        return refuse("a bus pair can be a stage only once")
    if len(set(carriers)) < len(carriers):
        return refuse("an input pair can carry only one stage")
    return None


def _fed_by(state, slot: int):
    """The bus whose stereo mix feeds the slot pair (slot, slot + 1) exactly, or None."""
    left, right = state.source(OutputSlot(slot)), state.source(OutputSlot(slot + 1))
    if left is None or right is None:
        return None
    bus = left.code - topology._BUS_MIX_BASE
    if bus in _FEEDS and right.code == left.code + 1:
        return bus
    return None


def _live(state, bus, inp) -> bool:
    cell = MixCell(bus, inp)
    gain = state.gain(cell)
    return gain is not None and not gain.is_silent and not state.muted(cell)


def live_inputs(state, bus: int) -> tuple[int, ...]:
    """The inputs live on either bus of the pair (bus, bus + 1): what a stage silences by soloing its carrier (W8)."""
    return tuple(sorted({inp for b in (bus, bus + 1) for inp in range(44) if _live(state, b, inp)}))


def recognise(state) -> ChainState:
    """The chain the unit's state describes: the stages feeding the Main jacks, each with the carrier that feeds it."""
    left, right = state.source(OutputSlot(MAIN_JACKS[0])), state.source(OutputSlot(MAIN_JACKS[1]))
    if left is None or right is None:
        return ChainState(False, ())
    bus = left.code - topology._BUS_MIX_BASE
    if bus not in STAGE_BUSES or right.code != left.code + 1:
        return ChainState(False, ())
    stages, seen = [], set()
    while bus is not None and bus not in seen:
        seen.add(bus)
        carrier = next((i for i in CARRIER_INPUTS
                        if _fed_by(state, _MIXER_INPUT_BASE + i) is not None and _live(state, bus, i)), None)
        if carrier is None:
            return ChainState(False, ())              # the jacks are fed by a bus, but not through a chain
        stages.append(Stage(bus, carrier))
        previous = _fed_by(state, _MIXER_INPUT_BASE + carrier)
        bus = None if previous == MAIN_BUS else previous
    if bus is not None:                               # cycle or dangling: not a chain
        return ChainState(False, ())
    stages.reverse()
    return ChainState(True, tuple(stages))


def _patch(state, slot: int, code: int, writes):
    if state.source(OutputSlot(slot)) != Source(code):
        writes.append(Write(Address(P.PATCH, slot), Source(code)))


def _borrowed(state, keep=()) -> tuple[int, ...]:
    """Carrier pairs whose slots a chain has patched (not at their native source) and that `keep` does not use."""
    out = []
    for i in CARRIER_INPUTS:
        if i in keep:
            continue
        for side in (0, 1):
            slot = _MIXER_INPUT_BASE + i + side
            source = state.source(OutputSlot(slot))
            if source is not None and source.code != native_source(slot):
                out.append(i)
                break
    return tuple(out)


def _hand_back(state, carrier: int, writes):
    """A borrowed pair goes back to the hardware: unsoloed and silent wherever a chain left it live, slots native."""
    for bus in range(16):
        for inp in (carrier, carrier + 1):
            cell = MixCell(bus, inp)
            if state.soloed(cell):
                writes.append(Write(Address(P.MIX_SOLO, cell.index), False))
            gain = state.gain(cell)
            if gain is not None and not gain.is_silent:
                writes.append(Write(Address(P.MIX_FADER, cell.index), Gain(0.0)))
    for side in (0, 1):
        slot = _MIXER_INPUT_BASE + carrier + side
        _patch(state, slot, native_source(slot), writes)


def plan_on(stages, state) -> tuple[Write, ...] | Refusal:
    """The ordered writes that build the chain: links, carriers silenced elsewhere, what an earlier chain borrowed and
    this one does not use handed back, patches, stage levels, jacks last.

    The carrier is soloed on its stage, so whatever else the stage pair mixes today stays silent while the chain is
    on and comes back untouched when it is off (W8: Ana 7-8 carrying Computer 1-2 as well doubled the music, +6 dB)."""
    stages = tuple(stages)
    if (refusal := validate(stages)) is not None:
        return refusal
    input_links, bus_links = state.links()
    layout = topology.StereoLayout.from_links(input_links, bus_links)
    writes = []
    for stage in stages:
        if not bus_links[stage.bus]:
            writes.append(Write(Address(P.BUS_LINK, stage.bus), True))
        if not input_links[stage.carrier]:
            writes.append(Write(Address(P.INPUT_LINK, stage.carrier), True))
    # a carrier must be silent on every bus but its own stage before its slot is patched, or the mix feeds itself
    for stage in stages:
        for bus in range(16):
            if bus in (stage.bus, stage.bus + 1):
                continue
            for inp in (stage.carrier, stage.carrier + 1):
                gain = state.gain(MixCell(bus, inp))
                if gain is not None and not gain.is_silent:
                    writes.append(Write(Address(P.MIX_FADER, MixCell(bus, inp).index), Gain(0.0)))
    # a stage change (a preset loaded while on, a stage removed) must not leave the previous chain's carriers behind
    for carrier in _borrowed(state, keep={s.carrier for s in stages}):
        _hand_back(state, carrier, writes)
    previous = MAIN_BUS
    for stage in stages:
        for side in (0, 1):
            _patch(state, _MIXER_INPUT_BASE + stage.carrier + side, topology.bus_mix_source(previous) + side, writes)
        previous = stage.bus
    for stage in stages:
        cell = layout.primary_cell(stage.carrier, stage.bus)
        if state.gain(cell) != Gain(1.0):
            writes.append(Write(Address(P.MIX_FADER, cell.index), Gain(1.0)))
        if state.muted(cell):
            writes.append(Write(Address(P.MIX_MUTE, cell.index), False))
        if not state.soloed(cell):
            writes.append(Write(Address(P.MIX_SOLO, cell.index), True))
        if state.master_gain(BusIndex(stage.bus)) != Gain(1.0):
            writes.append(Write(Address(P.BUS_FADER, stage.bus), Gain(1.0)))
        if state.master_muted(BusIndex(stage.bus)):
            writes.append(Write(Address(P.BUS_MUTE, stage.bus), False))
    for side, slot in enumerate(MAIN_JACKS):
        _patch(state, slot, topology.bus_mix_source(stages[-1].bus) + side, writes)
    return tuple(writes)


def plan_off(state) -> tuple[Write, ...] | Refusal:
    """The ordered writes that hand everything back: jacks first, then every borrowed carrier (the recognised chain's,
    last stage first, and anything an earlier chain left behind) unsoloed, silenced and unpatched."""
    chain = recognise(state)
    borrowed = _borrowed(state)
    if not chain.on and not borrowed:
        return Refusal(RefusalKind.CHAIN, "no chain is on")
    writes = []
    for slot in MAIN_JACKS:
        _patch(state, slot, native_source(slot), writes)
    order = [s.carrier for s in reversed(chain.stages)] + [c for c in borrowed if c not in {s.carrier for s in chain.stages}]
    for carrier in order:
        _hand_back(state, carrier, writes)
    return tuple(writes)
