import pytest

from _motu_mixer.domain import topology
from _motu_mixer.domain.topology import SourceKind, StereoLayout, classify
from _motu_mixer.domain.values import MixCell, OutputPair, Source, SourcePair

# the link arrays the unit reported on 2026-10-12
INPUT_LINKS = tuple(i in (10, 28, 30, 34, 36, 38, 40, 42) for i in range(44))
BUS_LINKS = tuple(b in (0, 2, 8, 10, 12, 14) for b in range(16))
LINE_3, LINE_4, LINE_5, COMPUTER_1, COMPUTER_2 = 2, 3, 4, 28, 29
MAIN_1, MAIN_2, ANA_3 = 0, 1, 4


@pytest.fixture
def layout():
    return StereoLayout.from_links(INPUT_LINKS, BUS_LINKS)


def test_bus_tabs_follow_the_bus_links(layout):
    assert [tab.label for tab in layout.tabs()] == [
        "Main 1-2", "Ana 1-2", "Ana 3", "Ana 4", "Ana 5", "Ana 6", "Ana 7-8", "Phones 1-2", "Phones 3-4",
        "Reverb 1-2",
    ]
    label, first_bus, is_stereo = layout.tabs()[2]
    assert (label, first_bus, is_stereo) == ("Ana 3", ANA_3, False)
    assert layout.tabs()[0] == ("Main 1-2", MAIN_1, True)


def test_input_strips_follow_the_input_links(layout):
    strips = layout.strips()
    assert len(strips) == 36
    labels = [strip.label for strip in strips]
    assert labels[:11] == ["Mic 1", "Mic 2"] + [f"Line {n}" for n in range(3, 11)] + ["S/PDIF L-R"]
    assert {"Computer 1-2", "Computer 5", "Computer 6", "Computer 13-14", "Reverb return L-R"} <= set(labels)
    label, first_input, pan_visible = strips[2]
    assert (label, first_input, pan_visible) == ("Line 3", LINE_3, True)
    assert ("Computer 1-2", COMPUTER_1, False) in strips


def test_links_are_read_at_even_indexes_only():
    odd_only = StereoLayout.from_links(tuple(i == 3 for i in range(44)), tuple(b == 5 for b in range(16)))
    assert len(odd_only.strips()) == 44 and len(odd_only.tabs()) == 16
    assert odd_only.primary_cell(LINE_4, 5) == MixCell(5, LINE_4)


def test_a_linked_pair_writes_only_its_first_cell(layout):
    assert layout.primary_cell(LINE_3, MAIN_1) == MixCell(0, 2)
    assert layout.primary_cell(LINE_3, MAIN_2) == MixCell(0, 2)
    assert layout.primary_cell(COMPUTER_1, MAIN_1) == MixCell(0, 28)
    assert layout.primary_cell(COMPUTER_2, MAIN_2) == MixCell(0, 28)
    assert layout.primary_cell(LINE_3, ANA_3) == MixCell(4, 2)
    assert layout.master_bus(MAIN_1) == 0 and layout.master_bus(MAIN_2) == 0
    assert layout.master_bus(ANA_3) == ANA_3


def test_pan_shows_only_on_stereo_tabs_and_never_on_a_linked_strip(layout):
    assert layout.pan_visible(LINE_3, MAIN_1)
    assert not layout.pan_visible(LINE_3, ANA_3)
    assert not layout.pan_visible(COMPUTER_1, MAIN_1)
    assert not layout.pan_visible(COMPUTER_2, MAIN_1)
    assert layout.pan_visible(LINE_5, MAIN_2)


@pytest.mark.parametrize("code, kind, label, index", [
    (58, SourceKind.SPDIF_IN, "S/PDIF in L", None),
    (59, SourceKind.SPDIF_IN, "S/PDIF in R", None),
    (76, SourceKind.BUS_MIX, "Main 1 mix", 0),
    (89, SourceKind.BUS_MIX, "Phones 4 mix", 13),
    (90, SourceKind.REVERB, "Reverb L", None),
    (91, SourceKind.REVERB, "Reverb R", None),
    (92, SourceKind.POST_FX, "Mic 1 post-FX", 0),
    (101, SourceKind.POST_FX, "Line 10 post-FX", 9),
    (127, SourceKind.NOTHING, "nothing", None),
    (0, SourceKind.HOST, "Computer out 1", None),
    (31, SourceKind.HOST, "Computer out 32", None),
    (47, SourceKind.LOCAL, "Mic 1", None),
    (34, SourceKind.LOCAL, "Mic 2", None),
    (50, SourceKind.LOCAL, "Line 3", None),
    (36, SourceKind.LOCAL, "Line 10", None),
    (60, SourceKind.ADAT, "ADAT A 1", None),
    (64, SourceKind.ADAT, "ADAT B 1", None),
    (75, SourceKind.ADAT, "ADAT B 8", None),
    (35, SourceKind.UNKNOWN, "source 35 (unknown)", None),
    (125, SourceKind.UNKNOWN, "source 125 (unknown)", None),
])
def test_classify_names_every_source_range(code, kind, label, index):
    assert classify(Source(code)) == (kind, label, index)
    assert classify(Source(code)).kind is kind and classify(Source(code)).label == label


def test_slot_and_source_numbering():
    assert topology.mixer_input_slot(10) == 86
    assert topology.mixer_input_slot(43) == 119
    assert topology.bus_mix_source(2) == 78 and topology.bus_mix_source(13) == 89
    with pytest.raises(ValueError):
        topology.bus_mix_source(14)
    assert topology.post_fx_input(92) == 0 and topology.post_fx_input(101) == 9
    with pytest.raises(ValueError):
        topology.post_fx_input(102)
    assert topology.REVERB_SOURCES == (90, 91)
    assert topology.SPDIF_IN == (58, 59) and topology.SPDIF_OUT_SLOTS == (58, 59)


def test_routing_choices_carry_display_labels():
    pairs = topology.OUTPUT_PAIRS
    assert pairs[OutputPair(37, 39)] == "Main 1-2"
    assert pairs[OutputPair(53, 56)] == "Line out 3-4"
    assert pairs[OutputPair(54, 51)] == "Line out 5-6"
    assert pairs[OutputPair(43, 40)] == "Line out 9-10"
    assert pairs[OutputPair(58, 59)] == "S/PDIF out"
    assert pairs[OutputPair(0, 1)] == "Computer in 1-2"
    assert OutputPair(43, 40) in pairs
    assert all(isinstance(pair, OutputPair) for pair in pairs)
    assert len(pairs) == 5 + 2 + 1 + 4 + 4 + 16

    sources = topology.SOURCE_OPTIONS
    assert sources[SourcePair(Source(76), Source(77))] == "Main 1-2 mix"
    assert sources[SourcePair(Source(78), Source(79))] == "Ana 1-2 mix"
    assert sources[SourcePair(Source(47), Source(34))] == "Mic 1-2"
    assert sources[SourcePair(Source(58), Source(59))] == "S/PDIF in L-R"
    assert sources[SourcePair(Source(90), Source(91))] == "Reverb L-R"
    assert sources[SourcePair(Source(127), Source(127))] == "nothing"
    assert not any(classify(pair.left).kind is SourceKind.POST_FX for pair in sources)
