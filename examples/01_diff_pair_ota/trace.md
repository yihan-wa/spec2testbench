# Running Example 01: 5-Transistor Differential Pair OTA

End-to-end manual walkthrough of the spec → IR → netlist → simulate → evaluate
pipeline. The goal of this document is NOT to produce a working testbench
(that's easy by hand) — it is to surface, at every stage, what is missing
from the inputs and what assumptions a human silently fills in. Those
observations feed Step 2 of the project (固化 IR schema + 评估准则).

---

## 0. Environment

- **Date:** 2026-05-13
- **ngspice version:** ngspice-45.2 (Compiled with KLU Direct Linear Solver)
- **OS:** macOS (darwin 25.4.0)
- **MOSFET model:** level=1 (generic textbook params, no PDK)

---

## 1. NL spec

> We need to verify the small-signal performance of our 5-transistor
> differential-pair OTA. The DC gain should exceed 60 dB, and the
> unity-gain bandwidth should be at least 10 MHz when driving a 1 pF
> capacitive load on the output. Please run AC analysis from 1 Hz up
> to 1 GHz. TT corner at 27 °C is sufficient for now — we'll add
> corners later.

### Intentional ambiguities (for downstream stages to surface)

- "small-signal performance" implies AC, but analysis type is never named explicitly.
- 1 pF load is on "the output" — node name not given.
- AC sweep type (dec / lin) and points-per-decade not specified.
- "DC gain" = magnitude at what frequency? Asymptotic low-f, or a specific point?
- ">" vs ">=" not distinguished for pass criteria.
- Input drive: single-ended? Differential? AC magnitude unspecified.
- Bias current and supply voltage entirely absent (designer assumes PDK defaults).
- Output common-mode / DC operating point requirement is implicit.

---

## 2. Gold IR (hand-written)

Written from scratch using a flat single-record JSON as a rough straw-man, deliberately
without consulting any existing pydantic schema. Comments (`//`) call out every
field I had to invent or every place the NL spec was silent. JSONC, not strict JSON.

```jsonc
{
  "dut": {
    "name": "diff_pair_ota_5t",
    "topology_hint": "5-transistor differential pair OTA",
    // GAP-A: NL never names the input/output ports. Schema needs either
    // (a) role→net mapping here, or (b) defer entirely to DUT netlist port order.
    "ports": {
      "inp": "vinp",
      "inn": "vinn",
      "out": "vout",
      "vdd": "vdd",
      "vss": "0"
    }
  },

  // GAP-B: NL has TWO spec items (DC gain, UGB) but they share ONE analysis
  // run (single AC sweep). Schema design choice: list per-spec test_items
  // (duplicates analysis block) vs. one analysis + multiple measurements.
  // Going with the latter; flagging this as the more natural shape.
  "analyses": [
    {
      "id": "ac_smallsig",
      "type": "AC",
      // GAP-C: NL doesn't say dec/lin sweep nor points-per-decade.
      // Inventing "dec, 20 pts/decade" as a default. Where should this
      // default live — IR, emitter, or PDK profile?
      "sweep_style": "dec",
      "points_per_decade": 20,
      "f_start": "1Hz",
      "f_stop": "1GHz"
    }
  ],

  // GAP-D: Differential vs single-ended drive is implicit. A "diff-pair OTA"
  // is conventionally tested with mag=+0.5 on inp, -0.5 on inn (differential
  // stimulus with common-mode = 0). NL never says this.
  "stimulus": [
    {
      "kind": "AC_voltage",
      "port": "inp",
      "magnitude": 0.5,
      "phase": 0
    },
    {
      "kind": "AC_voltage",
      "port": "inn",
      "magnitude": 0.5,
      "phase": 180
    }
  ],

  // GAP-E: Is `loading` analysis-scoped or test-plan-scoped?
  // Here it's plan-scoped (one C across the whole sim). If you had two
  // analyses with different loads, you'd need per-analysis loading.
  "loading": [
    {
      "kind": "C",
      "value": "1pF",
      "from": "out",
      "to": "0"
    }
  ],

  "measurements": [
    {
      "id": "dc_gain",
      "from_analysis": "ac_smallsig",
      // GAP-F: "DC gain" in an AC sweep = magnitude at the lowest swept
      // frequency (the asymptote). No clean primitive exists. Options:
      //  - "max(vdb(out))" (assumes monotone roll-off — usually true here)
      //  - "vdb(out) @ f=f_start"
      //  - dedicated "low_frequency_gain" primitive
      // Going with @f_start, more semantically honest.
      "metric": "vdb(out) @ f=f_start",
      "unit": "dB"
    },
    {
      "id": "ugb",
      "from_analysis": "ac_smallsig",
      // GAP-G: "Unity-gain bandwidth" = freq where |H(f)| crosses 1 (= 0 dB).
      // Requires a "find first frequency where vdb(out) crosses 0 going
      // downward" primitive. ngspice .measure can express this; schema needs
      // a generic "find x where y crosses value" form.
      "metric": "freq_where(vdb(out) crosses 0 falling)",
      "unit": "Hz"
    }
  ],

  "pass_criteria": [
    {
      "measurement": "dc_gain",
      "op": ">",
      "value": 60,
      "unit": "dB"
    },
    {
      "measurement": "ugb",
      "op": ">=",
      "value": 10e6,
      "unit": "Hz"
    }
  ],

  "corners": [
    {
      "process": "TT",
      "temperature_celsius": 27
      // GAP-H: supply voltage not bound to corner. PDK convention?
    }
  ],

  // GAP-I: NL never says VDD, bias current, input common-mode. These are
  // "designer assumed defaults" — must come from PDK or a global_setup block.
  // Putting them here as nullable to make the gap explicit.
  "global_setup": {
    "vdd": null,
    "ibias_tail": null,
    "vin_common_mode": null
  }
}
```

### Schema gaps observed

| ID | Gap | Why it matters | Temporary handling |
|----|-----|----------------|--------------------|
| A  | DUT port-name mapping not in NL | Emitter needs to know which net to drive / probe | Hardcoded `vinp/vinn/vout/vdd/0` in IR |
| B  | One AC run can produce many measurements; IR must support 1-analysis-N-measurements | Avoids duplicating sweep configs and re-running sim | Split `analyses` and `measurements` into separate top-level arrays |
| C  | Sweep style + density (dec/lin, pts) silent in NL | Determines sim runtime and frequency resolution | Defaulted to `dec, 20 pts/decade` in IR |
| D  | Differential vs single-ended stimulus implicit | Wrong drive style → totally wrong gain numbers | Wrote balanced ±0.5 mag, 0°/180° pair |
| E  | `loading` scope ambiguous (plan-wide vs per-analysis) | Multi-analysis plans break under plan-wide scope | Documented as plan-scoped; revisit when 2nd analysis appears |
| F  | "DC gain" has no clean primitive in an AC sweep | Different choices give slightly different numbers | Used `vdb(out) @ f=f_start` |
| G  | "UGB" needs a generic "find-crossing" primitive | Most bandwidth-style specs need this shape | Used `freq_where(... crosses ... falling)` syntax (made up) |
| H  | Corners carry process + temp but not supply voltage | Real corners (e.g., ff_0p9V) bundle voltage too | Left supply out of corner entry, in `global_setup` |
| I  | VDD / ibias / Vcm absent from NL | Without them sim won't bias correctly, gain = garbage | Made `global_setup` nullable; values to be filled in Stage 4 from "PDK defaults" |

**Meta-observation:** the IR splits cleanly into 6 sections — `dut`, `analyses`,
`stimulus`, `loading`, `measurements`, `pass_criteria`, `corners`, `global_setup`.
The figure-2 single-flat-record style mixes all of these and breaks down once
you have more than one measurement. This is the first concrete piece of
feedback for Step 2 (schema固化).

---

## 3. DUT netlist

Saved as a sibling file: `dut.cir`. Reproduced here for trace completeness.

```spice
.subckt ota5t  vinp vinn vout vbias vdd vss
* PMOS current-mirror load
M3  n1    n1    vdd  vdd  PMOD  W=80u L=1u
M4  vout  n1    vdd  vdd  PMOD  W=80u L=1u
* NMOS input differential pair
M1  n1    vinp  tail vss  NMOD  W=40u L=1u
M2  vout  vinn  tail vss  NMOD  W=40u L=1u
* NMOS tail current source
M5  tail  vbias vss  vss  NMOD  W=20u L=1u
.ends ota5t

.model NMOD NMOS (level=1 vto=0.5  kp=100u lambda=0.01)
.model PMOD PMOS (level=1 vto=-0.5 kp=50u  lambda=0.01)
```

### Design choices baked into the DUT (= "PDK-equivalent defaults")

These are the numbers that will fill the `null`s in IR.`global_setup` in
Stage 4. They are not in the NL spec and not in the IR — they live with the
DUT + (in production) the PDK profile.

| Quantity | Value chosen | Where it gets used in Stage 4 |
|---|---|---|
| VDD            | 1.8 V          | `global_setup.vdd` |
| Vbias_tail     | 0.6 V          | sets I_tail ≈ 10 µA via M5 sizing |
| I_tail (target) | 10 µA          | designer intent; not directly written, falls out of Vbias |
| Vin common-mode | 0.9 V (≈ VDD/2) | `global_setup.vin_common_mode` |
| W/L M1,M2 | 40u / 1u | drives gm, hence DC gain |
| W/L M3,M4 | 80u / 1u | matched g_m for current-mirror, contributes to ro |
| W/L M5    | 20u / 1u | tail current device sizing |
| lambda (both) | 0.01 | chosen low → ro high → DC gain ≥ 60 dB |
| KP_n / KP_p   | 100u / 50u (2:1) | textbook PMOS-half-of-NMOS-mobility ratio |
| Vt_n / Vt_p   | 0.5 / −0.5 V | typical low-voltage values |

### Observations from writing the DUT

- **Subckt port order is a contract.** I chose `vinp vinn vout vbias vdd vss`.
  This order is **not in the IR** — the IR only names roles (`inp`, `inn`,
  `out`, `vdd`, `vss`). Gap-A from §2 confirmed: emitter needs role→port
  mapping or has to assume order. Adding `vbias` as a 4th port is also a
  gap — IR doesn't model bias-pin ports today.
- **Models inlined with the subckt** for hermeticity. In a real PDK flow,
  models live in a separate `.lib` file. Schema needs a `model_source` field
  (`inline` / `lib` / `pdk_corner_callup`).
- **Body terminals (4th MOSFET pin)** matter: M1/M2 bulk tied to `vss`
  causes body effect at the tail node (~0.35 V above vss). Level-1 models
  this fine, but the IR's stimulus / loading sections have no concept of
  bulk-bias networks; if a future test needed reverse body bias, current IR
  couldn't express it.

### New gap (J) discovered in Stage 3

| ID | Gap | Why it matters | Temporary handling |
|----|-----|----------------|--------------------|
| J  | Subckt port-list and "bias pin" not modelled in IR | Emitter has to know `vbias` exists as a port, not as an internal node | Out-of-band: testbench (Stage 4) drives `vbias` directly; IR is silent about it |

---

## 4. Full testbench (hand-written)

Saved as a sibling file: `testbench.cir`. Reproduced here (without the long
comment header) so the trace stays self-contained.

```spice
.include "dut.cir"

* Supplies (from §3 PDK-equivalent defaults)
V_VDD   vdd    0     DC 1.8
V_BIAS  vbias  0     DC 0.6
V_CM    vcm    0     DC 0.9

* Differential AC stimulus around common-mode (IR §2 stimulus block)
V_INP   vinp   vcm   DC 0  AC 0.5  0
V_INN   vinn   vcm   DC 0  AC 0.5  180

* Output load (IR §2 loading block)
C_LOAD  vout   0     1p

* Instantiate DUT (subckt port order: vinp vinn vout vbias vdd vss)
X1  vinp vinn vout vbias vdd 0  ota5t

.ac dec 20 1 1G

.measure ac dc_gain find vdb(vout) at=1
.measure ac ugb     when vdb(vout)=0 cross=1

.end
```

### Knowledge gap: info the emitter needed beyond IR + DUT

Every row below is something that **had to be invented** during emit; none of
it is derivable purely from IR §2 + dut.cir. These are the emitter's "knowledge
debts" — Step 2 must decide where each one lives (in IR, in PDKContext, in DUT
metadata, or hard-wired in the emitter).

| ID | Knowledge debt | Source it would come from in production | Notes |
|----|----------------|------------------------------------------|-------|
| K1 | `.include "dut.cir"` mechanism + filename | DUT-metadata (path to netlist) | IR has no "where is the DUT" field |
| K2 | Subckt port order `vinp vinn vout vbias vdd vss` | DUT-metadata (subckt signature) | IR only names roles, not order |
| K3 | Need a `vcm` node and a `V_CM` source to set input common-mode | PDKContext / designer convention | IR `global_setup.vin_common_mode` = 0.9V but never says "create a vcm node" |
| K4 | Differential AC done as two sources referenced to `vcm`, ±0.5 mag, 0°/180° | Convention encoded in emitter | IR §2 declared the two sources but not the `vcm` reference node |
| K5 | `vbias` is a DC source pin, not an AC stimulus | DUT-metadata (port roles) | Gap-J from §3 — IR has no concept of bias-injection ports |
| K6 | ngspice-specific `.measure` syntax for "value at frequency" → `find ... at=` | Emitter (dialect-specific) | IR primitive `vdb(out) @ f=f_start` had to be lowered to `find vdb(vout) at=1` |
| K7 | ngspice-specific `.measure` syntax for "first downward zero crossing" → `when ...=0 cross=1` | Emitter (dialect-specific) | IR primitive `freq_where(... crosses 0 falling)` had to be lowered; `cross=1` doesn't enforce direction in ngspice — could match rising too (latent bug) |
| K8 | Default analysis temperature (27 °C) is implicit in ngspice; IR corners said TT@27°C | Emitter knows ngspice default = 27°C | If corner were 85°C, would need `.option temp=85` or `.temp 85`; emitter must know to emit it |
| K9 | Convergence options (`.option reltol`, `.option abstol`) | Simulator profile | Not emitted because ngspice defaults work for this small circuit; will bite on larger DUTs |
| K10 | `f_start = 1` resolves the IR token `f=f_start` | Cross-section reference inside IR | Emitter had to look up `analyses[0].f_start` to fill the `at=` argument |
| K11 | Net-name convention: subckt-internal `vout` is also the top-level node name `vout` (because `X1 ... vout ...` passes the top-level `vout` as the 3rd arg) | Emitter / DUT-metadata | Easy to get wrong if subckt port order ≠ instantiation order |

### Observations from writing the testbench

- **Biggest single surprise:** the `vcm` reference node has *no representative*
  in IR §2. The IR says "differential AC stimulus" but the implementation
  requires inventing an extra node `vcm` driven by a third DC source. Step 2
  should decide whether `vcm` is implicit in IR (then emitter manufactures it)
  or explicit (a `stimulus` entry of `kind: DC_voltage`).
- **`.measure` is dialect-specific and brittle.** Both measurements lowered
  from one-line IR expressions to ngspice-specific `.measure` statements with
  non-obvious flags (`at=`, `cross=1`). Spectre / HSPICE will differ. This is
  exactly why we need a strict IR primitive set (per Gap-F discussion).
- **`cross=1` is too loose for UGB** — it matches the *first* zero crossing
  regardless of direction. For a strictly monotone-falling AC response this is
  fine, but if the response has peaking near UGB it could fire on the wrong
  crossing. The right semantic is "first crossing going downward". This is a
  latent semantic bug in the emitter we just wrote.
- **Temperature was free** because ngspice's default is 27 °C. If the IR had
  said 85 °C, the emitter would need to know to emit `.option temp=85`. So
  K8 only didn't bite because we got lucky with the corner choice.
- **No model-include line needed** because dut.cir already inlines the
  `.model` statements. In a real PDK flow these are in a separate library
  file selected by corner — emitter would need a `.lib "...tt.lib" tt` line
  driven by the corner spec.

### Effective net list of gaps so far

After Stage 4 we have **10 schema gaps (A–J)** from §2/§3 plus **11
knowledge debts (K1–K11)** from this stage. Two distinct populations:

- **Schema gaps (A–J):** "what fields should IR have" — feed Step 2 schema design
- **Knowledge debts (K1–K11):** "what context does emitter need from outside IR" — feed Step 2 emitter design + PDKContext design

These two lists are the actual deliverable of the running example, far more
than the working testbench is.

---

## 5. Simulation run

- **Command:** `ngspice -b testbench.cir -o testbench.log`
- **Run from:** `examples/01_diff_pair_ota/`
- **Iterations to converge (edits before it ran):** 3 (2 failures, then success)
- **Final run output (testbench.log tail):**

```
Doing analysis at TEMP = 27.000000 and TNOM = 27.000000
Using SPARSE 1.3 as Direct Linear Solver
No. of Data Rows : 181
dc_gain_lin         =  2.019711e+03
ugb                 =  3.206502e+07
```

### Iteration history

| # | What I tried | Outcome | Root cause |
|---|--------------|---------|------------|
| 1 | `.measure ac dc_gain find vdb(vout) at=1` and similar for UGB | **Error:** `can't parse 'vd'`; analysis aborted | Raw `.measure ac` with `vdb()` not parsed by ngspice 45.2 in default compatibility mode |
| 2 | Changed `vdb()` → `vm()` (linear magnitude) | **Error:** `can't parse 'vm'`; same failure | Same root cause — the `.measure` parser itself rejects these function names without HSPICE compatibility mode |
| 3 | Replaced `.measure ac …` with native `.control { ac … ; meas ac … ; .endc }` block, kept `vm()` | **Success.** 181 data rows, both measurements emitted | ngspice's native expression evaluator (used inside `.control`) handles `vm()`/`vdb()` correctly; the `.measure` _statement_ form is brittle without compat flags |

### Implications for the emitter (new K-rows)

| ID | Knowledge debt | What this means |
|----|----------------|-----------------|
| K12 | ngspice prefers `.control … .endc` over raw `.measure ac` | Emitter targeting ngspice should default to `.control` blocks for measurements, not standalone `.measure` lines (which work in HSPICE/Spectre) |
| K13 | dB-form measurements (`vdb`) are unsafe in standalone `.measure`; safe inside `.control` | Either always use `.control`, or post-process from linear |
| K14 | "No compatibility mode selected" is a silent warning — analysis fails downstream | Emitter could pre-emptively emit `.options ngbehavior=hs` to ease pain, but that changes other behaviors |

### Raw numerical results (still linear — dB conversion in Stage 6)

| Quantity | Raw value from ngspice |
|---|---|
| `dc_gain_lin` (|H| @ 1 Hz) | 2.019711 × 10³ |
| `ugb`         (Hz where |H|=1) | 3.206502 × 10⁷ |

---

## 6. Measurement extraction

### Final measurements (after unit conversion)

| Spec item | Measured value | Spec unit | Extraction method |
|---|---|---|---|
| DC gain | 66.1058 dB | dB  | `meas`-echoed line in log → grep → `20·log10(·)` in Python |
| UGB     | 32.0650 MHz | MHz | `meas`-echoed line in log → grep → `/1e6` in Python |

Conversion script (one-shot, not committed as code):

```python
import math
g_lin = 2.019711e+03   # from log line: dc_gain_lin = ...
ugb_hz = 3.206502e+07  # from log line: ugb = ...
g_db  = 20 * math.log10(g_lin)        # → 66.1058 dB
ugb_M = ugb_hz / 1e6                   # → 32.0650 MHz
```

### Extraction method options (and their automation cost)

Three ways to get those numbers out of a ngspice run, ranked by automation friendliness:

| Option | How it works | Pros | Cons |
|---|---|---|---|
| (A) Manual read of log | Eyeball `testbench.log` for `meas`-echoed lines | Zero setup; what we did first | Doesn't scale; no machine contract |
| (B) Grep + parse | `grep -E 'dc_gain_lin\|ugb' testbench.log` → strip whitespace → float() | Trivial; ngspice format is stable | Names hard-wired; fragile if log layout changes |
| (C) `wrdata` / `print` to file | Add `wrdata results.csv dc_gain_lin ugb` inside `.control` | Produces a clean .csv | Adds another file; ngspice has quirks with `wrdata` for scalars |

For v0 (and probably the whole benchmark harness), **Option B is the right
default** — it's robust enough, doesn't require changing the testbench, and
keeps the evaluator decoupled from simulator internals.

### Where dB-conversion lives (and why this matters)

The fact that **dB conversion happens outside the simulator** (in post-processing)
is a direct consequence of K12/K13 from Stage 5. The evaluator subsystem must
therefore know:

- which raw quantity the IR measurement maps to (`dc_gain_lin` not `dc_gain_dB`)
- what unit transform to apply before comparing to the pass criterion

This is a new piece of "knowledge debt" the evaluator carries, distinct from
the emitter's K1–K14. Call it the **evaluator's transform table**: per-IR-
measurement, a (raw_name, raw_unit) → (spec_name, spec_unit) mapping with the
function in between.

| ID | Knowledge debt (evaluator-side) | Why it matters |
|----|---------------------------------|----------------|
| E1 | dc_gain measurement: raw `vm()` linear → compare in dB requires `20·log10` | If evaluator compares 2019.7 to "> 60", it would always pass for any reasonable gain — silent wrong answer |
| E2 | ugb measurement: raw Hz → spec said "MHz" via natural-language | Numerical comparison must reconcile units; `>= 10 MHz` and `>= 1e7 Hz` are equivalent only if the evaluator parses spec units |

Both E1 and E2 are **silent-bug class**: if the evaluator forgets the
transform, the pipeline still runs end-to-end and produces a pass/fail —
just the wrong one. This reinforces why a strict IR primitive set
(per Gap-F discussion) is critical: the IR should declare both the raw
quantity and the spec unit so the evaluator can't omit the transform.

---

## 7. Pass / fail

### Per-spec verdict

| Spec item | Pass criterion | Measured | Margin | Verdict |
|---|---|---|---|---|
| DC gain | `> 60 dB` (strict) | 66.11 dB | +6.11 dB | ✅ PASS |
| UGB     | `>= 10 MHz` (inclusive) | 32.07 MHz | +22.07 MHz | ✅ PASS |

### Overall: **✅ PASS** (both spec items satisfied)

### Notes & observations from the verdict step

- **Strict vs inclusive operators matter.** DC gain spec used `>` (exclusive),
  UGB used `>=` (inclusive). NL spec didn't distinguish — I picked them in
  IR §2 by reading "should exceed" as strict and "at least" as inclusive.
  An edge-case run at exactly 60.0 dB would land on the opposite verdicts.
  → Reinforces Gap-G: pass-criterion operator semantics need to be **explicit
  in IR**, not inferred at evaluator runtime.

- **Margins are large** (+6 dB / +22 MHz). This is by construction: I picked
  the DUT sizing in Stage 3 (lambda=0.01, W/L=40 for input pair) specifically
  so the targeted gain ≈ 66 dB sits comfortably above the 60 dB threshold.
  → This means this trace **cannot exercise the "edge-of-spec" behavior** of
  the evaluator (tolerance handling, FP rounding effects). A future bench
  case should be sized deliberately so one or both metrics land within
  ~0.1 dB of the threshold.

- **No tolerance was applied.** The comparison was raw arithmetic. In a real
  pipeline the evaluator may need to express e.g. "pass if gain > 60 dB
  within ±0.5 dB measurement tolerance" — the IR currently has no place
  for tolerance.
  → New gap: **Gap-K: pass_criteria has no tolerance/uncertainty field.**

- **The verdict is correct in the trivial sense** (numbers exceed thresholds)
  but I cannot verify it is correct in the **engineering sense** without
  cross-checking against an independent reference (e.g. hand-derived analytical
  gain prediction). For this example the order-of-magnitude is right
  (gm·ro ≈ 200µA/V × 10MΩ ≈ 2000 ≈ 66 dB — matches simulation), so we have
  weak independent verification. A real benchmark would need a stronger
  ground-truth mechanism.

---

## Summary: what this trace exposed

This running example produced a working ngspice testbench for a 5-tran diff-pair
OTA (gain 66.1 dB, UGB 32 MHz, both PASS), but **the working testbench is not
the deliverable** — the deliverable is the 27-item punch list of gaps and
knowledge debts. The 7 highest-leverage findings, in order of importance for
Step 2 (固化 IR schema):

1. **IR shape must change.** Image-2's flat-record JSON breaks the moment
   you have more than one measurement (DC gain + UGB share one AC analysis).
   The IR needs to split into ~6–8 explicit sections: `dut`, `analyses`,
   `stimulus`, `loading`, `measurements`, `pass_criteria`, `corners`,
   `global_setup`. (Gap-B, §2)

2. **IR alone is not enough.** Emitting a real testbench needs four companion
   inputs that have nowhere to live in IR today: DUT-metadata (file path,
   port signature, port roles), PDKContext (VDD, common-mode, conventions),
   simulator-profile (dialect mapping, default temperature, convergence
   options), and an evaluator transform-table (raw→spec unit conversions).
   Step 2 must name and design all four. (K1–K14 + E1–E2)

3. **The "silent-bug class" is the dominant risk.** Three findings (K7
   directionality loss, E1 missing dB transform, E2 unit mismatch) all
   share the property that the pipeline runs end-to-end without error and
   produces a verdict — just the wrong one. IR primitives must carry
   enough semantic content (operator direction, raw unit, spec unit) that
   the emitter and evaluator cannot silently drop them.

4. **Measurement primitives need a strict, closed vocabulary.** "DC gain"
   and "UGB" are not fields; they are computations. The trace tentatively
   used a free-form expression DSL (`vdb(out) @ f=f_start`,
   `freq_where(... crosses 0 falling)`), but this is brittle. Step 2 should
   choose between (a) a small fixed library of named primitives, or
   (b) a real expression DSL with formal semantics — not the current
   half-DSL. (Gap-F, Gap-G)

5. **Dialect translation is not a 1:1 string substitution.** ngspice 45.2
   rejected the HSPICE-style `.measure ac vdb(...) at=...` form entirely;
   the emitter had to fall back to a `.control` block with native `meas`.
   Different simulators won't just need different keyword maps — they may
   need different *structural* emit strategies. The emitter interface must
   accommodate that. (K12–K14)

6. **PDK-equivalent defaults are first-class.** Eleven design decisions
   (VDD, Vbias, W/L sizes, lambda, KP, Vt) had to be invented during the
   DUT stage and are not in NL, not in IR, and (today) not anywhere else.
   In production these come from a PDKContext + DUT-sizing file. Without
   this layer, no LLM-driven emitter can produce correct numbers — it
   would either hallucinate values or refuse.

7. **Pass-criterion semantics are richer than scalar comparison.** Strict
   vs inclusive operator (`>` vs `>=`), tolerance / uncertainty bands, and
   unit-aware comparison all need explicit IR support (Gap-K added in §7).
   The natural-language spec leaves all of these implicit; the IR must not.

### Inventory of gaps and debts

- **Schema gaps (A–K):** 11 items — feed Step 2 schema design
- **Emitter knowledge debts (K1–K14):** 14 items — feed Step 2 emitter + PDKContext design
- **Evaluator knowledge debts (E1–E2):** 2 items — feed Step 2 evaluator design
- **Total: 27 items** to resolve before the spec → testbench → verdict pipeline can be agent-driven end-to-end.

### What this trace deliberately did NOT exercise

- Multi-corner runs (only TT@27°C)
- Edge-of-spec measurements (margins were large by design)
- Transient / noise / Monte-Carlo analyses (only AC)
- A real PDK (level-1 models only)
- Subckt-internal probing or hierarchical DUTs
- Convergence failures in non-trivial circuits

Each of these is a candidate "second running example" once Step 2's IR redesign
needs to be stress-tested.
