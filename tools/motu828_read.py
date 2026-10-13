#!/usr/bin/env python3
"""Read-only MOTU 828 (2023/2024, USB 07fd:000e) state reader over the "828 COM" MIDI port.

Protocol recovered from MOTU's unofficial CueMix 5 Linux build (midi.js / dev_828.js), 2026-10-12.

  live:    python3 motu828_read.py --live [--seconds 3]    # needs `amidi` (alsa-utils), port hw:4,0,1
  offline: python3 motu828_read.py --dump fixtures/828-property-dump-2026-10-12.hex

Writes NOTHING to the device except the probe (0x01) and enable-property-reporting (0x02) requests.
"""
import argparse, re, subprocess, sys

HDR = bytes([0xF0, 0x00, 0x00, 0x3B, 0x00, 0x01])   # MOTU mfr id 00 00 3B + sysex protocol 00 01
REQ_PROBE, REQ_ENABLE, REQ_SETPROP = 0x01, 0x02, 0x00
DEV_TO_HOST = 0x40

def unpack7(msg):
    """Inverse of MOTU's 7-bit packing: after the header + request byte, each group = 1 MSB byte + 7 data bytes."""
    out, n = bytearray(), 7
    b7, n, shl = msg[n], n + 1, 7
    while msg[n] != 0xF7:
        if shl == 0:
            b7, n, shl = msg[n], n + 1, 7
        else:
            out.append(((b7 << shl) & 0x80) | msg[n]); shl -= 1; n += 1
    return bytes(out)

def parse(raw):
    """raw sysex byte stream -> list of (prop_id, index, payload) for device->host set-property messages."""
    props = []
    for part in raw.split(b'\xf7'):
        if part.startswith(HDR) and len(part) > 8 and part[6] == (DEV_TO_HOST | REQ_SETPROP):
            d = unpack7(part + b'\xf7')
            props.append(((d[0] << 8) | d[1], (d[2] << 8) | d[3], d[4:]))
    return props

# ---- routing-table naming (dev_828.js: PatchBase / LocalInputs / LocalOutputs) ----
LI = [15, 2, 18, 5, 14, 1, 13, 0, 17, 4]
LN = ["Mic 1", "Mic 2"] + [f"Line {i}" for i in range(3, 11)]
LO = [5, 7, 21, 24, 22, 19, 6, 9, 11, 8]
LON = ["Main 1", "Main 2"] + [f"Line out {i}" for i in range(3, 11)]
SRC = {127: "(nothing)", 58: "S/PDIF in L", 59: "S/PDIF in R"}
SRC.update({i: f"Computer out {i+1}" for i in range(32)})
SRC.update({32 + v: f"{LN[i]} in" for i, v in enumerate(LI)})
SRC.update({76 + i: f"Mixer: line-out mix {i+1}" for i in range(10)})     # dsp base 76 (get_line_mix)
SRC.update({86 + i: f"Mixer: phones mix {i}" for i in range(4)})          # dsp+10 (get_phone_mix)
SRC.update({90: "Reverb L", 91: "Reverb R"})                             # dsp+14 (get_reverb_mix)
DST = {58: "S/PDIF out L", 59: "S/PDIF out R",
       47: "Phones 1 L", 57: "Phones 1 R", 50: "Phones 2 L", 52: "Phones 2 R"}
DST.update({32 + v: LON[i] for i, v in enumerate(LO)})

def report(props):
    latest = {}
    for pid, idx, payload in props:
        latest[(pid, idx)] = payload
    s = lambda pid: latest.get((pid, 0), b'').split(b'\0')[0].decode(errors='replace')
    print(f"{len(props)} msgs, {len({p for p, _ in latest})} distinct props")
    print(f"model={s(21)} serial={s(2)} firmware={s(4)} api={int.from_bytes(latest.get((8, 0), b'\0\0')[:2], 'big')}")
    patch = {idx: pl[0] for (pid, idx), pl in latest.items() if pid == 5010 and pl}
    print("routing (prop 5010, patch[output] = source):")
    for d in sorted(DST, key=lambda k: list(DST).index(k)):
        if d in patch:
            print(f"  {DST[d]:13} <- {SRC.get(patch[d], patch[d])}")

def capture_live(seconds, port):
    # amidi's -t is an IDLE timeout and the 828 streams continuously, so bound it with `timeout` instead.
    cmd = ["timeout", str(seconds), "amidi", "-p", port, "-d",
           "-S", (HDR + bytes([REQ_PROBE, 0xF7]) + HDR + bytes([REQ_ENABLE, 0xF7])).hex(' ')]
    out = subprocess.run(cmd, capture_output=True, text=True).stdout
    return out

def hex_to_bytes(text):
    return bytes(int(x, 16) for x in text.split() if re.fullmatch(r'[0-9A-Fa-f]{2}', x))

if __name__ == "__main__":
    ap = argparse.ArgumentParser()
    g = ap.add_mutually_exclusive_group(required=True)
    g.add_argument("--live", action="store_true"); g.add_argument("--dump")
    ap.add_argument("--seconds", type=int, default=3); ap.add_argument("--port", default="hw:4,0,1")
    a = ap.parse_args()
    text = capture_live(a.seconds, a.port) if a.live else open(a.dump).read()
    report(parse(hex_to_bytes(text)))
