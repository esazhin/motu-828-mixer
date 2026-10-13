"""The 828's live meters: property 6000, one frame of peak bytes (0 = 0 dBFS, each step -0.5 dB, 255 = nothing),
about 17 a second once property reporting is on. Indices share the routing table's source codes
(CueMix dev_828.js / mixmgr.js: a strip meters meters[kMIPostFx] + its bank channel; a bus meters its mix output)."""
from dataclasses import dataclass

from .topology import ADAT_A, ADAT_B, SPDIF_IN

METERS_PROP = 6000
_SILENT = 255

# Per mixer input 0-43: Mic 1-2 + Line 3-10 post-FX taps (pre-fader, after the input EQ/dynamics), S/PDIF in,
# ADAT A, ADAT B, Computer 1-14 (host channels), reverb return.
INPUT_METER_INDEX = (
    tuple(range(92, 102)) + SPDIF_IN + ADAT_A + ADAT_B + tuple(range(0, 14)) + (56, 57)
)
# Per bus 0-15: each mix bus's output (76 + bus); the two reverb sends meter the reverb return.
BUS_METER_INDEX = tuple(range(76, 90)) + (56, 57)


@dataclass(frozen=True)
class MeterLevels:
    inputs: tuple   # 44 x dBFS (float) or None for silence
    buses: tuple    # 16 x dBFS (float) or None


def _level(raw: bytes, index: int):
    if index >= len(raw) or raw[index] >= _SILENT:
        return None
    return -raw[index] / 2


def meter_levels(raw: bytes) -> MeterLevels:
    return MeterLevels(
        inputs=tuple(_level(raw, i) for i in INPUT_METER_INDEX),
        buses=tuple(_level(raw, i) for i in BUS_METER_INDEX),
    )
