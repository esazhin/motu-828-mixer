# MOTU 828 (gen5) control protocol: spec for a CueMix-equivalent main mixer

> **Status:** v0.1, 2026-10-12. Derived from MOTU CueMix 5's own JavaScript (the Windows install,
> datastore **API version 9**, which is byte-identical, CRLF aside, to the 2024 unofficial Linux build) and
> **checked against a live 828** (fw `1.1.8+2458f7dace00`, API 9). Each claim is tagged:
> **[CODE]** = read from MOTU's code · **[LIVE]** = observed on the real unit · **[TBD]** = must be confirmed by
> the write tests in §9 before anything depends on it.
>
> **Scope:** CueMix's main mixer (per-bus faders, pan, mute, solo, stereo links, bus masters), the output routing
> table, **live meters** (§4.7) and **input/output EQ** (§4.6). **Not covered (never written):** preamp gain,
> pad, **48V phantom power**, gate/compressor, reverb parameters, talkback, footswitch, clock, device name.

---

## 1 · Transport

- **[LIVE]** USB-MIDI, ALSA rawmidi port named **"828 COM"** (today `hw:4,0,1`). Resolve it **by name** (or by
  USB id `07fd:000e` + port index 1), never by card number. Port "828 MIDI" (index 0) is the DIN jack and
  ignores control messages.
- **[CODE]** Each property change is its own SysEx message. There is no batching on the MIDI transport (the
  1452-byte coalescing in `datastore.js` applies only to the WebSocket transport).
- One owner per port. A second client (CueMix under Wine, a stray `amidi`) breaks the session.
- **[LIVE] Input buffer:** after EnableSysexAPI the unit sends its whole state (~5,970 messages, ~80 KB) in one
  burst and never repeats it unasked. ALSA's default 4 KB rawmidi input buffer **overruns** while a userspace
  reader is briefly busy (`/proc/asound/cardN/midi0`: `Overruns: 4`), and the lost cells never return. Raise the
  input buffer to the kernel maximum (1 MiB, `snd_rawmidi_params_set_buffer_size`) and, as a safety net, re-send
  EnableSysexAPI (the unit resends everything) while the state is incomplete.

## 2 · Framing

```
F0 00 00 3B 00 01 <req> <packed body ...> F7
   └─ MOTU ──┘ └proto┘
<req> = direction<<6 | id      direction: 0 = host→device, 1 = device→host
id:   0x00 SetProperty · 0x01 ProtocolProbe · 0x02 EnableSysexAPI
```

| Message | Bytes | Tag |
|---|---|---|
| Probe (host→dev) | `F0 00 00 3B 00 01 01 F7` | [LIVE] |
| Probe reply | `F0 00 00 3B 00 01 41 F7` | [LIVE] |
| Enable reporting (host→dev) | `F0 00 00 3B 00 01 02 F7`. The device then sends **every property** (initial sync), then **streams changes continuously** | [LIVE] |
| SetProperty (dev→host) | `… 01 40 <pack7(body_in)> F7` | [LIVE] |
| SetProperty (host→dev) | `… 01 00 <pack7(body_out)> F7` | [CODE] **[LIVE: W1 PASS 2026-10-12]** |

### 2.1 Body layout. **It is asymmetric.**

```
body_in  (device→host): [id:u16be][index:u16be][value]            value length is implied by the type
body_out (host→device): [id:u16be][index:u16be][len:u16be][value]  len = byte length of value
```
- **[LIVE]** body_in: e.g. fader messages decode to 8 bytes (4-byte value), mute messages to 5 (1-byte value).
- **[CODE] [LIVE]** body_out from `CreateDeviceMessage` (`datastore.js`): the 2-byte **len** field is present. W1 proved it:
  `f0 00 00 3b 00 01 00 0a 03 78 02 03 00 04 00 05 00 4d 4e f7` set cell 643 to raw `00804dce` (−6.00 dB) exactly. Strings are
  always sent as a **32-byte, NUL-padded** value with `len = 32`.

### 2.2 7-bit packing (`pack7`), both directions [CODE] [LIVE for unpack]

The body is split into groups of up to 7 bytes. Each group is preceded by one **MSB byte** that holds the top
bit of each following byte: the first data byte's bit 7 goes to MSB-byte bit 0, the second's to bit 1, and so on.
Every data byte is then sent as `byte & 0x7F`.

```
pack7(body):                                  unpack7(packed):
  out = []                                      out = []; i = 0
  for g in chunks(body, 7):                     while i < len(packed):
    msb = 0                                       msb = packed[i]; i += 1
    for k, b in enumerate(g):                     for k in range(7):
      msb |= ((b >> 7) & 1) << k                    if i == len(packed): break
    out += [msb] + [b & 0x7F for b in g]            out.append(packed[i] | (((msb >> k) & 1) << 7)); i += 1
  return out                                    return out
```
(Equivalent to MOTU's `encodeDeviceMessage` / `DecodeSetPropertySysexMessage`. Property test:
`unpack7(pack7(x)) == x` for every byte string, and the decoder must reproduce the fixture's known values, §8.)

## 3 · Value types [CODE] (`dev_common.js`, `datastore.js`)

| Type tag | Wire value (big-endian) | Meaning |
|---|---|---|
| `F` float | 4 bytes, **8.24 fixed point**, signed: `raw = round(v × 2^24)` | linear gain, pan, … |
| `I` int32 | 4 bytes signed | |
| `I16` | 2 bytes | |
| `B` byte | 1 byte | bools (0/1), small enums, routing sources |
| `S` string | NUL-terminated in; **32 bytes NUL-padded out** | names |
| `A` array | raw bytes | MAC, serial |

Every property is an **array**: a scalar is an array of length 1 at `index 0`. One message carries one element.

## 4 · Mixer model

### 4.1 Inputs: 44 mixer channels (column order inside a bus) [CODE] [LIVE]

| idx | channel | idx | channel |
|---|---|---|---|
| 0–1 | Mic 1–2 | 20–27 | ADAT B 1–8 |
| 2–9 | Line 3–10 | 28–29 | Computer (host) 1–2 ("main") |
| 10–11 | S/PDIF L/R | 30–37 | Computer 3–10 ("line") |
| 12–19 | ADAT A 1–8 | 38–41 | Computer 11–14 ("phones") |
| | | 42–43 | Reverb return L/R |

### 4.2 Buses: 16 mono mix columns [CODE] [LIVE]

| bus | name | bus | name |
|---|---|---|---|
| 0–1 | Main 1–2 | 10–13 | Phones 1–4 (= Phones 1 L/R, Phones 2 L/R) |
| 2–9 | Ana 1–8 (feed Line out 3–10 by default) | 14–15 | Reverb send 1–2 |

A bus's output reaches physical jacks through the **routing table** (§5). The `dsp` patch source `76 + bus`
is that bus's mix output. Example [LIVE]: S/PDIF out L/R ← sources 78/79 = buses 2/3 (Ana 1–2).

### 4.3 Per-cell properties (index = `bus × 44 + input`, 704 cells) [CODE] [LIVE]

| Property | id | Type | Semantics |
|---|---|---|---|
| kiMixFader | **1016** | F | linear gain. **0 = −∞**, **1.0 = 0 dB**, **UI max 4.0 = +12 dB** (`fader.js` clamps 0..4). dB = 20·log10(g) |
| kiMixPan | **1017** | F | 0.0 = hard L, **0.5 = centre**, 1.0 = hard R |
| kiMixSolo | **1018** | B | 0/1 |
| kiMixMute | **1019** | B | 0/1 |

### 4.4 Per-channel / per-bus properties [CODE]

| Property | id | Type | Index | Semantics |
|---|---|---|---|---|
| kiMixStereo | **1000** | B | input 0..43 | 1 on the **first (even-index) channel** of a pair = pair linked (default: S/PDIF idx 10 = 1) |
| koMixStereo | **1001** | B | bus 0..15 | 1 on the first bus of a pair = stereo bus pair. [LIVE] today: Main 1-2, Ana 1-2, Ana 7-8, Phones 1-2, Phones 3-4, Reverb 1-2 linked |
| koBusFader | **1027** | F | bus | bus master level (same scale as 4.3). [LIVE] 16 entries (the code comment says 14) |
| koBusMute | **1028** | B | bus | bus master mute |
| kHostOutMixMode | 1020 | B | 0 | 0 = basic, 1 = extended host-out mix mode. **Read-only for v1** |

### 4.5 Stereo semantics: one cell per action [CODE]

**Corrected 2026-10-12 (verified in `mixmgr.js`):** CueMix writes **exactly one cell** per
control action: `index = get_mix_index(channels[n].gainICh, buses[b].gainOCh)`. For a linked **input** pair,
`gainICh` is the **first** channel; for a linked **bus** pair, `gainOCh` is the **first** bus. Bus master
(1027/1028) is written at the first bus only. The stereo toggle writes only the link bit at the even index.
[LIVE] supporting evidence: Main 2's column holds its own live value (Computer 2 at 0 dB) while Main 1 holds
the rest, so the earlier interim rule ("write both columns") would **overwrite real state**. Do not mirror.
W4 is no longer needed for v1.

### 4.6 EQ (the DSP's fixed parametric biquads) [CODE] [LIVE]

| Property | id | Type | Index | Semantics |
|---|---|---|---|---|
| kiEQMode / Bypass / Freq / Gain / BW | 1002–1006 | B / B / **I32 Hz** / F / F | `ch × 4 + band`, **10 ch** (Mic 1-2, Line 3-10) × **4 bands** | mode: 0 peak · 1 low shelf · 2 high shelf · 3 high-pass |
| koEQMode / Bypass / Freq / Gain / BW | 1022–1026 | same | `slot × 3 + band`, **14 slots × 3 bands** | one EQ per **mix bus** (below) |

- **Values [CODE] (`equi.js` kEQCtrlInfo):** frequency **20–20,000 Hz** (int32); gain **±20 dB** (8.24, signed); the
  property named "BW" **is Q**, edited by CueMix as **Q 0.45–10** (8.24). Bypass 0/1 per band.
- **Modes per band [CODE] (`kBand1EQModes`, `kBand4EQModes`):** first band peak / low shelf / high-pass; last band
  peak / high shelf; middle bands peak only. High-pass ignores gain and Q (fixed Butterworth); shelves ignore Q
  (fixed √2 slope) in CueMix's response math (`EQBand.CalculateCoeff`, RBJ-style biquads).
- **Output EQ belongs to the mix bus [CODE] (`mixmgr.js`: `busEq + bank channel`):** slot 0 Main 1-2 · 2–9 Ana 1–8
  (the buses that feed Line out 3–10) · 10 Phones 1-2 · 12 Phones 3-4 · 13 Reverb; a stereo-linked pair uses
  its first slot (1, 11 are the unused second slots of Main and Phones 1).
- [LIVE] Writes verified on an unused slot (1): frequency 500 Hz, gain −7.50 dB, Q 2.25 read back from the unit, restored.
- **Ceiling:** the device reports exactly 40 input and 42 output EQ cells. There is **no** delay, FIR/convolution,
  per-side Main EQ, extra bands, or measurement primitive anywhere in the protocol (API 9). The band count is
  the firmware's limit, not CueMix's. **Do not probe indices beyond the reported arrays** (undefined behaviour on the DSP).

### 4.7 Live meters: property 6000 [CODE] [LIVE]

- `kMetersID = 6000` (`dev_common.js`); CueMix feeds `data.slice(4)` straight into its meter store
  (`datastore.js`). One message, index 0, **128 bytes**, about **17 per second** once reporting is on, with no
  extra request. Each byte is a peak: **0 = 0 dBFS, −0.5 dB per step, 255 = nothing** (`meterstore.js`).
- Indices share the routing source codes (§5). Per mixer input: Mic 1-2 + Line 3-10 at their **post-FX taps 92–101**
  (pre-fader, after the input EQ/dynamics), S/PDIF in 58/59, ADAT A 60–63 + 68–71, ADAT B 64–67 + 72–75,
  Computer 1–14 = 0–13, reverb return 56/57. Per bus: its mix output `76 + bus` (reverb buses meter 56/57).
  102–125 are post-EQ, post-gate and gain-reduction taps. Table computed by running CueMix's own `dev_828.js`.
- Pre-/post-fader metering in CueMix is local UI arithmetic (`kMixInMeterMode`, id −1, never sent).
- [LIVE] On a real capture the post-FX taps track their own raw inputs (0.6 % disagreement, from input
  dynamics) against 69 % for a table shifted by one channel.

## 5 · Routing table (outputs) [CODE] [LIVE]

`kFPGAPatch` **5010**, type B, **one message per output slot**: `patch[output_slot] = source`. `127` = no
source. Optical-expander variant: 5020. Any source may feed any output: *a bus mix* (`76+bus`), **a direct input**,
*a computer channel*, or *nothing*. [LIVE] A direct route works: S/PDIF out ← Line 7-8 (45/32) with **no mix bus**
(read back from the unit). CueMix itself only offers mix buses for most outputs.

```
Address map (dev_828.js PatchBase, from 828mk5_patch.h)
PatchBase: host=0 (32 ch) · local=32 · digital=58 · dsp=76 · zero=127 ("nothing")
Sources:   Computer out n  = n-1 (0..31)
           local inputs    = 32 + LocalInputs[i]   LocalInputs = [15,2,18,5,14,1,13,0,17,4]  (Mic1,Mic2,Line3..Line10)
           S/PDIF in L/R   = 58 / 59               ADAT A = 60-63,68-71   ADAT B = 64-67,72-75
           bus mix b       = 76 + b  (b = 0..13)   reverb L/R = 90/91     post-FX input i = 92 + i
Outputs:   local line outs = 32 + LocalOutputs.lines[i]  lines = [5,7,21,24,22,19,6,9,11,8] (Main1,Main2,Line3..10)
           phones          = 32 + [15,25,18,20]   (1L,1R,2L,2R)
           S/PDIF out L/R  = 58 / 59               ADAT outs as ADAT sources   Computer in n = n-1
```

**Mixer-input slots [LIVE]:** patch slots **76–119 feed mixer inputs 0–43** (slot `76 + i` = the source of
mixer input i; live: slots 86/87 ← 58/59 S/PDIF in, slots 118/119 ← 90/91 reverb). They are not outputs:
v1 never writes them, and the S/PDIF guard follows them when it traces a path into the mixer.

**Hard guard:** refuse (do not send) any assignment that makes S/PDIF in (58/59) reach S/PDIF out (58/59),
directly or through a bus whose live faders include S/PDIF in. The Axe-Fx III is on both ends, so this
would be a feedback loop.

## 6 · Session lifecycle

1. Open "828 COM" → send **Probe** → expect the reply within 1 s (else: not an 828 / port busy).
2. Send **Enable** → the device sends the full property set. Treat the session as synced once all of these
   have arrived: ModelID (21), APIVersion (8), DevName (3), DevUID (2), as CueMix does [CODE].
3. **Verify the contract on boot:** `ModelID == "828E"` and `APIVersion == 9`. Anything else → **read-only
   mode, loud warning naming the mismatch** (firmware drift guard).
4. Steady state: keep consuming the stream (it never stops; prop 6000 ≈ meters/heartbeat, undecoded),
   applying every SetProperty to the local model. Changes from the front panel arrive here too.
5. Writes: send SetProperty (host→dev). **[LIVE] Read-back:** a fresh probe+enable session reports the new value.
   [TBD] whether a *live* stream echoes writes (W1's first attempt lost its capture to a killed `amidi` buffer, so this is
   unanswered, not negative). Design for both: update the local model optimistically, then let any echo overwrite it.
6. Disconnect or USB replug → reopen → probe → enable → full resync. No local state is trusted across sessions.

## 7 · Behavioral acceptance (what "mimics the CueMix main mixer" means)

```gherkin
Feature: Linux main-mixer control of the MOTU 828

  Background:
    Given the 828 is connected and its control port is free

  Scenario: The mixer looks and works like CueMix's mixer view
    When I open the mixer page
    Then I can pick a bus (Main, Ana 1-8, Phones, Reverb; stereo pairs shown as one bus)
    And I see one strip per input channel with fader, pan, mute and solo, plus the bus master
    And stereo-linked inputs appear as a single strip

  Scenario: Connect and mirror the unit
    When the mixer service starts
    Then it identifies the unit as an 828 by name, not by ALSA card number
    And every fader, pan, mute, solo, stereo link and bus master shown equals the unit's current state

  Scenario: Move a channel fader
    Given bus "Ana 1-2" is shown
    When I set "Line 5" to -6 dB
    Then the unit reports Line 5 on that bus at -6 dB (±0.1 dB)
    And no other cell changed

  Scenario: Mute, solo and pan behave like CueMix
    When I mute, solo or pan a channel on a bus
    Then the unit reports exactly that change on that bus only

  Scenario: Stereo pairs move together
    Given "Line 5/6" are a linked stereo pair on a stereo bus
    When I move the pair's fader
    Then both channels of the pair change identically on the unit

  Scenario: Bus master level and mute
    When I change the master level or mute of bus "Main 1-2"
    Then the unit reports the new master state for that bus

  Scenario: Changes made on the hardware appear in the UI
    When the level of a cell changes on the unit itself (front panel or another client)
    Then the UI shows the new value within 1 second without a reload

  Scenario: Route a mix to an output
    When I route bus mix "Ana 1-2" to "S/PDIF out"
    Then the unit's routing table reports S/PDIF out L/R fed by that mix

  Scenario: Feedback-loop guard
    When I attempt any routing or mix change that would carry S/PDIF in to S/PDIF out
    Then the change is refused with a message naming the loop
    And nothing is sent to the unit

  Scenario: Firmware drift
    Given the unit reports a model or API version other than 828E / 9
    When the service starts
    Then it runs read-only and says which value did not match

  Scenario: Survive a replug
    When the 828 is unplugged and plugged back in
    Then the UI reconnects by itself and shows the unit's state again

  Scenario: Use it from any device on the LAN
    When I open the mixer page from a phone on the home network
    Then I see and control the same mixer state
```

## 8 · Testing

- **Hermetic unit tier** (no hardware, zero prereqs): pack7/unpack7 round-trip; decoding
  `fixtures/828-property-dump-2026-10-12.hex` must reproduce these **known values [LIVE]**: model `828E`,
  serial `828E000000` (scrubbed placeholder), API 9; bus 0 cell Line 3 = 1.000 (0.0 dB) pan 0.0; bus 2 (Ana 1) Line 7 muted;
  S/PDIF out L/R ← 78/79; koMixStereo = `[1,0,1,0,0,0,0,0,1,0,1,0,1,0,1,0]`. Encoder: body_out byte layout
  per type (§2.1, §3), feedback guard, index math (§4.3).
- **Live tier** (opt-in, the 828 attached): the firmware is the real reference, so a small set of checks runs
  against the unit itself: probe, enable, sync, plus the write tests W1–W5 below. The simulator and fakes used
  by the unit tier are built from this spec and the recorded fixture, so the live tier is what keeps them honest.
- **Boot preflight** in the service: §6 step 3 (model/API check), failing loudly.

## 9 · Write-verification plan: run BEFORE building on any [TBD]

Each test reads the value, writes a new one, reads it back from the stream, then **restores the original**:

| # | Write | Proves |
|---|---|---|
| W1 ✅ | kiMixFader cell 643 (bus 14 Reverb 1 × ADAT B 8) → −6 dB → restored. `tools/motu828_w1.py` | **PASS 2026-10-12**: body_out incl. `len`, pack7, 8.24, index math. (Echo: still open) |
| W2 | kiMixMute on the same cell → 1 → 0 | byte type path |
| W3 | koBusFader on an unused bus | per-bus addressing |
| ~~W4~~ | dropped: §4.5 is settled from CueMix code (one cell per action, first channel/bus), so no live test is needed | stereo semantics (§4.5) [CODE] |
| W5 ✅ | kFPGAPatch: an **unused** output slot (Line out 10, slot 40) → 127 → restore | **PASS 2026-10-12**: routing write path |

Safety: start with W1 on a muted, unused cell. Never touch 48V (5004), gain or pad. Keep `amidi` or the
client bounded with `timeout`. Record every result back into this spec and flip [TBD] → [LIVE].
