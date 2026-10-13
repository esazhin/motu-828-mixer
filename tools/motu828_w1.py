#!/usr/bin/env python3
"""W1 write-verification (PROTOCOL-SPEC.md §9): set ONE mixer fader cell, read it back, restore it.

Default target: bus 14 (Reverb 1) x input 27 (ADAT B 8) = cell 643, an unused cell.
Mechanism (PASSED 2026-10-12): read the cell in a FRESH session (probe + enable -> full dump), send the SET,
read again in a fresh session, restore, read again. (Do NOT use a long-running `timeout … amidi -d` listener
for read-back: amidi is killed with its stdout still buffered and the later dumps are lost, which looks
like "the write didn't take".)

  python3 motu828_w1.py [--cell 643] [--db -6] [--port hw:4,0,1]
"""
import argparse, math, subprocess, sys, time
sys.path.insert(0, __import__('os').path.dirname(__file__))
import motu828_read as r

KI_MIX_FADER = 1016

def pack7(body):
    out = []
    for s in range(0, len(body), 7):
        g = body[s:s + 7]
        msb = 0
        for k, b in enumerate(g):
            msb |= ((b >> 7) & 1) << k
        out += [msb] + [b & 0x7F for b in g]
    return bytes(out)

def to824(v):
    return round(v * 0x01000000) & 0xFFFFFFFF

def set_float_msg(prop, index, value):
    # host->device body = [id:u16][index:u16][len:u16][value]  (PROTOCOL-SPEC §2.1)
    body = prop.to_bytes(2, 'big') + index.to_bytes(2, 'big') + (4).to_bytes(2, 'big') + to824(value).to_bytes(4, 'big')
    return r.HDR + bytes([0x00]) + pack7(body) + b'\xf7'

def send(port, msg):
    subprocess.run(["amidi", "-p", port, "-S", msg.hex(' ')], check=True, capture_output=True)

def f824(b):
    return int.from_bytes(b[:4], 'big', signed=True) / 0x01000000

def read_cell(port, prop, cell):
    v = None
    for pid, idx, pl in r.parse(r.hex_to_bytes(r.capture_live(3, port))):
        if pid == prop and idx == cell:
            v = pl
    return v

def fmt(v):
    if v is None:
        return "None"
    g = f824(v)
    return f"raw {v.hex()} = " + ("-inf" if g <= 0 else f"{20 * math.log10(g):+.2f} dB")

if __name__ == "__main__":
    ap = argparse.ArgumentParser()
    ap.add_argument("--cell", type=int, default=14 * 44 + 27)
    ap.add_argument("--db", type=float, default=-6.0)
    ap.add_argument("--port", default="hw:4,0,1")
    a = ap.parse_args()
    before = read_cell(a.port, KI_MIX_FADER, a.cell)
    print("0. before :", fmt(before))
    if before is None:
        sys.exit("cell not reported, aborting (nothing written)")
    send(a.port, set_float_msg(KI_MIX_FADER, a.cell, 10 ** (a.db / 20))); time.sleep(0.5)
    after = read_cell(a.port, KI_MIX_FADER, a.cell)
    print("1. set    :", fmt(after))
    send(a.port, set_float_msg(KI_MIX_FADER, a.cell, f824(before))); time.sleep(0.5)
    restored = read_cell(a.port, KI_MIX_FADER, a.cell)
    print("2. restore:", fmt(restored))
    ok = after == to824(10 ** (a.db / 20)).to_bytes(4, 'big') and restored == before
    print("W1", "PASS" if ok else "FAIL")
    sys.exit(0 if ok else 1)
