"""The 828's analog input block (CueMix dev_828.js / iosetup.js): preamp gain, polarity, pad, 48V and jack detection.
One byte per channel. Gain is whole dB: Mic 1-2 0-74 (kPreTrimCtrlInfo), Line 3-10 and the two insert returns 0-20
(kLineInTrimCtrlInfo). Polarity, pad and 48V are bits; the page asks before switching 48V on. CueMix greys pad and 48V out
on a line/instrument jack."""
from .status import Refusal, RefusalKind

GAIN, PHASE, PAD, PHANTOM, JACK_DETECT, RETURN_DETECT = 5001, 5002, 5003, 5004, 5005, 5021
MIC_GAIN_RANGE = (0, 74)
LINE_GAIN_RANGE = (0, 20)
MICS = 2
GAIN_CHANNELS = ("Mic 1", "Mic 2") + tuple(f"Line {n}" for n in range(3, 11)) + ("Return 1", "Return 2")
RETURNS_START = 10          # kiGain index of the first insert return (dev_828.js returns_igain_start_idx)
_WRITABLE = (GAIN, PHASE, PAD, PHANTOM)
_BIT_NAMES = {PHASE: "polarity", PAD: "pad", PHANTOM: "48V"}


def is_preamp_prop(prop: int) -> bool:
    return prop in _WRITABLE


def gain_range(index: int) -> tuple[int, int]:
    return MIC_GAIN_RANGE if index < MICS else LINE_GAIN_RANGE


def check_preamp_value(prop: int, index: int, value):
    """The byte to write, or a Refusal when the value is not one CueMix would send (never clamped)."""
    if prop in _BIT_NAMES:
        if value in (0, 1, True, False):
            return int(value)
        return Refusal(RefusalKind.VALUE_RANGE, f"{_BIT_NAMES[prop]} {value!r} must be 0 or 1")
    low, high = gain_range(index)
    whole = isinstance(value, int) and not isinstance(value, bool) or isinstance(value, float) and value.is_integer()
    if not whole or not low <= value <= high:
        return Refusal(RefusalKind.VALUE_RANGE, f"{GAIN_CHANNELS[index]} gain {value!r} must be a whole dB in {low}..{high}")
    return int(value)
