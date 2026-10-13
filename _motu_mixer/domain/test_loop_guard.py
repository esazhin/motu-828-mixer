from dataclasses import dataclass

from _motu_mixer.domain.loop_guard import SpdifLoopGuard
from _motu_mixer.domain.state import MixerState
from _motu_mixer.domain.status import Refusal, RefusalKind
from _motu_mixer.domain.topology import SourceKind
from _motu_mixer.domain.values import Address, BusIndex, OutputSlot, Report, Source, Write

UNITY = (1 << 24).to_bytes(4, "big")


@dataclass(frozen=True)
class Classified:
    kind: SourceKind
    label: str


class StubTopology:
    """Stands in for the topology catalogue with its own numbering and records everything the guard asks."""

    def __init__(self):
        self.asked = []
        self.classified = []

    def classify(self, source):
        self.asked.append("classify")
        self.classified.append(source)
        code = source.code
        if code in (58, 59):
            kind = SourceKind.SPDIF_IN
        elif 17 <= code <= 30:
            kind = SourceKind.BUS_MIX
        elif code in (40, 41):
            kind = SourceKind.REVERB
        elif 110 <= code <= 126:
            kind = SourceKind.POST_FX
        elif code == 127:
            kind = SourceKind.NOTHING
        else:
            kind = SourceKind.LOCAL
        return Classified(kind, f"test source {code}")

    def mixer_input_slot(self, i):
        self.asked.append("mixer_input_slot")
        return 119 - i

    def bus_mix_source(self, b):
        self.asked.append("bus_mix_source")
        return 30 - b

    def post_fx_input(self, code):
        self.asked.append("post_fx_input")
        return code - 110

    @property
    def REVERB_SOURCES(self):
        self.asked.append("REVERB_SOURCES")
        return (40, 41)

    @property
    def SPDIF_IN(self):
        self.asked.append("SPDIF_IN")
        return (58, 59)

    @property
    def SPDIF_OUT_SLOTS(self):
        self.asked.append("SPDIF_OUT_SLOTS")
        return (58, 59)


def _state(patch=(), faders=(), mutes=()):
    state = MixerState()
    for slot, code in patch:
        state.apply(Report(Address(5010, slot), bytes([code])))
    for bus, inp in faders:
        state.apply(Report(Address(1016, bus * 44 + inp), UNITY))
    for bus, inp in mutes:
        state.apply(Report(Address(1019, bus * 44 + inp), b"\x01"))
    return state


def _only_path(paths):
    assert len(paths) == 1
    return next(iter(paths))


def _ends(path):
    return path.hops[0].ref, path.hops[-1].ref


def test_guard_traces_sources_through_the_numbering_the_topology_gives():
    topology = StubTopology()
    guard = SpdifLoopGuard(topology=topology)

    # S/PDIF in R patched straight to S/PDIF out L
    direct = _only_path(guard.paths(_state(patch=[(58, 59)])))
    assert _ends(direct) == (Source(59), OutputSlot(58))

    # S/PDIF in L feeds mixer input 2 (slot 117 here), bus 1 carries it, bus 1's mix (source 29 here) feeds S/PDIF out R
    through_bus = _state(patch=[(117, 58), (59, 29)], faders=[(1, 2)])
    bus_path = _only_path(guard.paths(through_bus))
    assert _ends(bus_path) == (Source(58), OutputSlot(59))
    assert any(isinstance(hop.ref, BusIndex) and hop.ref == 1 for hop in bus_path.hops)
    assert {Source(58), Source(29)} <= set(topology.classified)

    # the same route with the cell muted carries nothing
    assert guard.paths(_state(patch=[(117, 58), (59, 29)], faders=[(1, 2)], mutes=[(1, 2)])) == frozenset()

    # through the reverb: S/PDIF on reverb bus 14, its return (source 40 here) on input 7, bus 3's mix to S/PDIF out
    reverb = _state(patch=[(114, 58), (112, 40), (58, 27)], faders=[(14, 5), (3, 7)])
    assert _ends(_only_path(guard.paths(reverb))) == (Source(58), OutputSlot(58))

    # the post-FX source of input 4 (source 114 here), which S/PDIF in R feeds
    post_fx = _state(patch=[(115, 59), (58, 114)])
    assert _ends(_only_path(guard.paths(post_fx))) == (Source(59), OutputSlot(58))

    # sources the topology calls local or nothing carry no S/PDIF
    assert guard.paths(_state(patch=[(58, 50), (59, 127), (110, 127)])) == frozenset()

    # a change that would create the direct path is refused with it
    before = _state(patch=[(58, 50)])
    writes = (Write(Address(5010, 58), Source(59)),)
    refusal = guard.check(before, before.with_writes(writes), writes)
    assert isinstance(refusal, Refusal)
    assert refusal.kind is RefusalKind.LOOP
    assert _ends(refusal.path) == (Source(59), OutputSlot(58))

    for name in ("classify", "mixer_input_slot", "bus_mix_source", "post_fx_input",
                 "REVERB_SOURCES", "SPDIF_IN", "SPDIF_OUT_SLOTS"):
        assert name in topology.asked


# --- the guard over the 828's own numbering, from the unit's 2026-10-12 routing ---

from _motu_mixer.domain import topology
from _motu_mixer.domain.loop_guard import Accepted
from _motu_mixer.domain.properties import PropertyId as P
from _motu_mixer.domain.values import Gain, MixCell

S_PDIF_L, S_PDIF_R, LINE_3, MIC_1 = 10, 11, 2, 0
REVERB_RETURN_L = 42
MAIN_1, ANA_1, ANA_2, REVERB_1 = 0, 2, 3, 14


def _raw_gain(db):
    return Gain.from_db(db).to_824().to_bytes(4, "big", signed=True)


def _unit_state(patch=(), faders=(), mutes=(), solos=(), master_mutes=()):
    """Mixer inputs fed as on 2026-10-12 and S/PDIF out L/R fed by the Ana 1-2 mix, plus the given cells."""
    feeds = [topology.local_input_source(i) for i in range(10)] + [58, 59]
    feeds += list(topology.ADAT_A) + list(topology.ADAT_B) + list(range(14)) + [90, 91]
    state = MixerState()
    for i, code in enumerate(feeds):
        state.apply(Report(Address(P.PATCH, 76 + i), bytes([code])))
    for slot, code in [(58, 78), (59, 79), *patch]:
        state.apply(Report(Address(P.PATCH, slot), bytes([code])))
    for (bus, inp), db in dict(faders).items():
        state.apply(Report(Address(P.MIX_FADER, MixCell(bus, inp).index), _raw_gain(db)))
    for prop, cells in ((P.MIX_MUTE, mutes), (P.MIX_SOLO, solos)):
        for bus, inp in cells:
            state.apply(Report(Address(prop, MixCell(bus, inp).index), b"\x01"))
    for bus in master_mutes:
        state.apply(Report(Address(P.BUS_MUTE, bus), b"\x01"))
    return state


def _level(bus, inp, db):
    return Write(Address(P.MIX_FADER, MixCell(bus, inp).index), Gain.from_db(db))


def _mute(bus, inp, on):
    return Write(Address(P.MIX_MUTE, MixCell(bus, inp).index), on)


def _route(slot, code):
    return Write(Address(P.PATCH, slot), Source(code))


def _check(before, *writes):
    return SpdifLoopGuard().check(before, before.with_writes(writes), writes)


def _refused_with(outcome, label):
    assert isinstance(outcome, Refusal)
    assert outcome.kind is RefusalKind.LOOP
    assert outcome.path.label() == label
    assert outcome.detail == label


def test_routing_s_pdif_in_straight_to_s_pdif_out_is_refused_on_either_side_and_from_any_client():
    before = _unit_state()

    # S/PDIF in L/R to S/PDIF out L/R, from the routing grid or as raw patch writes from a LAN program
    _refused_with(_check(before, _route(58, 58), _route(59, 59)), "S/PDIF in L → S/PDIF out L")

    # one side alone is still a loop
    _refused_with(_check(before, _route(58, 58)), "S/PDIF in L → S/PDIF out L")
    _refused_with(_check(before, _route(58, 59)), "S/PDIF in R → S/PDIF out L")


def test_raising_s_pdif_on_a_bus_feeding_s_pdif_out_is_refused_whatever_solo_and_master_say():
    before = _unit_state()
    _refused_with(_check(before, _level(ANA_1, S_PDIF_L, -6.0)), "S/PDIF in L → S/PDIF L → Ana 1 → S/PDIF out L")

    # another channel soloed and the bus master muted do not make the path safe
    before = _unit_state(solos=[(ANA_1, LINE_3)], master_mutes=[ANA_1])
    _refused_with(_check(before, _level(ANA_1, S_PDIF_L, -6.0)), "S/PDIF in L → S/PDIF L → Ana 1 → S/PDIF out L")


def test_routing_a_bus_that_carries_s_pdif_to_s_pdif_out_is_refused():
    before = _unit_state(faders={(MAIN_1, S_PDIF_L): -1.1})
    _refused_with(_check(before, _route(58, 76), _route(59, 77)), "S/PDIF in L → S/PDIF L → Main 1 → S/PDIF out L")


def test_unmuting_s_pdif_on_a_bus_feeding_s_pdif_out_is_refused():
    before = _unit_state(faders={(ANA_1, S_PDIF_L): -6.0}, mutes=[(ANA_1, S_PDIF_L)])
    assert SpdifLoopGuard().paths(before) == frozenset()
    _refused_with(_check(before, _mute(ANA_1, S_PDIF_L, False)), "S/PDIF in L → S/PDIF L → Ana 1 → S/PDIF out L")


def test_a_path_through_a_mixer_input_patched_from_s_pdif_in_is_refused():
    before = _unit_state(patch=[(topology.mixer_input_slot(LINE_3), 58)], faders={(MAIN_1, LINE_3): 0.0})
    _refused_with(_check(before, _route(58, 76), _route(59, 77)), "S/PDIF in L → Line 3 → Main 1 → S/PDIF out L")


def test_a_path_through_the_reverb_return_is_refused():
    before = _unit_state(faders={(ANA_1, REVERB_RETURN_L): 0.0})
    _refused_with(
        _check(before, _level(REVERB_1, S_PDIF_L, -10.0)),
        "S/PDIF in L → S/PDIF L → Reverb 1 → Reverb return L → Ana 1 → S/PDIF out L",
    )


def test_a_path_through_a_channel_post_fx_source_is_refused():
    before = _unit_state(patch=[(topology.mixer_input_slot(LINE_3), 58)])
    _refused_with(_check(before, _route(58, 94)), "S/PDIF in L → Line 3 → Line 3 post-FX → S/PDIF out L")


def test_routing_s_pdif_in_to_a_computer_input_is_accepted():
    before = _unit_state()
    assert _check(before, _route(0, 58), _route(1, 59)) == Accepted()


def test_a_silent_or_muted_path_is_no_loop():
    guard = SpdifLoopGuard()
    assert guard.paths(_unit_state(faders={(ANA_1, S_PDIF_L): float("-inf")})) == frozenset()
    assert guard.paths(_unit_state(faders={(ANA_1, S_PDIF_L): -6.0}, mutes=[(ANA_1, S_PDIF_L)])) == frozenset()
    assert _check(_unit_state(), _level(ANA_1, S_PDIF_L, float("-inf"))) == Accepted()


def test_a_loop_already_present_allows_unrelated_and_weakening_changes_and_refuses_strengthening():
    guard = SpdifLoopGuard()
    before = _unit_state(faders={(ANA_1, S_PDIF_L): -6.0})
    present = _only_path(guard.paths(before))
    assert present.label() == "S/PDIF in L → S/PDIF L → Ana 1 → S/PDIF out L"

    assert _check(before, _level(MAIN_1, MIC_1, -3.0)) == Accepted()
    _refused_with(_check(before, _level(ANA_1, S_PDIF_L, 0.0)), present.label())
    assert _check(before, _level(ANA_1, S_PDIF_L, -12.0)) == Accepted()

    muted = (_mute(ANA_1, S_PDIF_L, True),)
    assert _check(before, *muted) == Accepted()
    assert guard.paths(before.with_writes(muted)) == frozenset()

    # a second route on top of the present one is refused too
    _refused_with(_check(before, _level(ANA_2, S_PDIF_R, -6.0)), "S/PDIF in R → S/PDIF R → Ana 2 → S/PDIF out R")


def test_a_device_report_creating_a_loop_shows_in_the_paths():
    guard = SpdifLoopGuard()
    state = _unit_state()
    assert guard.paths(state) == frozenset()
    state.apply(Report(Address(P.MIX_FADER, MixCell(ANA_1, S_PDIF_L).index), _raw_gain(-6.0)))
    assert {path.label() for path in guard.paths(state)} == {"S/PDIF in L → S/PDIF L → Ana 1 → S/PDIF out L"}


def test_check_answers_accepted_or_a_loop_refusal_and_paths_a_frozenset_of_loop_paths():
    from _motu_mixer.domain.status import LoopPath

    guard = SpdifLoopGuard()
    before = _unit_state()

    # check takes the state before, the state after and the writes that lead from one to the other
    accepted = guard.check(before, before.with_writes((_level(MAIN_1, MIC_1, -3.0),)), (_level(MAIN_1, MIC_1, -3.0),))
    assert accepted == Accepted()
    writes = (_level(ANA_1, S_PDIF_L, -6.0),)
    refused = guard.check(before, before.with_writes(writes), writes)
    assert refused.kind is RefusalKind.LOOP and isinstance(refused.path, LoopPath)

    # paths is empty with no loop, and holds LoopPaths when there is one
    assert guard.paths(before) == frozenset()
    found = guard.paths(before.with_writes(writes))
    assert isinstance(found, frozenset) and found
    assert all(isinstance(path, LoopPath) for path in found)
