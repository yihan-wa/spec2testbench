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
- analyses:      simulation runs to do, each with a unique `id`
- stimulus:      signal sources, by stimulus kind enum + DUT port roles
- loading:       passive loads with from_role/to_role and SPICE-style values
- measurements:  SCALARS derived from analyses; each picks a primitive from
                 a CLOSED vocabulary (do NOT invent new primitive names)
- pass_criteria: per-measurement verdicts with strict op + spec_unit
- corners:       process / temperature / optional supply_voltage_override

== KEY RULES ==

1. MEASUREMENT PRIMITIVES (closed vocabulary, exactly these strings):

   "ac_low_freq_asymptote"
     Semantics: |H(f)| evaluated at f = f_start of the referenced AC analysis.
     Use for: "DC gain", "low-frequency gain", "open-loop gain at DC".
     Takes NO extra parameters. output_unit is typically "dB".

   "ac_freq_at_magnitude_crossing"
     Semantics: the frequency at which |H(f)| crosses target_magnitude
     in the specified direction during the AC sweep.
     REQUIRES: target_magnitude (LINEAR, not dB) AND direction.
     direction ∈ {"rising", "falling", "any"}.
     Use for:
       - Unity-gain bandwidth / UGB / GBW: target_magnitude=1.0, direction="falling"
       - −3 dB high-frequency corner:       target_magnitude=10**(-3/20) of low-freq gain, direction="falling"

   The schema will reject any primitive name not in this list.

2. STIMULUS KINDS (closed enum):

   "balanced_differential_AC"
     Encodes the convention: ±0.5 × magnitude on the two listed ports with
     phases 0° and 180° (referenced to a common-mode node owned by the
     emitter, not described here). For any AC test of a differential pair
     or fully-differential DUT, prefer this kind.
     `ports` must be [positive_leg_role, negative_leg_role].

   "single_ended_AC"   — magnitude on the one listed port; phase 0.
   "DC_voltage"        — dc_value on the listed port; typical for bias pins.

3. PORT ROLES come from the user message's DUT metadata. Use the exact
   role strings provided; do NOT rename them. Typical conventions:
     - "inp", "inn"        differential inputs
     - "out" / "outp","outn" single-ended / differential outputs
     - "vdd", "vss"        supplies
     - "bias_<something>"  external bias-injection pin (NOT a signal)

4. PASS-CRITERION OPERATOR — choose strictness from NL phrasing:
     "should exceed" / "must be greater than"            → "gt"
     "at least" / "≥"  / "not less than" / "minimum of"  → "ge"
     "below" / "must be less than"                       → "lt"
     "at most" / "≤" / "maximum of"                      → "le"
     "approximately" / "around"                          → "approx_eq" + tolerance

5. UNITS — always declare both:
     measurement.output_unit  = the raw unit the simulator will produce
     pass_criterion.spec_unit = the unit the spec phrased the threshold in
   Examples:
     "DC gain > 60 dB"      → output_unit="dB",  spec_unit="dB"
     "UGB ≥ 10 MHz"         → output_unit="Hz",  spec_unit="MHz"
     "phase margin > 60°"   → output_unit="deg", spec_unit="deg"

6. CORNERS: if the spec says e.g. "TT corner at 27 °C", emit one Corner
   with process="TT", temperature_celsius=27.0. Leave supply_voltage_override
   null unless the spec explicitly mentions a non-default supply voltage.

7. Carry the original NL spec verbatim into meta.nl_spec.

8. If the NL spec phrasing is ambiguous (e.g. "DC gain" without saying
   whether to evaluate at f=DC or at f_start), choose the conservative
   default that matches an existing primitive, and do NOT invent fields.
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
