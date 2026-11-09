"""The protocol questions still open on the real 828, each with the one behaviour the service carries."""
from dataclasses import dataclass


@dataclass(frozen=True)
class OpenItem:
    name: str
    question: str
    behaviour: str
    settled_by: str


OPEN_ITEMS = (
    OpenItem(
        name="echo",
        question="Does the live stream echo the host's own writes?",
        behaviour="An accepted change is applied to the local state and published at once; "
                  "any later report from the unit overwrites it, echoed or not.",
        settled_by="Live check: write kiMixFader 1016/643, listen 1 s on the same open port "
                   "for a 1016/643 report, then restore.",
    ),
    OpenItem(
        name="W2",
        question="Is the one-byte mute/solo write path (1018/1019) applied by the unit?",
        behaviour="Mute and solo writes are sent as one-byte values for the single cell of the action.",
        settled_by="Live W2: kiMixMute 1019/643 set to 1, read back, then restored to 0.",
    ),
    OpenItem(
        name="W3",
        question="Is per-bus addressing of the bus master (1027/1028) applied by the unit?",
        behaviour="Bus master level and mute are written at index = bus, the first bus of a linked pair.",
        settled_by="Live W3: koBusFader and koBusMute on bus 15 (Reverb 2) written, read back, restored.",
    ),
    OpenItem(
        name="W5",
        question="Is a routing write to kFPGAPatch (5010) applied by the unit?",
        behaviour="Routing writes set 5010[slot] = source code, one byte per output slot, "
                  "never for slots 76-119.",
        settled_by="Live W5: kFPGAPatch slot 40 (Line out 10) set to 127, read back, restored.",
    ),
    OpenItem(
        name="W6",
        question="Are one-byte writes to the analog input block (kiGain 5001, kiPhase 5002, kPad 5003, k48V 5004) applied by the unit?",
        behaviour="Gain is written as whole dB within CueMix's range for that channel (Mic 0-74, Line and returns 0-20), "
                  "polarity, pad and 48V as 0/1; the page asks before switching 48V on.",
        settled_by="Live W6: kiGain index 9 (Line 10) raised by 1 dB, read back, then restored.",
    ),
)
