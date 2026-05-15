"""LLM-driven extractor: NL spec → TestPlan IR.

Per memory `prefer-cross-provider-portability`, this module ships TWO
parallel implementations:

- `extract_with_anthropic(...)` — Anthropic SDK + native tool use + prompt
  caching. Hits api.anthropic.com directly.
- `extract_with_openai_compatible(...)` — OpenAI SDK + function calling +
  configurable `base_url`. Works with OpenAI direct, OpenRouter, Xiaomi
  MiMo, Alibaba DashScope, local vLLM/ollama, or any OpenAI-protocol
  endpoint.

Both paths share:
- The system prompt (`_SYSTEM_PROMPT`) — the only place "how to extract" lives.
- The structured-output schema (`TestPlan.model_json_schema()`).
- Pydantic validation on the model's structured response.

Adding a new provider = adding a new ~30-line `extract_with_<name>` function;
do NOT introduce a unified `Provider` interface or hub.
"""

from __future__ import annotations

import json
from dataclasses import dataclass

from .ir import TestPlan

# ───────────────────────────── shared bits ──────────────────────────────


@dataclass(frozen=True)
class DutMetadata:
    """The DUT context the extractor needs beyond the NL spec.

    In production this would come from a PDKContext + DUT-sizing file pair
    (deferred Gap-I); here it is a small dataclass the caller hand-curates.
    """

    name: str
    netlist_path: str
    subckt_ports: tuple[tuple[str, str], ...]
    """(name, role) pairs in SPICE call order. E.g. (("vinp", "inp"), ...)."""


class ExtractionError(RuntimeError):
    """Raised when an LLM response cannot be turned into a valid TestPlan."""


_TOOL_NAME = "submit_test_plan"
_TOOL_DESCRIPTION = (
    "Submit the structured TestPlan IR derived from the given NL spec and DUT metadata."
)


_SYSTEM_PROMPT = """\
You are an analog IC verification expert. Convert a natural-language test
specification into a strictly-structured JSON intermediate representation
(the TestPlan IR) and submit it via the provided tool. Output NO free text —
only call the tool.

The TestPlan IR has these sections:
- meta:          plan id and the original NL spec text (passed through verbatim)
- dut:           DUT identity, netlist_path, and the ORDERED subckt_ports list
                 (these are given in the user message; copy them faithfully)
- analyses:      simulation runs to do, each with a unique `id` and a `type`
                 from {"AC","TRAN","DC","NOISE"} — picks the right sub-schema
- stimulus:      signal sources, by stimulus kind enum + DUT port roles
- loading:       passive loads with from_role/to_role and SPICE-style values
- measurements:  SCALARS derived from analyses; each picks a primitive from
                 a CLOSED vocabulary (do NOT invent new primitive names)
- pass_criteria: per-measurement verdicts with strict op + spec_unit
- corners:       process / temperature / optional supply_voltage_override

== ANALYSIS TYPES (closed, exactly four) ==

"AC"     — small-signal sweep. Fields: f_start, f_stop, sweep_style (dec/oct/lin),
           points_per_decade.
"TRAN"   — transient. Fields: t_step (printing step, s), t_stop (s), t_start (s),
           uic (bool).
"DC"     — operating point or single-source sweep. Fields: optional
           sweep_source_role, sweep_start, sweep_stop, sweep_step — set ALL
           four for a sweep, NONE for a plain operating-point (.op).
"NOISE"  — noise sweep. Fields: output_role, input_stimulus_id (id of an AC
           stimulus that ngspice treats as the input source), f_start, f_stop,
           sweep_style, points_per_decade.

Anything outside these four (stb, pss, pac, pnoise, hb, qpss, envlp, dcmatch,
acmatch, sp, xf, sens, pz, etc.) is NOT supported in v0 — do NOT emit them.

== KEY RULES ==

1. MEASUREMENT PRIMITIVES (closed vocabulary, exactly these 16 strings):

   --- AC primitives (use only with type="AC" analyses) ---

   "ac_low_freq_asymptote"
     Semantics: |H(f)| evaluated at f = f_start of the referenced AC analysis.
     NL triggers: "DC gain", "low-frequency gain", "open-loop gain at DC".
     Takes NO extra parameters. output_unit is typically "dB".

   "ac_freq_at_magnitude_crossing"
     Semantics: frequency at which |H(f)| crosses target_magnitude in the
     specified direction during the AC sweep.
     REQUIRES: target_magnitude (LINEAR, not dB) AND direction
               ∈ {"rising","falling","any"}.
     NL triggers:
       - Unity-gain bandwidth / UGB / GBW: target_magnitude=1.0, direction="falling"
       - −3 dB high-frequency corner:      target_magnitude=10**(-3/20)·gain_lin,
                                           direction="falling"

   "ac_phase_at_freq"
     Semantics: ∠H(f) at a specified frequency, in degrees.
     REQUIRES: EXACTLY ONE of at_freq (Hz) or at_when_measurement
               (the id of another measurement whose value is a frequency,
                typically a UGB measurement).
     NL triggers: "phase at 1 MHz", "phase response at the unity gain frequency".

   "ac_magnitude_at_freq"
     Semantics: |H(f)| at a specified frequency.
     REQUIRES: at_freq (Hz). output_unit is typically "dB".
     NL triggers: "gain at 1 MHz", "in-band gain at 100 kHz".

   "ac_phase_margin"
     Semantics: phase margin = 180° + ∠H(f@UGB). Always evaluated at the
     UGB frequency of another measurement.
     REQUIRES: at_when_measurement = id of the UGB-style measurement.
     NL triggers: "phase margin", "PM".

   --- TRAN primitives (use only with type="TRAN" analyses) ---

   "tran_slew_rate"
     Semantics: |dV/dt| during an output transition, measured over 10%→90%
     of the step amplitude.
     REQUIRES: edge ∈ {"rising","falling","both"}.
     OPTIONAL: window = [t_low, t_high] (s).
     NL triggers: "slew rate", "压摆率".

   "tran_settling_time"
     Semantics: time from the trigger stimulus edge until the output stays
     within ±tolerance_pct of the final value.
     REQUIRES: tolerance_pct (fraction, e.g. 0.001 for 0.1%), trigger_event
               = {stimulus_id, edge ∈ {"rising","falling","both"}}.
     OPTIONAL: window.
     NL triggers: "settling time to 0.1%", "稳定时间".

   "tran_overshoot_pct"
     Semantics: 100 × (max(V) − V_final) / (V_final − V_initial) over window.
     OPTIONAL: window. output_unit is typically "%".
     NL triggers: "overshoot", "过冲".

   "tran_peak_to_peak"
     Semantics: max(V) − min(V) over window.
     OPTIONAL: window.
     NL triggers: "peak-to-peak output", "output swing" (when transient),
                  "峰峰值".

   "tran_thd"
     Semantics: total harmonic distortion at a fundamental single-tone input.
     REQUIRES: fundamental_freq (Hz).
     OPTIONAL: num_harmonics (default 9), window. output_unit is "%" or "dB".
     NL triggers: "THD", "总谐波失真".

   --- DC primitives (use only with type="DC" analyses) ---

   "dc_offset_input_referred"
     Semantics: input voltage required to steer the output to a target value.
     REQUIRES: target_output_role (DUT role), target_output_value (float in V
               or the string "midrail" for (VDD+VSS)/2).
     output_unit is "V".
     NL triggers: "input-referred offset", "输入失调".

   "dc_output_swing_range"
     Semantics: the requested extreme of the output across a DC sweep.
     REQUIRES: extreme ∈ {"min","max","range"} ("range" = max − min).
     output_unit is "V".
     NL triggers: "output swing", "输出摆幅".

   "dc_supply_current"
     Semantics: current flowing in/out of the named supply rail at the .op
     point (or DC sweep point).
     REQUIRES: supply_role (DUT role, typically "vdd").
     output_unit is "A".
     NL triggers: "quiescent current", "I_DD", "静态电流".

   "dc_gm"
     Semantics: small-signal transconductance d(I_out)/d(V_in) at the
     specified bias point.
     REQUIRES: input_role, output_role, at_bias_value (V).
     output_unit is "S" (Siemens).
     NL triggers: "transconductance", "gm".

   --- NOISE primitives (use only with type="NOISE" analyses) ---

   "noise_input_referred_at_freq"
     Semantics: input-referred noise PSD (V/√Hz) at a specified frequency.
     REQUIRES: at_freq (Hz). output_unit is "V/sqrt(Hz)".
     NL triggers: "input-referred noise at 1 MHz", "1 kHz 处的输入噪声".

   "noise_integrated_rms"
     Semantics: RMS integrated noise over [f_low, f_high], referred to
     input or output side.
     REQUIRES: f_low (Hz), f_high (Hz), referred_to ∈ {"input","output"}.
     output_unit is "V".
     NL triggers: "integrated input-referred noise", "积分噪声 RMS".

   The schema will reject any primitive name not in this list, any
   primitive used with the wrong analysis type, and any primitive that is
   missing its required fields or sets fields it does not own.

2. STIMULUS KINDS (closed enum):

   "balanced_differential_AC"
     ±0.5·magnitude on the two listed ports, phases 0°/180°. For AC tests of
     a differential pair or fully-differential DUT. ports = [positive_leg_role,
     negative_leg_role]. REQUIRES magnitude.

   "single_ended_AC"     — magnitude on the one listed port; phase 0.
                           REQUIRES magnitude. Use as the input source for
                           NoiseAnalysis (set noise.input_stimulus_id to this).

   "DC_voltage"          — dc_value on the listed port; typical for bias pins.
                           REQUIRES dc_value.

   "tran_pulse"          — SPICE PULSE(v1 v2 td tr tf pw per).
                           Carries a nested `pulse` object with fields v1, v2,
                           td (default 0), tr (default 1e-9), tf (default 1e-9),
                           pw, per. Use for step-response inputs feeding
                           slew_rate / settling_time / overshoot.

   "tran_sine"           — SPICE SIN(offset amp freq).
                           Carries a nested `sine` object with dc_offset
                           (default 0), amplitude, freq. Use as the fundamental
                           tone for tran_thd.

   "tran_step"           — one-shot V1→V2 step at time t_step. Carries a
                           nested `step` object with v1, v2, t_step (default 0),
                           tr (default 1e-12). Lighter than tran_pulse when only
                           one edge is needed.

   "dc_sweep_source"     — declares this DUT-port stimulus is the source being
                           swept by a DcAnalysis. Set NO inline parameters; the
                           DcAnalysis carries sweep_start/stop/step.

3. PORT ROLES come from the user message's DUT metadata. Use the exact
   role strings provided; do NOT rename them. Typical conventions:
     - "inp", "inn"        differential inputs
     - "out" / "outp","outn" single-ended / differential outputs
     - "vdd", "vss"        supplies
     - "bias_<something>"  external bias-injection pin (NOT a signal)

4. PASS-CRITERION OPERATOR — choose strictness from NL phrasing:
     "should exceed" / "must be greater than" / "shall exceed"  → "gt"
     "at least" / "≥" / "not less than" / "minimum of"          → "ge"
     "below" / "must be less than" / "shall be less than"        → "lt"
     "at most" / "≤" / "maximum of" / "no more than"             → "le"
     "approximately" / "around" / "≈"                            → "approx_eq" + tolerance

5. UNITS — always declare both:
     measurement.output_unit  = the raw unit the simulator will produce
     pass_criterion.spec_unit = the unit the spec phrased the threshold in
   Examples:
     "DC gain > 60 dB"               → output_unit="dB",          spec_unit="dB"
     "UGB ≥ 10 MHz"                  → output_unit="Hz",          spec_unit="MHz"
     "phase margin > 60°"            → output_unit="deg",         spec_unit="deg"
     "slew rate > 10 V/μs"           → output_unit="V/s",         spec_unit="V/us"
     "settling time < 100 ns"        → output_unit="s",           spec_unit="ns"
     "input offset < 5 mV"           → output_unit="V",           spec_unit="mV"
     "Iq < 100 μA"                   → output_unit="A",           spec_unit="uA"
     "noise at 1 MHz < 10 nV/√Hz"    → output_unit="V/sqrt(Hz)", spec_unit="nV/sqrt(Hz)"

6. CORNERS: if the spec says e.g. "TT corner at 27 °C", emit one Corner
   with process="TT", temperature_celsius=27.0. Leave supply_voltage_override
   null unless the spec explicitly mentions a non-default supply voltage.

7. Carry the original NL spec verbatim into meta.nl_spec.

8. If the NL spec phrasing is ambiguous (e.g. "DC gain" without saying
   whether to evaluate at f=DC or at f_start), choose the conservative
   default that matches an existing primitive, and do NOT invent fields.

9. ANALYSIS/PRIMITIVE PAIRING is enforced by the schema:
     - AC primitives need an AC analysis in `from_analysis`.
     - TRAN primitives need a TRAN analysis.
     - DC primitives need a DC analysis.
     - NOISE primitives need a NOISE analysis.
   If a spec needs (e.g.) both DC gain and slew rate, emit TWO analyses
   (one AC, one TRAN) and reference the right one from each measurement.

10. When a settling-time / slew-rate measurement needs a stimulus to trigger
    on, emit the driving stimulus (tran_pulse or tran_step) AND set the
    measurement's trigger_event = {stimulus_id, edge}. Set the stimulus's
    scope to "analysis" and scope_analysis_id to that TRAN analysis so it
    is not active during other (e.g. AC) analyses in the same plan.
"""


def _build_user_message(nl_spec: str, dut: DutMetadata, plan_id: str) -> str:
    port_lines = "\n".join(
        f"  {i + 1}. name={name}, role={role}"
        for i, (name, role) in enumerate(dut.subckt_ports)
    )
    return (
        f"DUT metadata:\n"
        f"- name: {dut.name}\n"
        f"- netlist_path: {dut.netlist_path}\n"
        f"- subckt_ports (in SPICE call order):\n{port_lines}\n"
        f"\n"
        f"Plan id: {plan_id}\n"
        f"\n"
        f"NL spec to extract:\n"
        f"---\n{nl_spec.strip()}\n---"
    )


def _tool_input_schema() -> dict:
    """Single source of truth for the JSON schema fed to either provider."""
    return TestPlan.model_json_schema()


# ───────────────────────────── Anthropic path ──────────────────────────────


def extract_with_anthropic(
    nl_spec: str,
    dut: DutMetadata,
    *,
    plan_id: str,
    api_key: str | None = None,
    model: str = "claude-sonnet-4-6",
    max_tokens: int = 4096,
) -> TestPlan:
    """Extract IR using Anthropic's native API (api.anthropic.com).

    Reads ANTHROPIC_API_KEY from env if `api_key` is None. Adds `cache_control`
    on the system block so repeated calls (same system prompt) get a cache hit.
    """
    from anthropic import Anthropic

    client = Anthropic(api_key=api_key)

    resp = client.messages.create(
        model=model,
        max_tokens=max_tokens,
        system=[
            {
                "type": "text",
                "text": _SYSTEM_PROMPT,
                "cache_control": {"type": "ephemeral"},
            }
        ],
        tools=[
            {
                "name": _TOOL_NAME,
                "description": _TOOL_DESCRIPTION,
                "input_schema": _tool_input_schema(),
            }
        ],
        tool_choice={"type": "tool", "name": _TOOL_NAME},
        messages=[
            {
                "role": "user",
                "content": _build_user_message(nl_spec, dut, plan_id),
            }
        ],
    )

    tool_use = next((b for b in resp.content if b.type == "tool_use"), None)
    if tool_use is None:
        raise ExtractionError(
            f"Anthropic response did not include a tool_use block. "
            f"stop_reason={resp.stop_reason!r}, content={resp.content!r}"
        )

    return TestPlan.model_validate(tool_use.input)


# ────────────────────── OpenAI-compatible path ────────────────────────────


def extract_with_openai_compatible(
    nl_spec: str,
    dut: DutMetadata,
    *,
    plan_id: str,
    api_key: str,
    base_url: str,
    model: str,
    max_tokens: int = 4096,
) -> TestPlan:
    """Extract IR using any OpenAI-compatible endpoint.

    Works with — given the right (api_key, base_url, model) triple:

      OpenAI direct:
        api_key=sk-...,    base_url="https://api.openai.com/v1",
        model="gpt-4o-mini" or similar
      OpenRouter:
        api_key=sk-or-..., base_url="https://openrouter.ai/api/v1",
        model="anthropic/claude-sonnet-4.6" (or any model OpenRouter exposes)
      Xiaomi MiMo:
        api_key=...,       base_url="<xiaomi-endpoint>/v1",
        model="<xiaomi-model-id>"
      Local vLLM / ollama / etc.:
        api_key="EMPTY",   base_url="http://localhost:8000/v1",
        model="<served-model-name>"

    Uses function calling (universal OpenAI-compatible feature). Does not
    rely on `response_format=json_schema` strict mode because that has
    extra schema requirements not honoured by every third-party endpoint.
    """
    from openai import OpenAI

    client = OpenAI(api_key=api_key, base_url=base_url)

    resp = client.chat.completions.create(
        model=model,
        max_tokens=max_tokens,
        tools=[
            {
                "type": "function",
                "function": {
                    "name": _TOOL_NAME,
                    "description": _TOOL_DESCRIPTION,
                    "parameters": _tool_input_schema(),
                },
            }
        ],
        tool_choice={"type": "function", "function": {"name": _TOOL_NAME}},
        messages=[
            {"role": "system", "content": _SYSTEM_PROMPT},
            {"role": "user", "content": _build_user_message(nl_spec, dut, plan_id)},
        ],
    )

    msg = resp.choices[0].message
    if not msg.tool_calls:
        raise ExtractionError(
            f"OpenAI-compatible response had no tool_calls. "
            f"finish_reason={resp.choices[0].finish_reason!r}, "
            f"content={msg.content!r}"
        )

    try:
        args = json.loads(msg.tool_calls[0].function.arguments)
    except json.JSONDecodeError as e:
        raise ExtractionError(
            f"Could not parse tool_call.function.arguments as JSON: {e}; "
            f"raw={msg.tool_calls[0].function.arguments!r}"
        ) from e

    return TestPlan.model_validate(args)
