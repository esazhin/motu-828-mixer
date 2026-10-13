"""The 828 properties this service knows: wire type, array length (from the 2026-10-12 capture), writability."""
from dataclasses import dataclass
from enum import Enum, IntEnum


class PropertyId(IntEnum):
    DEV_UID = 2  # the serial number
    DEV_NAME = 3
    FIRMWARE = 4
    API_VERSION = 8
    MODEL_ID = 21
    INPUT_LINK = 1000
    BUS_LINK = 1001
    IN_EQ_MODE = 1002
    IN_EQ_BYPASS = 1003
    IN_EQ_FREQ = 1004
    IN_EQ_GAIN = 1005
    IN_EQ_Q = 1006  # 'kiEQBW' in CueMix, edited there as Q 0.45-10
    MIX_FADER = 1016
    MIX_PAN = 1017
    MIX_SOLO = 1018
    MIX_MUTE = 1019
    BUS_FADER = 1027
    OUT_EQ_MODE = 1022
    OUT_EQ_BYPASS = 1023
    OUT_EQ_FREQ = 1024
    OUT_EQ_GAIN = 1025
    OUT_EQ_Q = 1026  # 'koEQBW'
    BUS_MUTE = 1028
    OUTPUT_TRIM = 5000
    PREAMP_GAIN = 5001
    PAD = 5003
    PHANTOM_POWER = 5004
    OPTICAL_MODE = 5006
    PATCH = 5010
    EXPANDER_PATCH = 5020
    OPTICAL_EXPANDER = 5025


class ValueType(Enum):
    F824 = "8.24 fixed point, 4 bytes signed"
    BYTE = "1 byte"
    I16 = "2 bytes"
    I32 = "4 bytes signed"
    STRING = "NUL-terminated in, 32 bytes NUL-padded out"


@dataclass(frozen=True)
class PropertySpec:
    value_type: ValueType
    length: int
    writable: bool


P = PropertyId
PROPERTY_TABLE = {
    P.DEV_UID: PropertySpec(ValueType.STRING, 1, False),
    P.DEV_NAME: PropertySpec(ValueType.STRING, 1, False),
    P.FIRMWARE: PropertySpec(ValueType.STRING, 1, False),
    P.API_VERSION: PropertySpec(ValueType.I16, 1, False),
    P.MODEL_ID: PropertySpec(ValueType.STRING, 1, False),
    P.INPUT_LINK: PropertySpec(ValueType.BYTE, 44, True),
    P.BUS_LINK: PropertySpec(ValueType.BYTE, 16, True),
    P.MIX_FADER: PropertySpec(ValueType.F824, 704, True),
    P.MIX_PAN: PropertySpec(ValueType.F824, 704, True),
    P.MIX_SOLO: PropertySpec(ValueType.BYTE, 704, True),
    P.MIX_MUTE: PropertySpec(ValueType.BYTE, 704, True),
    P.BUS_FADER: PropertySpec(ValueType.F824, 16, True),
    P.BUS_MUTE: PropertySpec(ValueType.BYTE, 16, True),
    # EQ: input 10 ch x 4 bands, output 14 bus slots x 3 bands (domain/eq.py validates every value)
    P.IN_EQ_MODE: PropertySpec(ValueType.BYTE, 40, True),
    P.IN_EQ_BYPASS: PropertySpec(ValueType.BYTE, 40, True),
    P.IN_EQ_FREQ: PropertySpec(ValueType.I32, 40, True),
    P.IN_EQ_GAIN: PropertySpec(ValueType.F824, 40, True),
    P.IN_EQ_Q: PropertySpec(ValueType.F824, 40, True),
    P.OUT_EQ_MODE: PropertySpec(ValueType.BYTE, 42, True),
    P.OUT_EQ_BYPASS: PropertySpec(ValueType.BYTE, 42, True),
    P.OUT_EQ_FREQ: PropertySpec(ValueType.I32, 42, True),
    P.OUT_EQ_GAIN: PropertySpec(ValueType.F824, 42, True),
    P.OUT_EQ_Q: PropertySpec(ValueType.F824, 42, True),
    P.OUTPUT_TRIM: PropertySpec(ValueType.BYTE, 16, False),
    P.PREAMP_GAIN: PropertySpec(ValueType.BYTE, 12, False),
    P.PAD: PropertySpec(ValueType.BYTE, 2, False),
    P.PHANTOM_POWER: PropertySpec(ValueType.BYTE, 2, False),
    P.OPTICAL_MODE: PropertySpec(ValueType.BYTE, 2, False),
    P.PATCH: PropertySpec(ValueType.BYTE, 128, True),
    P.EXPANDER_PATCH: PropertySpec(ValueType.BYTE, 128, False),
    P.OPTICAL_EXPANDER: PropertySpec(ValueType.BYTE, 1, False),
}
del P

# every index of these must have arrived before any write is allowed
GATE_ARRAYS = (
    PropertyId.INPUT_LINK, PropertyId.BUS_LINK, PropertyId.MIX_FADER, PropertyId.MIX_MUTE,
    PropertyId.OPTICAL_MODE, PropertyId.PATCH, PropertyId.EXPANDER_PATCH, PropertyId.OPTICAL_EXPANDER,
)


def spec(prop_id: int) -> PropertySpec | None:
    return PROPERTY_TABLE.get(prop_id)
