import pytest

from _motu_mixer.domain.status import LoopPath, PathNode, Refusal, RefusalKind
from _motu_mixer.domain.values import BusIndex, OutputSlot, Source


def _spdif_through_ana_1_2():
    return LoopPath((
        PathNode(Source(58), "S/PDIF in"),
        PathNode(BusIndex(2), "Ana 1-2"),
        PathNode(OutputSlot(58), "S/PDIF out"),
    ))


def test_loop_path_label_joins_hops_with_arrows():
    assert _spdif_through_ana_1_2().label() == "S/PDIF in → Ana 1-2 → S/PDIF out"
    assert _spdif_through_ana_1_2() == _spdif_through_ana_1_2()


def test_loop_path_runs_from_spdif_in_to_spdif_out():
    with pytest.raises(ValueError):
        LoopPath((PathNode(Source(47), "Mic 1"), PathNode(OutputSlot(58), "S/PDIF out")))
    with pytest.raises(ValueError):
        LoopPath((PathNode(Source(58), "S/PDIF in"), PathNode(OutputSlot(53), "Line out 3")))


def test_only_a_loop_refusal_carries_a_path():
    assert Refusal(RefusalKind.LOOP, "loop", _spdif_through_ana_1_2()).path is not None
    with pytest.raises(ValueError):
        Refusal(RefusalKind.READ_ONLY, "API version 10, expected 9", _spdif_through_ana_1_2())
