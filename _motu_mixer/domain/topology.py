"""The fixed 828 catalogue: channel and bus names, patch-table numbering, routing choices and the stereo layout."""
from enum import Enum
from typing import NamedTuple

from .values import BusIndex, InputIndex, MixCell, OutputPair, Source, SourcePair

# mixer columns and buses (PROTOCOL-SPEC §4.1, §4.2)
INPUT_NAMES = (
    ("Mic 1", "Mic 2")
    + tuple(f"Line {n}" for n in range(3, 11))
    + ("S/PDIF L", "S/PDIF R")
    + tuple(f"ADAT A {n}" for n in range(1, 9))
    + tuple(f"ADAT B {n}" for n in range(1, 9))
    + tuple(f"Computer {n}" for n in range(1, 15))
    + ("Reverb return L", "Reverb return R")
)
BUS_NAMES = (
    ("Main 1", "Main 2")
    + tuple(f"Ana {n}" for n in range(1, 9))
    + tuple(f"Phones {n}" for n in range(1, 5))
    + ("Reverb 1", "Reverb 2")
)

# patch-table numbering (dev_828.js PatchBase; ADAT order as the capture's mixer-input slots show it)
LOCAL_INPUTS = (15, 2, 18, 5, 14, 1, 13, 0, 17, 4)  # Mic 1, Mic 2, Line 3..10
LOCAL_OUTPUT_LINES = (5, 7, 21, 24, 22, 19, 6, 9, 11, 8)  # Main 1, Main 2, Line out 3..10
LOCAL_OUTPUT_PHONES = (15, 25, 18, 20)  # Phones 1 L/R, Phones 2 L/R
LOCAL_BASE = 32
ADAT_A = (60, 61, 62, 63, 68, 69, 70, 71)
ADAT_B = (64, 65, 66, 67, 72, 73, 74, 75)
SPDIF_IN = (58, 59)
SPDIF_OUT_SLOTS = (58, 59)
REVERB_SOURCES = (90, 91)
NOTHING = 127
_MIXER_INPUT_BASE = 76
_BUS_MIX_BASE = 76
_POST_FX_BASE = 92


def mixer_input_slot(i: int) -> int:
    return _MIXER_INPUT_BASE + InputIndex(i)


def bus_mix_source(b: int) -> int:
    if not 0 <= b <= 13:
        raise ValueError(f"bus {b} has no mix source; reverb buses use REVERB_SOURCES")
    return _BUS_MIX_BASE + b


def post_fx_input(code: int) -> InputIndex:
    if not 92 <= code <= 101:
        raise ValueError(f"source {code} is not a post-FX source")
    return InputIndex(code - _POST_FX_BASE)


def local_input_source(i: int) -> int:
    return LOCAL_BASE + LOCAL_INPUTS[i]


class SourceKind(Enum):
    SPDIF_IN = "S/PDIF in"
    BUS_MIX = "bus mix"
    REVERB = "reverb"
    POST_FX = "post-FX"
    NOTHING = "nothing"
    HOST = "computer out"
    LOCAL = "local input"
    ADAT = "ADAT in"
    UNKNOWN = "unknown"


class SourceClass(NamedTuple):
    kind: SourceKind
    label: str
    index: int | None = None  # the bus of a BUS_MIX, the mixer input of a POST_FX


_LOCAL_BY_CODE = {local_input_source(i): INPUT_NAMES[i] for i in range(10)}
_ADAT_BY_CODE = {code: f"ADAT A {n}" for n, code in enumerate(ADAT_A, 1)}
_ADAT_BY_CODE.update({code: f"ADAT B {n}" for n, code in enumerate(ADAT_B, 1)})


def classify(source: Source) -> SourceClass:
    code = source.code
    if code in SPDIF_IN:
        return SourceClass(SourceKind.SPDIF_IN, f"S/PDIF in {'LR'[code - 58]}")
    if 76 <= code <= 89:
        bus = code - _BUS_MIX_BASE
        return SourceClass(SourceKind.BUS_MIX, f"{BUS_NAMES[bus]} mix", bus)
    if code in REVERB_SOURCES:
        return SourceClass(SourceKind.REVERB, f"Reverb {'LR'[code - 90]}")
    if 92 <= code <= 101:
        i = post_fx_input(code)
        return SourceClass(SourceKind.POST_FX, f"{INPUT_NAMES[i]} post-FX", i)
    if code == NOTHING:
        return SourceClass(SourceKind.NOTHING, "nothing")
    if 0 <= code <= 31:
        return SourceClass(SourceKind.HOST, f"Computer out {code + 1}")
    if code in _LOCAL_BY_CODE:
        return SourceClass(SourceKind.LOCAL, _LOCAL_BY_CODE[code])
    if code in _ADAT_BY_CODE:
        return SourceClass(SourceKind.ADAT, _ADAT_BY_CODE[code])
    return SourceClass(SourceKind.UNKNOWN, f"source {code} (unknown)")


def _pair_label(left: str, right: str) -> str:
    stem, _, first = left.rpartition(" ")
    return f"{stem} {first}-{right.rpartition(' ')[2]}"


def _pairs(names, codes):
    return [(_pair_label(names[k], names[k + 1]), codes[k], codes[k + 1]) for k in range(0, len(codes), 2)]


def _output_pairs():
    lines = [LOCAL_BASE + o for o in LOCAL_OUTPUT_LINES]
    phones = [LOCAL_BASE + o for o in LOCAL_OUTPUT_PHONES]
    entries = [("Main 1-2", lines[0], lines[1])]
    entries += [(f"Line out {n}-{n + 1}", lines[n - 1], lines[n]) for n in range(3, 11, 2)]
    entries += [("Phones 1", phones[0], phones[1]), ("Phones 2", phones[2], phones[3])]
    entries += [("S/PDIF out", *SPDIF_OUT_SLOTS)]
    entries += [(f"ADAT A out {n}-{n + 1}", ADAT_A[n - 1], ADAT_A[n]) for n in range(1, 9, 2)]
    entries += [(f"ADAT B out {n}-{n + 1}", ADAT_B[n - 1], ADAT_B[n]) for n in range(1, 9, 2)]
    entries += [(f"Computer in {n}-{n + 1}", n - 1, n) for n in range(1, 33, 2)]
    return {OutputPair(left, right): label for label, left, right in entries}


def _source_options():
    entries = [(f"{label} mix", left, right)
               for label, left, right in _pairs(BUS_NAMES[:14], [bus_mix_source(b) for b in range(14)])]
    entries += [("Reverb L-R", *REVERB_SOURCES)]
    entries += _pairs(INPUT_NAMES[:10], [local_input_source(i) for i in range(10)])
    entries += [("S/PDIF in L-R", *SPDIF_IN)]
    entries += _pairs(INPUT_NAMES[12:20], ADAT_A) + _pairs(INPUT_NAMES[20:28], ADAT_B)
    entries += [(f"Computer out {n}-{n + 1}", n - 1, n) for n in range(1, 33, 2)]
    entries += [("nothing", NOTHING, NOTHING)]
    return {SourcePair(Source(left), Source(right)): label for label, left, right in entries}


OUTPUT_PAIRS = _output_pairs()
SOURCE_OPTIONS = _source_options()


class Tab(NamedTuple):
    label: str
    first_bus: BusIndex
    is_stereo: bool


class Strip(NamedTuple):
    label: str
    first_input: InputIndex
    pan_visible: bool


class StereoLayout:
    """Bus tabs and input strips as the link arrays group them; links are read at even indexes only."""

    def __init__(self, input_links, bus_links):
        self._input_links = tuple(bool(v) for v in input_links)
        self._bus_links = tuple(bool(v) for v in bus_links)

    @classmethod
    def from_links(cls, input_links, bus_links) -> "StereoLayout":
        return cls(input_links, bus_links)

    @staticmethod
    def _first(links, i):
        even = i - i % 2
        return even if links[even] else i

    def _grouped(self, links, names):
        groups, i = [], 0
        while i < len(names):
            if i % 2 == 0 and links[i]:
                groups.append((_pair_label(names[i], names[i + 1]), i, True))
                i += 2
            else:
                groups.append((names[i], i, False))
                i += 1
        return groups

    def tabs(self) -> tuple[Tab, ...]:
        return tuple(Tab(label, BusIndex(b), stereo) for label, b, stereo in self._grouped(self._bus_links, BUS_NAMES))

    def strips(self) -> tuple[Strip, ...]:
        return tuple(Strip(label, InputIndex(i), not linked)
                     for label, i, linked in self._grouped(self._input_links, INPUT_NAMES))

    def first_bus(self, tab: int) -> BusIndex:
        return BusIndex(self._first(self._bus_links, BusIndex(tab)))

    def first_input(self, strip: int) -> InputIndex:
        return InputIndex(self._first(self._input_links, InputIndex(strip)))

    def primary_cell(self, strip: int, tab: int) -> MixCell:
        """The one cell CueMix writes for a strip on a tab: first bus of the tab, first input of the strip."""
        return MixCell(self.first_bus(tab), self.first_input(strip))

    def master_bus(self, tab: int) -> BusIndex:
        return self.first_bus(tab)

    def pan_visible(self, strip: int, tab: int) -> bool:
        return self._bus_links[tab - tab % 2] and not self._input_links[strip - strip % 2]
