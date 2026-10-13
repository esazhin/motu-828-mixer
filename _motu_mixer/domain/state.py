"""MixerState: the unit's raw property values by address, with typed reads."""
from . import properties
from .properties import PropertyId as P, ValueType
from .values import (
    Address, BusIndex, Delta, DeviceIdentity, Gain, MixCell, OpticalConfig, OpticalMode, OutputSlot, Pan,
    Report, Source,
)

_ONE_824 = 1 << 24
_IDENTITY = (P.MODEL_ID, P.API_VERSION, P.DEV_NAME, P.DEV_UID)


def _decode(value_type, raw):
    if value_type is ValueType.STRING:
        return raw.split(b"\0", 1)[0].decode(errors="replace")
    number = int.from_bytes(raw, "big", signed=value_type is not ValueType.BYTE)
    return number / _ONE_824 if value_type is ValueType.F824 else number


def _encode(value_type, value):
    if value_type is ValueType.F824:
        if isinstance(value, Gain):
            number = value.to_824()
        elif isinstance(value, Pan):
            number = round(value.value * _ONE_824)
        else:  # a plain signed number: EQ gain (dB) and Q
            number = round(float(value) * _ONE_824)
        return number.to_bytes(4, "big", signed=True)
    if isinstance(value, Source):
        value = value.code
    width = {ValueType.BYTE: 1, ValueType.I16: 2, ValueType.I32: 4}[value_type]
    return int(value).to_bytes(width, "big", signed=value_type is not ValueType.BYTE)


class MixerState:
    def __init__(self, spec=properties.spec, gate_arrays=properties.GATE_ARRAYS, raw=None):
        self._spec = spec
        self._gate_arrays = gate_arrays
        self._raw = dict(raw or {})

    def apply(self, report: Report) -> Delta | None:
        old = self._raw.get(report.address)
        if old == report.raw:
            return None
        self._raw[report.address] = report.raw
        return Delta(report.address, old, report.raw)

    def with_writes(self, writes) -> "MixerState":
        raw = dict(self._raw)
        for write in writes:
            raw[write.address] = _encode(self._value_type(write.address.prop), write.value)
        return MixerState(self._spec, self._gate_arrays, raw)

    def copy(self) -> "MixerState":
        return MixerState(self._spec, self._gate_arrays, self._raw)

    def raw(self, address: Address) -> bytes | None:
        return self._raw.get(address)

    def _value_type(self, prop):
        entry = self._spec(prop)
        if entry is None:
            raise ValueError(f"property {prop} has no known type")
        return entry.value_type

    def _read(self, prop, index):
        raw = self._raw.get(Address(prop, index))
        return None if raw is None else _decode(self._value_type(prop), raw)

    def _mapped(self, prop, index, convert):
        value = self._read(prop, index)
        return None if value is None else convert(value)

    def gain(self, cell: MixCell) -> Gain | None:
        return self._mapped(P.MIX_FADER, cell.index, lambda v: Gain(max(v, 0.0)))

    def pan(self, cell: MixCell) -> Pan | None:
        return self._mapped(P.MIX_PAN, cell.index, Pan)

    def muted(self, cell: MixCell) -> bool | None:
        return self._mapped(P.MIX_MUTE, cell.index, bool)

    def soloed(self, cell: MixCell) -> bool | None:
        return self._mapped(P.MIX_SOLO, cell.index, bool)

    def master_gain(self, bus: BusIndex) -> Gain | None:
        return self._mapped(P.BUS_FADER, bus, lambda v: Gain(max(v, 0.0)))

    def master_muted(self, bus: BusIndex) -> bool | None:
        return self._mapped(P.BUS_MUTE, bus, bool)

    def source(self, slot: OutputSlot) -> Source | None:
        return self._mapped(P.PATCH, slot, Source)

    def links(self) -> tuple[tuple[bool, ...], tuple[bool, ...]]:
        """(input links ×44, bus links ×16); a link not yet received reads False."""
        inputs = tuple(bool(self._read(P.INPUT_LINK, i)) for i in range(44))
        buses = tuple(bool(self._read(P.BUS_LINK, b)) for b in range(16))
        return inputs, buses

    def identity(self) -> DeviceIdentity | None:
        if any(Address(prop, 0) not in self._raw for prop in _IDENTITY):
            return None
        return DeviceIdentity(
            model=self._read(P.MODEL_ID, 0), serial=self._read(P.DEV_UID, 0),
            firmware=self._read(P.FIRMWARE, 0) or "", api=self._read(P.API_VERSION, 0),
        )

    def optical_config(self) -> OpticalConfig | None:
        modes = [self._read(P.OPTICAL_MODE, side) for side in (0, 1)]
        expander = self._read(P.OPTICAL_EXPANDER, 0)
        if None in modes or expander is None:
            return None
        input_mode, output_mode = (OpticalMode.ADAT if m == 0 else OpticalMode.TOSLINK for m in modes)
        return OpticalConfig(input_mode, output_mode, expander)

    def arrays_complete(self) -> bool:
        return all(
            all(Address(prop, i) in self._raw for i in range(self._spec(prop).length))
            for prop in self._gate_arrays
        )
