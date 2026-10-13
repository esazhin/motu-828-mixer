"""The 828 SysEx control protocol (PROTOCOL-SPEC §2-3): framing, 7-bit packing, encode and decode, kept in pairs."""
from dataclasses import dataclass
from enum import Enum

from ..domain import properties
from ..domain.properties import ValueType
from ..domain.values import Address, Gain, Pan, Report, Source

HEADER = bytes([0xF0, 0x00, 0x00, 0x3B, 0x00, 0x01])
END = 0xF7
SET_PROPERTY, PROBE_REQUEST, ENABLE_REQUEST = 0x00, 0x01, 0x02
DEVICE_TO_HOST = 0x40
_ONE_824 = 1 << 24
_STRING_OUT_LENGTH = 32
_WIDTHS = {ValueType.F824: 4, ValueType.BYTE: 1, ValueType.I16: 2, ValueType.I32: 4}


def pack7(body: bytes) -> bytes:
    out = bytearray()
    for start in range(0, len(body), 7):
        group = body[start:start + 7]
        out.append(sum(((b >> 7) & 1) << k for k, b in enumerate(group)))
        out += bytes(b & 0x7F for b in group)
    return bytes(out)


def unpack7(packed: bytes) -> bytes:
    out = bytearray()
    for start in range(0, len(packed), 8):
        msb, group = packed[start], packed[start + 1:start + 8]
        out += bytes(b | (((msb >> k) & 1) << 7) for k, b in enumerate(group))
    return bytes(out)


class SysexSplitter:
    """Reassembles F0…F7 frames from chunks of any size; drops a frame cut short and bytes outside a frame."""

    def __init__(self):
        self._frame = None

    def feed(self, chunk: bytes) -> list[bytes]:
        frames = []
        for byte in chunk:
            if byte == 0xF0:
                self._frame = bytearray([byte])
            elif self._frame is not None:
                self._frame.append(byte)
                if byte == END:
                    frames.append(bytes(self._frame))
                    self._frame = None
        return frames


@dataclass(frozen=True)
class ProbeReply:
    pass


@dataclass(frozen=True)
class Ignored:
    pass


def _request(frame: bytes) -> int | None:
    if len(frame) < 8 or not frame.startswith(HEADER) or frame[-1] != END:
        return None
    return frame[6]


def _id_and_index(body: bytes) -> Address:
    return Address(int.from_bytes(body[0:2], "big"), int.from_bytes(body[2:4], "big"))


def decode(frame: bytes) -> Report | ProbeReply | Ignored:
    request = _request(frame)
    if request == DEVICE_TO_HOST | PROBE_REQUEST:
        return ProbeReply()
    if request == DEVICE_TO_HOST | SET_PROPERTY:
        body = unpack7(frame[7:-1])
        if len(body) >= 4:
            return Report(_id_and_index(body), body[4:])
    return Ignored()


def encode_report(address: Address, raw: bytes) -> bytes:
    body = address.prop.to_bytes(2, "big") + address.index.to_bytes(2, "big") + raw
    return HEADER + bytes([DEVICE_TO_HOST | SET_PROPERTY]) + pack7(body) + bytes([END])


def _value_type(prop_id, spec):
    entry = spec(prop_id)
    if entry is None:
        raise ValueError(f"property {prop_id} has no known type")
    return entry.value_type


def encode_value(value_type: ValueType, value) -> bytes:
    if value_type is ValueType.STRING:
        return str(value).encode()[:_STRING_OUT_LENGTH - 1].ljust(_STRING_OUT_LENGTH, b"\0")
    if isinstance(value, Gain):
        number = value.to_824()
    elif isinstance(value, Pan):
        number = round(value.value * _ONE_824)
    elif isinstance(value, Source):
        number = value.code
    elif value_type is ValueType.F824:
        number = round(float(value) * _ONE_824)
    else:
        number = int(value)
    return number.to_bytes(_WIDTHS[value_type], "big", signed=value_type is not ValueType.BYTE)


def decode_value(prop_id: int, raw: bytes, spec=properties.spec):
    value_type = _value_type(prop_id, spec)
    if value_type is ValueType.STRING:
        return raw.split(b"\0", 1)[0].decode(errors="replace")
    number = int.from_bytes(raw, "big", signed=value_type is not ValueType.BYTE)
    return number / _ONE_824 if value_type is ValueType.F824 else number


def encode_set(address: Address, value, spec=properties.spec) -> bytes:
    raw = encode_value(_value_type(address.prop, spec), value)
    body = (address.prop.to_bytes(2, "big") + address.index.to_bytes(2, "big")
            + len(raw).to_bytes(2, "big") + raw)
    return HEADER + bytes([SET_PROPERTY]) + pack7(body) + bytes([END])


def probe_frame() -> bytes:
    return HEADER + bytes([PROBE_REQUEST, END])


def enable_frame() -> bytes:
    return HEADER + bytes([ENABLE_REQUEST, END])


class HostRequest(Enum):
    PROBE = "probe"
    ENABLE = "enable"
    OTHER = "other"


PROBE, ENABLE, OTHER = HostRequest.PROBE, HostRequest.ENABLE, HostRequest.OTHER


@dataclass(frozen=True)
class SetRequest:
    address: Address
    raw: bytes


def classify_host_frame(frame: bytes) -> HostRequest | SetRequest:
    request = _request(frame)
    if request == PROBE_REQUEST and len(frame) == 8:
        return PROBE
    if request == ENABLE_REQUEST and len(frame) == 8:
        return ENABLE
    if request == SET_PROPERTY:
        body = unpack7(frame[7:-1])
        if len(body) >= 6:
            length = int.from_bytes(body[4:6], "big")
            if len(body) == 6 + length:
                return SetRequest(_id_and_index(body), body[6:])
    return OTHER
