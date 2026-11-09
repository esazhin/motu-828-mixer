# Monitor chain: multi-stage EQ on the Main output

> | meta | |
> |---|---|
> | id | `monitor-chain` |
> | created | `2026-11-08` |
> | status | draft · locked (2026-11-08) · delivered (2026-11-08) · **verified** (2026-11-09, W8 ✅) |
> | builds on | [PROTOCOL-SPEC.md](../PROTOCOL-SPEC.md) §4.6 (EQ), §5 (routing, W7, mixer latency) |

## 1 · Intent

The 828 gives each mix bus one 3-band EQ, and the firmware's band count is fixed (§4.6 ceiling). Room
correction on the monitor path wants more than three bands. The protocol allows a chain: a bus's mix can be
patched into a mixer input (W7), that input mixed on a second bus, and that bus's output sent to the Main
jacks. Every stage adds one 3-band EQ and **one sample** of latency (42 µs for two passes, measured
2026-11-08). The chain is therefore a pure gain in fidelity, with no timing reason to switch it off: the
only reason to switch it off is to give the borrowed buses and inputs back to the hardware.

The same mechanism is a **preset**: a named routing takeover that the user switches on and off from the page.
Off means "hardware takes precedence, as it was before": every slot and cell the preset touched returns to its
native mapping.

## 2 · Behaviour

Role: binding.

### 2.1 Vocabulary

- **Stage**: a stereo bus pair that carries the chain. Stage 0 is Main 1-2 itself. Stage n ≥ 1 is one of the
  Ana pairs (1-2, 3-4, 5-6, 7-8), its 3-band output EQ being the stage's EQ.
- **Carrier**: the stereo mixer-input pair that brings stage n−1's mix into stage n. Any pair of mixer
  inputs 10–41 may be borrowed (S/PDIF, ADAT A/B, Computer 1–14); Mic/Line 1–10 never, since they are physical
  inputs with their own input EQ and dynamics; the **reverb return (42-43) never**: switching a chain on through
  it (2026-11-08, live) put the built-in reverb on the monitor path, so its slots 118/119 feed the reverb
  processor and the mixer hears the processor's output. **Default first carrier: ADAT B 7-8 (inputs 26-27,
  slots 102/103)**: idle on this unit, proven writable by W7, and the pair a future ADAT expander needs last.
- **Chain**: Main → carrier₁ → stage₁ → carrier₂ → stage₂ … → Main jacks. Up to **four** stages on this
  unit (the four Ana pairs): 15 bands, 5 samples. **Default first stage: Ana 5-6** (buses 6-7), which feeds no
  jack on this unit.
- **Scope**: the Main jacks only. The Phones buses are never a chain target or a stage: this is room
  correction, and headphones are corrected elsewhere (VSX).
- **Native mapping**: for every mixer-input slot `76 + i` the one source the firmware assigns it (Mic/Line
  codes 32+LocalInputs, S/PDIF 58/59, ADAT 60–75, Computer 0–13, reverb 90/91; §5). For the Main jacks it is
  Main mix 76/77. The native mapping is derivable, so **off never needs stored state**.

### 2.2 Switching the chain on

One atomic action, planned fully before anything is sent, refused as a whole if any step is refused:

1. Link each stage pair (koMixStereo) and each carrier pair (kiMixStereo) if not already linked.
2. Silence each carrier (−∞) on **every bus but its own stage**, so the mix it is about to carry cannot feed back.
   Then hand back every carrier pair an earlier chain borrowed (slots not at their native source) that this chain
   does not use: unsoloed, silent, slots native. A preset loaded while on, or a stage removed, therefore leaves the
   unit wired exactly as the window shows (fixed 2026-11-09: before this, the dropped stage stayed wired and
   recognition kept showing it until off).
3. Patch carrier n's two slots (`76 + input`) to stage n−1's mix outputs (`76 + bus`, `77 + bus`).
4. On stage n: carrier n at 0 dB (the pair's primary cell, §4.5), unmuted, **soloed** (so the stage pair's own
   mix, whatever it carries today, is silent while the chain is on); stage n's master at 0 dB, unmuted.
5. Main jacks (slots 37, 39) ← last stage's mix outputs.

Order matters for silence: silence, patches, levels, then the jacks last, so the monitors never hear a half-built
chain and no carrier ever carries a mix it is live on. Switching off reverses the order: jacks first (back to
76/77), carriers unsoloed and to −∞ on their stage, carrier slots back to native, links left as they were found.
Off also hands back any borrowed pair it finds that no recognised chain uses, so it repairs a half-built state.
The solo is why a stage's own mix needs no stored state: its faders are never touched.

### 2.3 Guards

- **Self-feed**: a bus must never reach a mixer input that is live (fader > −∞ and unmuted) on that same bus,
  directly or through any number of hops. This generalises the S/PDIF guard and is checked on every write,
  not only chain actions, so a later fader move cannot close a loop either.
- **Ceiling**: stage buses are Ana pairs only; carriers are inputs 10–43 only; at most four stages; a bus or
  input appears in the chain once.
- **Conflicts shown, not hidden**: a stage whose jacks carry something today (Ana 1-2 → Line out 3-4 …), a stage
  pair with live inputs of its own (soloed out while on) and a carrier whose native source is live in a mix are
  allowed, but the picker says what is taken over.
- The write gate, allowlist and EQ range checks apply unchanged.

### 2.4 Recognising a chain

The service derives the chain from the state alone: Main jacks fed by `76 + b` with b ≠ 0 names the last
stage; a stage's carrier is the linked input pair whose slots point at the previous stage's outputs. A chain
built by this service, by CueMix or by hand is shown the same way, and a restart loses nothing.

### 2.5 The page

- **Switch**: a `Chain` toggle on the Main tab, lit when a chain is on. Off with no stages configured opens
  the chain window.
- **Chain window** (an overlay like the EQ panel, `/#chain`): an ordered list of stages, Main first. Add a
  stage (pick a free Ana pair and a free carrier pair; the picker lists what each pick takes over), remove a
  stage, reorder. Each stage row shows its bus meter and an EQ button opening that stage's existing EQ panel.
- **Combined EQ**: one response graph summing every stage, band cards grouped and coloured by stage, each
  card editable exactly as in the single-bus panel (same properties, same ranges). The graph is the product
  of the per-stage responses, computed with the existing `eq_math.js`.
- **Meters**: a carrier has no meter of its own (§5, W7); stage meters are the bus meters.
- A configuration (stages and carriers) is kept per browser so "on" after "off" rebuilds the same chain; the
  unit's state remains the truth for what is on now.
- **Presets**: a named preset holds the stages and the output EQ of Main and of every stage ([mode, bypass,
  freq, gain, q] per band), kept per browser. Save stores the chain as shown; Load writes back every band that
  differs, then switches the chain on with the preset's stages (nothing is sent for what already matches);
  Delete forgets it. The name last saved or loaded is shown in the window and on the Main master's `Chain`
  button while the unit's chain and EQ still match it, marked edited (`*`) once a band or stage differs.
- Double-click on any EQ point, in the single-bus panel or the combined view, zeroes that band's gain.

### 2.6 Acceptance

| id | scenario | passes when |
|---|---|---|
| C1 | On with one stage while music plays on Main | Main jacks read the stage's outputs, the carrier slots read 76/77, stage level equals Main's ±0.5 dB, no loop reported. **Passed 2026-11-09** (Ana 7-8 carried by ADAT A 5-6: 0.0 dB by side-by-side recording, once the carrier is soloed; +6 dB before, from the stage's own mix) |
| C2 | Off | every touched slot and cell equals its native value; Main jacks 76/77. **Passed 2026-11-09** (fresh sync) |
| C3 | Two stages | Main jacks read the second stage; the first stage's carrier reads 76/77, the second's reads the first stage's outputs |
| C4 | Self-feed attempt (raise a carrier on its own source bus) | refused with the path named; nothing sent |
| C5 | Restart with a chain on | the page shows the chain on, with the right stages, without any write |
| C6 | Combined EQ | editing a band in the combined view writes that one stage's property; the summed curve equals the product of the stage curves |
| C7 | Latency (live) | the chained Main jack output lags the direct one by about n samples for n stages. **Measured 2026-11-09**: one stage, peak at lag 0, 0.46 sample interpolated (≈ 10 µs) |

## 3 · Binding design

Role: binding.

- `domain/chain.py`: `Stage`, `Carrier`, `Chain` values; `native_source(slot)`; `recognise(state) -> Chain`;
  `plan_on(chain, state)` / `plan_off(state)` returning ordered `Write`s or a `Refusal`.
- `domain/loop_guard.py`: generalised to any bus → input → same bus path; the S/PDIF rule stays as the
  stricter special case.
- `domain/properties.py`: slots 76–119 become writable **only through chain actions**; `SetRouteSlot` and
  `SetProperty` on them stay refused.
- `actions.py`: `SetChain(on: bool, stages: tuple[...])`; `view_model.py`: `chain` in the state body
  (recognised stages, carriers, on/off) and a `set_chain` parser.
- Page: `tab`-level `Chain` toggle, `chain` overlay, combined EQ built from the existing EQ view code.
- Spec: new §5.1 "Chains" with the native-mapping table and the W8 live test (C1–C3, C7).

## 4 · Decisions taken at lock

- First carrier pair: ~~the reverb return~~ **ADAT B 7-8** (changed 2026-11-08 after the first live switch-on:
  the reverb return carries the built-in reverb, so the chain through it was audibly reverberant). First
  stage: **Ana 5-6**.
- The carrier is **soloed on its stage** (added 2026-11-09, W8): a stage pair is rarely empty, and anything else it
  mixes adds to the chain. On this unit Ana 7-8 carried Computer 1-2 as well, so the music arrived twice and the
  stage ran 6 dB hot. Solo silences the rest without touching their faders, and off simply unsolos.
- **Phones are out of scope**, as target and as stage. Room correction only; headphone correction is done by VSX.
