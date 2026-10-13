"""The 828's parametric EQ (CueMix dev_828.js / equi.js): input EQ on Mic 1-2 + Line 3-10 (4 bands each), output EQ
on each mix bus (3 bands). Per band: mode, bypass, frequency (Hz, int32), gain (dB, 8.24, signed) and Q (8.24; the
property is named 'BW' but CueMix edits it as Q, 0.45-10). Index = channel x bands + band."""
from dataclasses import dataclass

from .status import Refusal, RefusalKind

PEAK, LOW_SHELF, HIGH_SHELF, HIGH_PASS = 0, 1, 2, 3
FREQ_RANGE = (20, 20000)
GAIN_RANGE = (-20.0, 20.0)
Q_RANGE = (0.45, 10.0)
_FIELDS = ("mode", "bypass", "freq", "gain", "q")


@dataclass(frozen=True)
class EqLayout:
    mode: int       # first property id; bypass, freq, gain, q follow
    channels: int
    bands: int

    def prop(self, field: str) -> int:
        return self.mode + _FIELDS.index(field)

    @property
    def props(self):
        return tuple(self.mode + i for i in range(len(_FIELDS)))

    @property
    def length(self) -> int:
        return self.channels * self.bands


INPUT_EQ = EqLayout(mode=1002, channels=10, bands=4)
OUTPUT_EQ = EqLayout(mode=1022, channels=14, bands=3)
_LAYOUTS = (INPUT_EQ, OUTPUT_EQ)


def is_eq_prop(prop: int) -> bool:
    return any(prop in layout.props for layout in _LAYOUTS)


def band_of(prop: int, index: int):
    layout = next(layout for layout in _LAYOUTS if prop in layout.props)
    return layout, index // layout.bands, index % layout.bands


def modes_for_band(layout: EqLayout, band: int) -> tuple:
    if band == 0:
        return (PEAK, LOW_SHELF, HIGH_PASS)
    if band == layout.bands - 1:
        return (PEAK, HIGH_SHELF)
    return (PEAK,)


def eq_slot_for_bus(bus: int, linked: bool) -> int:
    """CueMix busEq + bank channel: Main 0, Ana 1-8 -> 2-9, Phones 10-13, Reverb 13; a linked pair uses its first."""
    if bus >= 14:
        return 13
    return bus & ~1 if linked else bus


def check_eq_value(prop: int, index: int, value):
    """The value to write, typed for the wire, or a Refusal when it is outside what CueMix allows (never clamped)."""
    layout, _channel, band = band_of(prop, index)
    field = _FIELDS[prop - layout.mode]

    def refuse(why):
        return Refusal(RefusalKind.VALUE_RANGE, f"EQ {field} {value!r} {why}")

    if field == "bypass":
        if value in (0, 1, True, False):
            return int(value)
        return refuse("must be 0 or 1")
    if field == "mode":
        allowed = modes_for_band(layout, band)
        return int(value) if value in allowed and not isinstance(value, bool) else refuse(f"not allowed on band {band + 1} (allowed {allowed})")
    low, high = {"freq": FREQ_RANGE, "gain": GAIN_RANGE, "q": Q_RANGE}[field]
    if isinstance(value, bool) or not low <= value <= high:
        return refuse(f"outside {low}..{high}")
    return int(round(value)) if field == "freq" else float(value)
