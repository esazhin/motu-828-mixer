"""Immutable value objects of the 828 mixer domain."""
import math
from dataclasses import dataclass
from enum import Enum

_ONE_824 = 1 << 24
MAX_GAIN = 4.0


@dataclass(frozen=True)
class Gain:
    linear: float

    def __post_init__(self):
        if not self.linear >= 0:
            raise ValueError(f"gain must be >= 0, got {self.linear}")

    @classmethod
    def from_db(cls, db: float | str) -> "Gain":
        db = float(db)
        return cls(0.0 if db == -math.inf else 10 ** (db / 20))

    @classmethod
    def from_824(cls, raw: int) -> "Gain":
        return cls(max(raw, 0) / _ONE_824)

    @property
    def db(self) -> float:
        return -math.inf if self.is_silent else 20 * math.log10(self.linear)

    @property
    def is_silent(self) -> bool:
        return self.linear == 0

    def clamped(self) -> "Gain":
        return Gain(min(self.linear, MAX_GAIN))

    def to_824(self) -> int:
        return round(self.linear * _ONE_824)


@dataclass(frozen=True)
class Pan:
    value: float
    CENTRE = 0.5

    def __post_init__(self):
        object.__setattr__(self, "value", min(max(float(self.value), 0.0), 1.0))


class _BoundedIndex(int):
    LIMIT = 0
    WHAT = ""

    def __new__(cls, value: int):
        if not 0 <= int(value) < cls.LIMIT:
            raise ValueError(f"{cls.WHAT} {value} out of range 0-{cls.LIMIT - 1}")
        return super().__new__(cls, value)


class BusIndex(_BoundedIndex):
    LIMIT, WHAT = 16, "bus"


class InputIndex(_BoundedIndex):
    LIMIT, WHAT = 44, "input"


class OutputSlot(_BoundedIndex):
    LIMIT, WHAT = 128, "output slot"

    @property
    def writable(self) -> bool:
        # slots 76-119 feed the mixer inputs and stay as CueMix set them
        return not 76 <= self <= 119


@dataclass(frozen=True)
class MixCell:
    bus: BusIndex
    input: InputIndex

    def __post_init__(self):
        object.__setattr__(self, "bus", BusIndex(self.bus))
        object.__setattr__(self, "input", InputIndex(self.input))

    @property
    def index(self) -> int:
        return self.bus * 44 + self.input


@dataclass(frozen=True)
class OutputPair:
    left: OutputSlot
    right: OutputSlot

    def __post_init__(self):
        for side in ("left", "right"):
            slot = OutputSlot(getattr(self, side))
            if not slot.writable:
                raise ValueError(f"output slot {slot} is not writable")
            object.__setattr__(self, side, slot)


@dataclass(frozen=True)
class Source:
    code: int
    NOTHING = 127

    def __post_init__(self):
        if not 0 <= self.code <= 127:
            raise ValueError(f"source {self.code} out of range 0-127")


@dataclass(frozen=True)
class SourcePair:
    left: Source
    right: Source


@dataclass(frozen=True)
class Address:
    prop: int
    index: int


@dataclass(frozen=True)
class Write:
    address: Address
    value: Gain | Pan | bool | Source


@dataclass(frozen=True)
class Report:
    address: Address
    raw: bytes


@dataclass(frozen=True)
class Delta:
    address: Address
    old: bytes | None
    new: bytes

    def __post_init__(self):
        if self.old == self.new:
            raise ValueError("a delta needs a changed value")


@dataclass(frozen=True)
class DeviceIdentity:
    model: str
    serial: str
    firmware: str
    api: int

    def __post_init__(self):
        for name in ("model", "serial", "firmware"):
            object.__setattr__(self, name, getattr(self, name).split("\0", 1)[0])


class OpticalMode(Enum):
    ADAT = 0
    TOSLINK = 1


@dataclass(frozen=True)
class OpticalConfig:
    input_mode: OpticalMode
    output_mode: OpticalMode
    expander: int

    @property
    def verified(self) -> bool:
        return (self.input_mode is OpticalMode.ADAT and self.output_mode is OpticalMode.ADAT
                and self.expander == 0)


@dataclass(frozen=True)
class SentFrame:
    t: float
    frame: bytes


@dataclass(frozen=True)
class PortAddress:
    device: str
