"""The monitor chain (docs/monitor-chain.md): native sources, recognition, and the ordered on/off plans."""
from _motu_mixer.domain import chain
from _motu_mixer.domain.chain import ChainState, Stage, native_source, plan_off, plan_on, recognise
from _motu_mixer.domain.loop_guard import Accepted, SpdifLoopGuard
from _motu_mixer.domain.properties import PropertyId as P
from _motu_mixer.domain.state import MixerState
from _motu_mixer.domain.status import Refusal, RefusalKind
from _motu_mixer.domain.values import Address, Gain, MixCell, Report, Source, Write

UNITY = (1 << 24).to_bytes(4, "big")
LIVE_SLOTS = {   # the unit's mixer-input feeds and Main jacks on 2026-11-08
    **{76 + i: code for i, code in enumerate((47, 34, 50, 37, 46, 33, 45, 32, 49, 36, 58, 59))},
    **{88 + k: code for k, code in enumerate((60, 61, 62, 63, 68, 69, 70, 71, 64, 65, 66, 67, 72, 73, 74, 75))},
    **{104 + k: k for k in range(14)}, 118: 90, 119: 91, 37: 76, 39: 77,
}
ADAT_B_7_8, REVERB_RETURN, ANA_5_6, ANA_7_8, COMPUTER_13_14 = 26, 42, 6, 8, 40


def unit(patch=(), faders=(), mutes=(), links=(), bus_links=(), solos=()):
    """A state with the unit's native feeds, plus the given overrides; faders are (bus, input) at unity."""
    state = MixerState()
    for slot, code in {**LIVE_SLOTS, **dict(patch)}.items():
        state.apply(Report(Address(P.PATCH, slot), bytes([code])))
    for index in range(16):
        state.apply(Report(Address(P.BUS_FADER, index), UNITY))
    for bus, inp in faders:
        state.apply(Report(Address(P.MIX_FADER, bus * 44 + inp), UNITY))
    for bus, inp in mutes:
        state.apply(Report(Address(P.MIX_MUTE, bus * 44 + inp), b"\x01"))
    for inp in links:
        state.apply(Report(Address(P.INPUT_LINK, inp), b"\x01"))
    for bus in bus_links:
        state.apply(Report(Address(P.BUS_LINK, bus), b"\x01"))
    for bus, inp in solos:
        state.apply(Report(Address(P.MIX_SOLO, bus * 44 + inp), b"\x01"))
    return state


def one_stage_on():
    """Main → ADAT B 7-8 → Ana 5-6 → Main jacks, as plan_on builds it."""
    return unit(patch={102: 76, 103: 77, 37: 82, 39: 83}, faders=[(6, 26)], links=[26], bus_links=[6], solos=[(6, 26)])


def test_every_mixer_input_slot_and_the_main_jacks_have_the_unit_s_native_source():
    assert all(native_source(slot) == code for slot, code in LIVE_SLOTS.items())
    assert native_source(37) == 76 and native_source(39) == 77
    for bad in (75, 120, 0):
        try:
            native_source(bad)
        except ValueError:
            continue
        raise AssertionError(bad)


def test_a_stage_is_an_ana_pair_and_a_carrier_a_pair_from_s_pdif_to_computer_each_used_once():
    assert chain.validate((Stage(ANA_5_6, ADAT_B_7_8),)) is None
    assert chain.validate(tuple(Stage(b, c) for b, c in ((2, 10), (4, 12), (6, 40), (8, 38)))) is None
    for stages, why in (
        ((), "at least one"),
        ((Stage(0, 42),), "Main 1 cannot be a stage"),
        ((Stage(10, 42),), "Phones 1 cannot be a stage"),
        ((Stage(6, 0),), "Mic 1 cannot carry"),
        ((Stage(6, 41),), "cannot carry"),                      # the second input of a pair
        ((Stage(6, REVERB_RETURN),), "reverb is applied"),      # W8: the reverb sits on the return's path
        ((Stage(6, 26), Stage(6, 40)), "only once"),
        ((Stage(6, 26), Stage(8, 26)), "only one stage"),
        (tuple(Stage(b, 10 + 2 * k) for k, b in enumerate((2, 4, 6, 8, 2))), "at most 4"),
    ):
        refusal = chain.validate(stages)
        assert isinstance(refusal, Refusal) and refusal.kind is RefusalKind.CHAIN and why in refusal.detail, stages


def test_the_native_unit_has_no_chain_and_a_built_one_is_recognised_from_the_state_alone():
    assert recognise(unit()) == ChainState(False, ())
    assert recognise(one_stage_on()) == ChainState(True, (Stage(ANA_5_6, ADAT_B_7_8),))
    two = unit(patch={102: 76, 103: 77, 37: 84, 39: 85, 116: 82, 117: 83},
               faders=[(6, 26), (8, 40)], links=[26, 40], bus_links=[6, 8])
    assert recognise(two) == ChainState(True, (Stage(ANA_5_6, ADAT_B_7_8), Stage(ANA_7_8, COMPUTER_13_14)))
    # the jacks fed by a bus with no carrier behind it, or a carrier that is silent, is not a chain
    assert recognise(unit(patch={37: 82, 39: 83})) == ChainState(False, ())
    assert recognise(unit(patch={102: 76, 103: 77, 37: 82, 39: 83})) == ChainState(False, ())
    assert recognise(unit(patch={102: 76, 103: 77, 37: 82, 39: 83}, faders=[(6, 26)], mutes=[(6, 26)])) == ChainState(False, ())
    # a state without the patch table yet
    assert recognise(MixerState()) == ChainState(False, ())


def test_plan_on_orders_links_silencing_patches_levels_and_the_jacks_last():
    state = unit(faders=[(0, 26), (12, 27)])        # ADAT B 7-8 is live on Main and on Phones 2 L today
    writes = plan_on((Stage(ANA_5_6, ADAT_B_7_8),), state)
    assert writes == (
        Write(Address(P.BUS_LINK, 6), True),
        Write(Address(P.INPUT_LINK, 26), True),
        Write(Address(P.MIX_FADER, 0 * 44 + 26), Gain(0.0)),       # silent on Main before Main is patched into it
        Write(Address(P.MIX_FADER, 12 * 44 + 27), Gain(0.0)),
        Write(Address(P.PATCH, 102), Source(76)),
        Write(Address(P.PATCH, 103), Source(77)),
        Write(Address(P.MIX_FADER, 6 * 44 + 26), Gain(1.0)),       # unity on its stage
        Write(Address(P.MIX_SOLO, 6 * 44 + 26), True),             # alone on its stage (W8)
        Write(Address(P.PATCH, 37), Source(82)),                   # the jacks last
        Write(Address(P.PATCH, 39), Source(83)),
    )


def test_plan_on_writes_only_what_differs_and_chains_stage_to_stage():
    state = unit(links=[26, 40], bus_links=[6, 8], solos=[(6, 26), (8, 40)])
    writes = plan_on((Stage(ANA_5_6, ADAT_B_7_8), Stage(ANA_7_8, COMPUTER_13_14)), state)
    assert writes == (
        Write(Address(P.PATCH, 102), Source(76)), Write(Address(P.PATCH, 103), Source(77)),
        Write(Address(P.PATCH, 116), Source(82)), Write(Address(P.PATCH, 117), Source(83)),   # stage 2 carries stage 1
        Write(Address(P.MIX_FADER, 6 * 44 + 26), Gain(1.0)),
        Write(Address(P.MIX_FADER, 8 * 44 + 40), Gain(1.0)),
        Write(Address(P.PATCH, 37), Source(84)), Write(Address(P.PATCH, 39), Source(85)),
    )
    # a muted carrier cell or a muted / lowered stage master is opened
    muted = unit(links=[26], bus_links=[6], mutes=[(6, 26)])
    muted.apply(Report(Address(P.BUS_MUTE, 6), b"\x01"))
    muted.apply(Report(Address(P.BUS_FADER, 6), (1 << 23).to_bytes(4, "big")))
    writes = plan_on((Stage(ANA_5_6, ADAT_B_7_8),), muted)
    assert Write(Address(P.MIX_MUTE, 6 * 44 + 26), False) in writes
    assert Write(Address(P.BUS_FADER, 6), Gain(1.0)) in writes and Write(Address(P.BUS_MUTE, 6), False) in writes
    assert isinstance(plan_on((Stage(0, 26),), state), Refusal)


def test_the_built_chain_is_what_recognise_reads_back_and_passes_the_feedback_guard():
    state = unit(faders=[(0, 26)])
    stages = (Stage(ANA_5_6, ADAT_B_7_8), Stage(ANA_7_8, COMPUTER_13_14))
    writes = plan_on(stages, state)
    after = state.with_writes(writes)
    assert recognise(after) == ChainState(True, stages)
    assert isinstance(SpdifLoopGuard().check(state, after, writes), Accepted)
    assert SpdifLoopGuard().paths(after) == frozenset()


def test_plan_off_hands_everything_back_jacks_first_and_needs_a_chain_to_be_on():
    state = one_stage_on()
    writes = plan_off(state)
    assert writes == (
        Write(Address(P.PATCH, 37), Source(76)), Write(Address(P.PATCH, 39), Source(77)),
        Write(Address(P.MIX_SOLO, 6 * 44 + 26), False),
        Write(Address(P.MIX_FADER, 6 * 44 + 26), Gain(0.0)),
        Write(Address(P.PATCH, 102), Source(74)), Write(Address(P.PATCH, 103), Source(75)),
    )
    after = state.with_writes(writes)
    assert recognise(after) == ChainState(False, ())
    assert all(after.source(slot).code == native_source(slot) for slot in (37, 39, 102, 103))
    refusal = plan_off(unit())
    assert isinstance(refusal, Refusal) and refusal.kind is RefusalKind.CHAIN


def test_a_stage_s_other_live_inputs_are_named_and_silenced_by_the_carrier_s_solo():
    # W8 (2026-11-08): Ana 7-8 also mixed Computer 1-2 and 9-10, so the music arrived twice (+6 dB) until the carrier was soloed
    state = unit(faders=[(8, 28), (9, 29), (8, 36), (9, 37)], links=[28, 36], bus_links=[8])
    assert chain.live_inputs(state, 8) == (28, 29, 36, 37)
    assert chain.live_inputs(state, 6) == ()
    writes = plan_on((Stage(ANA_7_8, COMPUTER_13_14),), state)
    assert Write(Address(P.MIX_SOLO, 8 * 44 + 40), True) in writes
    after = state.with_writes(writes)
    assert after.soloed(MixCell(8, 40)) and recognise(after).on
    off = plan_off(after)
    assert off[2] == Write(Address(P.MIX_SOLO, 8 * 44 + 40), False)
    assert not after.with_writes(off).soloed(MixCell(8, 40))
    assert all(after.with_writes(off).gain(MixCell(b, i)) == Gain(1.0) for b, i in ((8, 28), (9, 29), (8, 36), (9, 37))), "the stage's own mix is untouched"


def test_a_carrier_live_on_its_own_source_bus_is_a_cycle_the_guard_refuses_and_reports():
    before = one_stage_on()
    raise_on_main = (Write(Address(P.MIX_FADER, 0 * 44 + 26), Gain(0.5)),)
    after = before.with_writes(raise_on_main)
    verdict = SpdifLoopGuard().check(before, after, raise_on_main)
    assert isinstance(verdict, Refusal) and verdict.kind is RefusalKind.LOOP
    assert verdict.path.is_cycle and verdict.path.label() == "Main 1 → Main 1 mix → ADAT B 7 → Main 1"
    paths = SpdifLoopGuard().paths(after)
    assert len(paths) == 1 and next(iter(paths)).is_cycle
    # the same cycle arriving as a unit report is shown, and an unrelated change is still accepted
    unrelated = (Write(Address(P.MIX_FADER, 2 * 44 + 3), Gain(0.5)),)
    assert isinstance(SpdifLoopGuard().check(after, after.with_writes(unrelated), unrelated), Accepted)
