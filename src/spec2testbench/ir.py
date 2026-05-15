"""TestPlan IR — the structured intermediate between NL spec and emitted netlist.

Originally designed to resolve the 11 schema gaps (A–K) surfaced by the manual
end-to-end walkthrough in ``examples/01_diff_pair_ota/trace.md``. Extended on
2026-05-15 to cover four analysis types (AC, TRAN, DC, NOISE) and 16 measurement
primitives so the IR can express the bulk of textbook analog testbench specs.

Gaps not addressed here are deferred deliberately:

- Gap I (VDD / ibias / Vin_common_mode): deferred to a future ``PDKContext``
  data structure; intentionally NOT representable in the IR.
- Emitter knowledge debts K1–K14: out of scope for Step 2 (Step 5).
- Evaluator transform table E1–E2: out of scope for Step 2 (Step 4).

The IR is intentionally split into seven top-level sections — a single flat
JSON record collapses as soon as one analysis feeds multiple measurements.

Analysis-type coverage (v0 envelope):
    AC, TRAN, DC, NOISE — all four runnable in ngspice.
Spectre / Spectre-RF analyses (stb, pss, hb, qpss, envlp, dcmatch, acmatch,
sp, xf, sens, pz, pstb, psp, pnoise, pxf, pac, qpac, qpnoise, qpxf, qpsp, hbac,
hbnoise, hbsp) are deliberately NOT enum members — adding them would create
the illusion of capability the ngspice-default emitter cannot back.
"""

from __future__ import annotations

import json
from enum import Enum
from typing import Annotated, Any, ClassVar, Literal, Union

from pydantic import BaseModel, ConfigDict, Field, model_validator


# ───────────────────────────── enums ──────────────────────────────


class SweepStyle(str, Enum):
    """Frequency-sweep spacing for AC / NOISE. Gap C."""

    DEC = "dec"
    OCT = "oct"
    LIN = "lin"


class AnalysisType(str, Enum):
    """Closed vocabulary of analysis kinds. v0 envelope = ngspice-runnable."""

    AC = "AC"
    TRAN = "TRAN"
    DC = "DC"
    NOISE = "NOISE"


class StimulusKind(str, Enum):
    """Closed vocabulary of stimulus shapes. Gap D.

    Each value encodes a complete engineering convention (e.g.,
    ``BALANCED_DIFFERENTIAL_AC`` means ±0.5·magnitude on the two input ports,
    phases 0° / 180°, referenced to a common-mode node owned by PDKContext).
    Emitter has a 1:1 translation per value.
    """

    BALANCED_DIFFERENTIAL_AC = "balanced_differential_AC"
    SINGLE_ENDED_AC = "single_ended_AC"
    DC_VOLTAGE = "DC_voltage"
    TRAN_PULSE = "tran_pulse"
    TRAN_SINE = "tran_sine"
    TRAN_STEP = "tran_step"
    DC_SWEEP_SOURCE = "dc_sweep_source"


class LoadingKind(str, Enum):
    C = "C"
    R = "R"
    L = "L"


class CrossingDirection(str, Enum):
    """Direction qualifier for crossing-style measurements. Gap G."""

    RISING = "rising"
    FALLING = "falling"
    ANY = "any"


class TransitionEdge(str, Enum):
    """Edge selector for TRAN measurements (slew-rate, settling-trigger)."""

    RISING = "rising"
    FALLING = "falling"
    BOTH = "both"


class NoiseReferenceSide(str, Enum):
    """Whether a noise measurement is referred to input or output side."""

    INPUT = "input"
    OUTPUT = "output"


class SwingExtreme(str, Enum):
    """Which end of a DC-sweep output range a measurement extracts."""

    MIN = "min"
    MAX = "max"
    RANGE = "range"  # numerical max − min


class MeasurementPrimitive(str, Enum):
    """Closed library of measurement primitives. Gap F + G.

    Coverage targets the four v0 analysis types (AC / TRAN / DC / NOISE).
    Each primitive has a fixed required-field signature enforced by the
    Measurement model validator.
    """

    # AC (5)
    AC_LOW_FREQ_ASYMPTOTE = "ac_low_freq_asymptote"
    AC_FREQ_AT_MAGNITUDE_CROSSING = "ac_freq_at_magnitude_crossing"
    AC_PHASE_AT_FREQ = "ac_phase_at_freq"
    AC_MAGNITUDE_AT_FREQ = "ac_magnitude_at_freq"
    AC_PHASE_MARGIN = "ac_phase_margin"

    # TRAN (5)
    TRAN_SLEW_RATE = "tran_slew_rate"
    TRAN_SETTLING_TIME = "tran_settling_time"
    TRAN_OVERSHOOT_PCT = "tran_overshoot_pct"
    TRAN_PEAK_TO_PEAK = "tran_peak_to_peak"
    TRAN_THD = "tran_thd"

    # DC (4)
    DC_OFFSET_INPUT_REFERRED = "dc_offset_input_referred"
    DC_OUTPUT_SWING_RANGE = "dc_output_swing_range"
    DC_SUPPLY_CURRENT = "dc_supply_current"
    DC_GM = "dc_gm"

    # NOISE (2)
    NOISE_INPUT_REFERRED_AT_FREQ = "noise_input_referred_at_freq"
    NOISE_INTEGRATED_RMS = "noise_integrated_rms"


class ComparisonOp(str, Enum):
    """Strict operator vocabulary for pass criteria. Gap K."""

    GT = "gt"
    GE = "ge"
    LT = "lt"
    LE = "le"
    EQ = "eq"
    APPROX_EQ = "approx_eq"


class Scope(str, Enum):
    """Whether a stimulus/loading applies across the whole plan or one analysis. Gap E."""

    PLAN = "plan"
    ANALYSIS = "analysis"


# ───────────────── primitive → analysis-type mapping ─────────────────

PRIMITIVE_TO_ANALYSIS_TYPE: dict[MeasurementPrimitive, AnalysisType] = {
    MeasurementPrimitive.AC_LOW_FREQ_ASYMPTOTE: AnalysisType.AC,
    MeasurementPrimitive.AC_FREQ_AT_MAGNITUDE_CROSSING: AnalysisType.AC,
    MeasurementPrimitive.AC_PHASE_AT_FREQ: AnalysisType.AC,
    MeasurementPrimitive.AC_MAGNITUDE_AT_FREQ: AnalysisType.AC,
    MeasurementPrimitive.AC_PHASE_MARGIN: AnalysisType.AC,
    MeasurementPrimitive.TRAN_SLEW_RATE: AnalysisType.TRAN,
    MeasurementPrimitive.TRAN_SETTLING_TIME: AnalysisType.TRAN,
    MeasurementPrimitive.TRAN_OVERSHOOT_PCT: AnalysisType.TRAN,
    MeasurementPrimitive.TRAN_PEAK_TO_PEAK: AnalysisType.TRAN,
    MeasurementPrimitive.TRAN_THD: AnalysisType.TRAN,
    MeasurementPrimitive.DC_OFFSET_INPUT_REFERRED: AnalysisType.DC,
    MeasurementPrimitive.DC_OUTPUT_SWING_RANGE: AnalysisType.DC,
    MeasurementPrimitive.DC_SUPPLY_CURRENT: AnalysisType.DC,
    MeasurementPrimitive.DC_GM: AnalysisType.DC,
    MeasurementPrimitive.NOISE_INPUT_REFERRED_AT_FREQ: AnalysisType.NOISE,
    MeasurementPrimitive.NOISE_INTEGRATED_RMS: AnalysisType.NOISE,
}


# All Measurement fields that are *only* meaningful for specific primitives.
# Anything in this set must be None unless explicitly allowed for the
# primitive being used.
_PRIMITIVE_SPECIFIC_FIELDS: frozenset[str] = frozenset({
    "target_magnitude", "direction",
    "at_freq", "at_when_measurement",
    "window", "edge", "tolerance_pct", "trigger_event",
    "fundamental_freq", "num_harmonics",
    "target_output_role", "target_output_value", "supply_role",
    "input_role", "output_role", "at_bias_value", "extreme",
    "f_low", "f_high", "referred_to",
})

# Per-primitive (required_fields, allowed_optional_fields).
# Note: AC_PHASE_AT_FREQ is special-cased below — it requires exactly one of
# {at_freq, at_when_measurement}, not both.
_PRIMITIVE_PARAM_SPEC: dict[MeasurementPrimitive, tuple[set[str], set[str]]] = {
    MeasurementPrimitive.AC_LOW_FREQ_ASYMPTOTE: (set(), set()),
    MeasurementPrimitive.AC_FREQ_AT_MAGNITUDE_CROSSING:
        ({"target_magnitude", "direction"}, set()),
    MeasurementPrimitive.AC_PHASE_AT_FREQ:
        (set(), {"at_freq", "at_when_measurement"}),
    MeasurementPrimitive.AC_MAGNITUDE_AT_FREQ: ({"at_freq"}, set()),
    MeasurementPrimitive.AC_PHASE_MARGIN: ({"at_when_measurement"}, set()),
    MeasurementPrimitive.TRAN_SLEW_RATE: ({"edge"}, {"window"}),
    MeasurementPrimitive.TRAN_SETTLING_TIME:
        ({"tolerance_pct", "trigger_event"}, {"window"}),
    MeasurementPrimitive.TRAN_OVERSHOOT_PCT: (set(), {"window"}),
    MeasurementPrimitive.TRAN_PEAK_TO_PEAK: (set(), {"window"}),
    MeasurementPrimitive.TRAN_THD:
        ({"fundamental_freq"}, {"num_harmonics", "window"}),
    MeasurementPrimitive.DC_OFFSET_INPUT_REFERRED:
        ({"target_output_role", "target_output_value"}, set()),
    MeasurementPrimitive.DC_OUTPUT_SWING_RANGE: ({"extreme"}, set()),
    MeasurementPrimitive.DC_SUPPLY_CURRENT: ({"supply_role"}, set()),
    MeasurementPrimitive.DC_GM:
        ({"input_role", "output_role", "at_bias_value"}, set()),
    MeasurementPrimitive.NOISE_INPUT_REFERRED_AT_FREQ: ({"at_freq"}, set()),
    MeasurementPrimitive.NOISE_INTEGRATED_RMS:
        ({"f_low", "f_high", "referred_to"}, set()),
}


# ───────────────────────────── pieces ─────────────────────────────


class Meta(BaseModel):
    """Identification + traceability."""

    model_config = ConfigDict(extra="forbid")

    schema_version: Literal[1] = 1
    id: str = Field(..., description="Stable identifier for this TestPlan.")
    nl_spec: str | None = Field(
        default=None,
        description="Original natural-language spec text, preserved for traceability "
        "and as a label for future extractor training.",
    )


class SubcktPort(BaseModel):
    """One pin of the DUT subckt. Gap A + J.

    ``name`` is the SPICE net name (what the emitter writes). ``role`` is the
    semantic key the rest of the IR refers to (e.g. ``inp``, ``out``, ``vdd``,
    ``bias_tail``). Order of ``Dut.subckt_ports`` is the SPICE call order —
    do not reorder without changing call sites.
    """

    model_config = ConfigDict(extra="forbid")

    name: str
    role: str


class Dut(BaseModel):
    """Reference to the device under test. Gap A + J."""

    model_config = ConfigDict(extra="forbid")

    name: str
    netlist_path: str | None = Field(
        default=None,
        description="Relative path to the DUT .cir file. Will be emitted as `.include`.",
    )
    subckt_ports: list[SubcktPort] = Field(
        ...,
        description="Ordered subckt port list; SPICE instantiation goes in this order.",
        min_length=1,
    )

    @model_validator(mode="after")
    def _roles_unique(self) -> Dut:
        roles = [p.role for p in self.subckt_ports]
        if len(set(roles)) != len(roles):
            dupes = {r for r in roles if roles.count(r) > 1}
            raise ValueError(f"Duplicate port roles in DUT: {sorted(dupes)}")
        return self

    def port_by_role(self, role: str) -> SubcktPort:
        for p in self.subckt_ports:
            if p.role == role:
                return p
        raise KeyError(f"No port with role {role!r} in DUT {self.name!r}")

    @property
    def roles(self) -> set[str]:
        return {p.role for p in self.subckt_ports}


# ───────────────────────── analysis models ─────────────────────────


class AcAnalysis(BaseModel):
    """An AC small-signal sweep. Gap B (via id) + Gap C (explicit sweep params)."""

    model_config = ConfigDict(extra="forbid")

    id: str
    type: Literal[AnalysisType.AC] = AnalysisType.AC
    sweep_style: SweepStyle = SweepStyle.DEC
    points_per_decade: int = Field(default=20, gt=0)
    f_start: float = Field(..., gt=0, description="Hz")
    f_stop: float = Field(..., gt=0, description="Hz")

    @model_validator(mode="after")
    def _stop_above_start(self) -> AcAnalysis:
        if self.f_stop <= self.f_start:
            raise ValueError(f"f_stop ({self.f_stop}) must exceed f_start ({self.f_start})")
        return self


class TranAnalysis(BaseModel):
    """A transient run. Maps to ngspice ``.tran t_step t_stop t_start uic?``."""

    model_config = ConfigDict(extra="forbid")

    id: str
    type: Literal[AnalysisType.TRAN] = AnalysisType.TRAN
    t_step: float = Field(..., gt=0, description="Printing/integration step (s).")
    t_stop: float = Field(..., gt=0, description="Simulation end time (s).")
    t_start: float = Field(default=0.0, ge=0, description="Output start time (s).")
    uic: bool = Field(
        default=False,
        description="If true, ngspice skips DC bias point and uses .ic values.",
    )

    @model_validator(mode="after")
    def _stop_above_start(self) -> TranAnalysis:
        if self.t_stop <= self.t_start:
            raise ValueError(f"t_stop ({self.t_stop}) must exceed t_start ({self.t_start})")
        return self


class DcAnalysis(BaseModel):
    """A DC operating-point or single-source sweep.

    If ``sweep_source_role`` is None, this is a bare operating-point analysis
    (ngspice ``.op``). Otherwise it is a single-source sweep
    (ngspice ``.dc <Vsrc> start stop step``).
    """

    model_config = ConfigDict(extra="forbid")

    id: str
    type: Literal[AnalysisType.DC] = AnalysisType.DC
    sweep_source_role: str | None = Field(
        default=None,
        description="DUT port role whose driving source is swept. None ⇒ .op only.",
    )
    sweep_start: float | None = None
    sweep_stop: float | None = None
    sweep_step: float | None = Field(default=None, description="May be negative for descending sweep.")

    @model_validator(mode="after")
    def _sweep_consistency(self) -> DcAnalysis:
        sweep_fields = (self.sweep_source_role, self.sweep_start, self.sweep_stop, self.sweep_step)
        n_set = sum(1 for x in sweep_fields if x is not None)
        if n_set not in (0, 4):
            raise ValueError(
                "DcAnalysis sweep fields must be all set together (sweep) "
                "or all None (operating point); got partial set"
            )
        if n_set == 4:
            if self.sweep_step == 0:
                raise ValueError("DcAnalysis.sweep_step must be non-zero")
            if (self.sweep_stop - self.sweep_start) * self.sweep_step <= 0:
                raise ValueError(
                    f"DcAnalysis.sweep_step sign ({self.sweep_step}) inconsistent "
                    f"with sweep direction (start={self.sweep_start}, stop={self.sweep_stop})"
                )
        return self


class NoiseAnalysis(BaseModel):
    """A NOISE analysis. Maps to ngspice ``.noise v(<out>) <input_src> dec ...``."""

    model_config = ConfigDict(extra="forbid")

    id: str
    type: Literal[AnalysisType.NOISE] = AnalysisType.NOISE
    output_role: str = Field(..., description="DUT port role where output noise is summed.")
    input_stimulus_id: str = Field(
        ...,
        description="Stimulus.id that ngspice treats as the input source for "
        "input-referred noise. Must be an AC stimulus.",
    )
    sweep_style: SweepStyle = SweepStyle.DEC
    points_per_decade: int = Field(default=20, gt=0)
    f_start: float = Field(..., gt=0)
    f_stop: float = Field(..., gt=0)

    @model_validator(mode="after")
    def _stop_above_start(self) -> NoiseAnalysis:
        if self.f_stop <= self.f_start:
            raise ValueError(f"f_stop ({self.f_stop}) must exceed f_start ({self.f_start})")
        return self


# Discriminated union — pydantic v2 selects the right model from the `type` tag.
Analysis = Annotated[
    Union[AcAnalysis, TranAnalysis, DcAnalysis, NoiseAnalysis],
    Field(discriminator="type"),
]


# ───────────────── stimulus parameter sub-models ─────────────────


class TranPulseParams(BaseModel):
    """SPICE PULSE source parameters: PULSE(v1 v2 td tr tf pw per)."""

    model_config = ConfigDict(extra="forbid")

    v1: float = Field(..., description="Initial value.")
    v2: float = Field(..., description="Pulsed value.")
    td: float = Field(default=0.0, ge=0, description="Delay before first edge (s).")
    tr: float = Field(default=1e-9, gt=0, description="Rise time (s).")
    tf: float = Field(default=1e-9, gt=0, description="Fall time (s).")
    pw: float = Field(..., gt=0, description="Pulse width (s).")
    per: float = Field(..., gt=0, description="Period (s); set ≫ t_stop for one-shot.")


class TranSineParams(BaseModel):
    """SPICE SIN source parameters: SIN(offset amp freq td theta phase)."""

    model_config = ConfigDict(extra="forbid")

    dc_offset: float = Field(default=0.0, description="DC offset (V).")
    amplitude: float = Field(..., gt=0, description="Sinusoidal amplitude (V).")
    freq: float = Field(..., gt=0, description="Frequency (Hz).")


class TranStepParams(BaseModel):
    """A one-shot V1→V2 step. Lighter wrapper than PULSE for settling/overshoot."""

    model_config = ConfigDict(extra="forbid")

    v1: float = Field(..., description="Pre-step value.")
    v2: float = Field(..., description="Post-step value.")
    t_step: float = Field(default=0.0, ge=0, description="Time at which step occurs (s).")
    tr: float = Field(
        default=1e-12, gt=0,
        description="Edge rise/fall time for numerical conditioning (s).",
    )


class Stimulus(BaseModel):
    """A signal-source description referencing DUT port roles. Gap D.

    Per-kind required parameters live on the sub-model fields ``pulse``,
    ``sine``, ``step``. AC-kind stimuli use ``magnitude``; DC-kind use
    ``dc_value``; ``DC_SWEEP_SOURCE`` carries no params here (the sweep
    range lives on the owning DcAnalysis).
    """

    model_config = ConfigDict(extra="forbid")

    id: str
    kind: StimulusKind
    ports: list[str] = Field(
        ...,
        description="DUT port roles this stimulus drives. For "
        "BALANCED_DIFFERENTIAL_AC the order is [positive_leg, negative_leg].",
        min_length=1,
    )
    magnitude: float | None = Field(
        default=None,
        description="For AC stimuli this is the differential (or single-ended) magnitude. "
        "The emitter is responsible for splitting it ±0.5 for differential.",
    )
    dc_value: float | None = Field(default=None, description="For DC_voltage stimuli.")
    pulse: TranPulseParams | None = Field(default=None, description="For TRAN_PULSE.")
    sine: TranSineParams | None = Field(default=None, description="For TRAN_SINE.")
    step: TranStepParams | None = Field(default=None, description="For TRAN_STEP.")
    scope: Scope = Scope.PLAN
    scope_analysis_id: str | None = Field(
        default=None,
        description="Required when scope == ANALYSIS; references Analysis.id.",
    )

    @model_validator(mode="after")
    def _scope_consistency(self) -> Stimulus:
        if self.scope is Scope.ANALYSIS and self.scope_analysis_id is None:
            raise ValueError("scope=ANALYSIS requires scope_analysis_id to be set")
        if self.scope is Scope.PLAN and self.scope_analysis_id is not None:
            raise ValueError("scope=PLAN must not set scope_analysis_id")
        return self

    @model_validator(mode="after")
    def _kind_params(self) -> Stimulus:
        """Enforce that each kind sets exactly its expected params and no others."""
        ac_kinds = {StimulusKind.BALANCED_DIFFERENTIAL_AC, StimulusKind.SINGLE_ENDED_AC}
        k = self.kind

        # Which optional fields *may* be set for this kind.
        spec: dict[StimulusKind, set[str]] = {
            StimulusKind.BALANCED_DIFFERENTIAL_AC: {"magnitude"},
            StimulusKind.SINGLE_ENDED_AC: {"magnitude"},
            StimulusKind.DC_VOLTAGE: {"dc_value"},
            StimulusKind.TRAN_PULSE: {"pulse"},
            StimulusKind.TRAN_SINE: {"sine"},
            StimulusKind.TRAN_STEP: {"step"},
            StimulusKind.DC_SWEEP_SOURCE: set(),
        }
        all_fields = {"magnitude", "dc_value", "pulse", "sine", "step"}
        allowed = spec[k]

        for f in all_fields - allowed:
            if getattr(self, f) is not None:
                raise ValueError(
                    f"Stimulus {self.id!r} (kind={k.value}) must not set {f!r}"
                )
        for f in allowed:
            if getattr(self, f) is None:
                raise ValueError(
                    f"Stimulus {self.id!r} (kind={k.value}) must set {f!r}"
                )

        # AC kinds: balanced needs 2 ports, single-ended needs 1.
        if k is StimulusKind.BALANCED_DIFFERENTIAL_AC and len(self.ports) != 2:
            raise ValueError(
                f"Stimulus {self.id!r} (BALANCED_DIFFERENTIAL_AC) requires exactly "
                f"2 ports [positive_leg, negative_leg]; got {self.ports}"
            )
        if k in ac_kinds - {StimulusKind.BALANCED_DIFFERENTIAL_AC}:
            if len(self.ports) != 1:
                raise ValueError(
                    f"Stimulus {self.id!r} (kind={k.value}) requires exactly 1 port; "
                    f"got {self.ports}"
                )
        return self


class Loading(BaseModel):
    """A passive load on a DUT port. Gap E (explicit scope)."""

    model_config = ConfigDict(extra="forbid")

    id: str
    kind: LoadingKind
    value: str = Field(..., description='SPICE-style value, e.g. "1p", "10k", "100u".')
    from_role: str = Field(..., description="DUT port role at one terminal.")
    to_role: str = Field(default="vss", description="Port role at the other terminal; default ground.")
    scope: Scope = Scope.PLAN
    scope_analysis_id: str | None = None

    @model_validator(mode="after")
    def _scope_consistency(self) -> Loading:
        if self.scope is Scope.ANALYSIS and self.scope_analysis_id is None:
            raise ValueError("scope=ANALYSIS requires scope_analysis_id to be set")
        if self.scope is Scope.PLAN and self.scope_analysis_id is not None:
            raise ValueError("scope=PLAN must not set scope_analysis_id")
        return self


class TriggerEvent(BaseModel):
    """Reference to an edge of a stimulus — used by tran_settling_time."""

    model_config = ConfigDict(extra="forbid")

    stimulus_id: str
    edge: TransitionEdge


class Measurement(BaseModel):
    """A named scalar derived from an analysis run. Gap F + G.

    Most fields are primitive-specific and optional at the schema level. The
    ``_primitive_params`` validator enforces which fields are required vs
    disallowed per primitive. See ``_PRIMITIVE_PARAM_SPEC``.
    """

    model_config = ConfigDict(extra="forbid")

    id: str
    from_analysis: str = Field(..., description="Analysis.id this measurement reads from.")
    primitive: MeasurementPrimitive
    on_role: str = Field(
        default="out",
        description="DUT port role to probe. Default 'out' for typical single-output testbenches.",
    )
    output_unit: str = Field(
        ...,
        description="Unit of the raw value the simulator produces "
        '(e.g. "dB", "Hz", "V", "A", "s", "%"). Drives evaluator unit reconciliation.',
    )

    # ── AC primitive params ──
    target_magnitude: float | None = Field(
        default=None,
        description="For AC_FREQ_AT_MAGNITUDE_CROSSING: the |H| value to find (linear).",
    )
    direction: CrossingDirection | None = Field(
        default=None,
        description="For AC_FREQ_AT_MAGNITUDE_CROSSING: crossing direction. Gap G.",
    )
    at_freq: float | None = Field(
        default=None,
        description="Frequency (Hz) at which AC magnitude/phase or noise PSD is sampled.",
    )
    at_when_measurement: str | None = Field(
        default=None,
        description="ID of another Measurement whose value (a frequency) sets the eval "
        "point. Used by ac_phase_at_freq and ac_phase_margin (typically pointing at UGB).",
    )

    # ── TRAN primitive params ──
    window: tuple[float, float] | None = Field(
        default=None,
        description="(t_low, t_high) window in seconds for the measurement; "
        "None = entire TRAN run.",
    )
    edge: TransitionEdge | None = Field(
        default=None,
        description="For tran_slew_rate: which edge polarity to characterize.",
    )
    tolerance_pct: float | None = Field(
        default=None,
        description="For tran_settling_time: ± band as a fraction (0.001 = 0.1%).",
    )
    trigger_event: TriggerEvent | None = Field(
        default=None,
        description="For tran_settling_time: which stimulus edge starts the settling clock.",
    )
    fundamental_freq: float | None = Field(
        default=None,
        description="For tran_thd: input single-tone frequency (Hz).",
    )
    num_harmonics: int | None = Field(
        default=None, gt=0,
        description="For tran_thd: number of harmonics summed (default 9 if omitted).",
    )

    # ── DC primitive params ──
    target_output_role: str | None = Field(
        default=None,
        description="For dc_offset_input_referred: output port the input is steered toward.",
    )
    target_output_value: float | Literal["midrail"] | None = Field(
        default=None,
        description="For dc_offset_input_referred: target output voltage; "
        '"midrail" = (VDD+VSS)/2.',
    )
    supply_role: str | None = Field(
        default=None,
        description="For dc_supply_current: which supply rail role to probe (e.g. 'vdd').",
    )
    input_role: str | None = Field(
        default=None,
        description="For dc_gm: DUT input port role.",
    )
    output_role: str | None = Field(
        default=None,
        description="For dc_gm: DUT output port role.",
    )
    at_bias_value: float | None = Field(
        default=None,
        description="For dc_gm: input bias point (V) at which to evaluate gm.",
    )
    extreme: SwingExtreme | None = Field(
        default=None,
        description="For dc_output_swing_range: which end of the sweep range to extract.",
    )

    # ── NOISE primitive params ──
    f_low: float | None = Field(
        default=None, gt=0,
        description="For noise_integrated_rms: lower integration limit (Hz).",
    )
    f_high: float | None = Field(
        default=None, gt=0,
        description="For noise_integrated_rms: upper integration limit (Hz).",
    )
    referred_to: NoiseReferenceSide | None = Field(
        default=None,
        description="For noise_integrated_rms: input- vs output-referred summation.",
    )

    @model_validator(mode="after")
    def _primitive_params(self) -> Measurement:
        required, optional = _PRIMITIVE_PARAM_SPEC[self.primitive]
        allowed = required | optional

        if self.primitive is MeasurementPrimitive.AC_PHASE_AT_FREQ:
            # exactly-one-of constraint
            has_freq = self.at_freq is not None
            has_ref = self.at_when_measurement is not None
            if has_freq == has_ref:
                raise ValueError(
                    f"Measurement {self.id!r} (ac_phase_at_freq) requires exactly one "
                    f"of at_freq or at_when_measurement; got at_freq={self.at_freq!r}, "
                    f"at_when_measurement={self.at_when_measurement!r}"
                )
        else:
            missing = sorted(f for f in required if getattr(self, f) is None)
            if missing:
                raise ValueError(
                    f"Measurement {self.id!r} (primitive {self.primitive.value}) "
                    f"requires field(s) {missing}"
                )

        # Reject primitive-specific fields that don't belong to this primitive.
        extra = sorted(
            f for f in _PRIMITIVE_SPECIFIC_FIELDS - allowed
            if getattr(self, f) is not None
        )
        if extra:
            raise ValueError(
                f"Measurement {self.id!r} (primitive {self.primitive.value}) "
                f"must not set field(s) {extra}"
            )

        # tran_thd: num_harmonics default if omitted is documented; no validator change.
        # window sanity: t_low < t_high if both set.
        if self.window is not None:
            t_lo, t_hi = self.window
            if t_lo < 0 or t_hi <= t_lo:
                raise ValueError(
                    f"Measurement {self.id!r}: window {(t_lo, t_hi)} must satisfy "
                    "0 <= t_low < t_high"
                )
        return self


class Tolerance(BaseModel):
    """Numerical tolerance for pass criteria. Gap K."""

    model_config = ConfigDict(extra="forbid")

    abs: float | None = Field(default=None, description="Absolute tolerance in spec_unit.")
    rel: float | None = Field(default=None, description="Relative tolerance (e.g. 0.01 = 1%).")

    @model_validator(mode="after")
    def _one_form(self) -> Tolerance:
        if self.abs is None and self.rel is None:
            raise ValueError("Tolerance needs at least one of abs/rel")
        return self


class PassCriterion(BaseModel):
    """Verdict rule against a measurement. Gap K + closes silent-bug class for units."""

    model_config = ConfigDict(extra="forbid")

    measurement: str = Field(..., description="Measurement.id being judged.")
    op: ComparisonOp
    value: float
    spec_unit: str = Field(
        ...,
        description="Unit the spec compares in (may differ from measurement.output_unit; "
        "evaluator must reconcile).",
    )
    tolerance: Tolerance | None = None


class Corner(BaseModel):
    """One process / temp / (optional supply) PVT point. Gap H."""

    model_config = ConfigDict(extra="forbid")

    process: str = Field(..., description='e.g. "TT", "FF", "SS", "FS", "SF".')
    temperature_celsius: float
    supply_voltage_override: float | None = Field(
        default=None,
        description="If set, overrides the PDK-default supply. Gap H.",
    )


# ───────────────────────────── top level ─────────────────────────────


class TestPlan(BaseModel):
    """Root of the TestPlan IR.

    Seven sections, each with its own clear responsibility. See module docstring
    for the gap → field mapping; see ``examples/01_diff_pair_ota/trace.md``
    for the worked example this schema was designed against.
    """

    model_config = ConfigDict(extra="forbid")

    # Keep pytest from treating this class as a test-collection target.
    __test__: ClassVar[bool] = False

    meta: Meta
    dut: Dut
    analyses: list[Analysis] = Field(..., min_length=1)
    stimulus: list[Stimulus] = Field(default_factory=list)
    loading: list[Loading] = Field(default_factory=list)
    measurements: list[Measurement] = Field(..., min_length=1)
    pass_criteria: list[PassCriterion] = Field(default_factory=list)
    corners: list[Corner] = Field(..., min_length=1)

    @model_validator(mode="after")
    def _cross_refs(self) -> TestPlan:
        analysis_by_id = {a.id: a for a in self.analyses}
        measurement_by_id = {m.id: m for m in self.measurements}
        stimulus_ids = {s.id for s in self.stimulus}
        roles = self.dut.roles

        # Measurement.from_analysis must exist + analysis-type must match primitive
        for m in self.measurements:
            a = analysis_by_id.get(m.from_analysis)
            if a is None:
                raise ValueError(
                    f"Measurement {m.id!r}: from_analysis {m.from_analysis!r} not in analyses"
                )
            expected_atype = PRIMITIVE_TO_ANALYSIS_TYPE[m.primitive]
            if a.type is not expected_atype:
                raise ValueError(
                    f"Measurement {m.id!r}: primitive {m.primitive.value} requires "
                    f"{expected_atype.value} analysis, but from_analysis "
                    f"{m.from_analysis!r} is {a.type.value}"
                )
            if m.on_role not in roles:
                raise ValueError(
                    f"Measurement {m.id!r}: on_role {m.on_role!r} not in DUT roles {sorted(roles)}"
                )
            # primitive-level role cross-refs
            for role_attr in ("target_output_role", "supply_role", "input_role", "output_role"):
                role_val = getattr(m, role_attr)
                if role_val is not None and role_val not in roles:
                    raise ValueError(
                        f"Measurement {m.id!r}: {role_attr}={role_val!r} not in "
                        f"DUT roles {sorted(roles)}"
                    )
            # at_when_measurement must reference a real measurement (and not itself)
            if m.at_when_measurement is not None:
                if m.at_when_measurement == m.id:
                    raise ValueError(
                        f"Measurement {m.id!r}: at_when_measurement cannot reference self"
                    )
                if m.at_when_measurement not in measurement_by_id:
                    raise ValueError(
                        f"Measurement {m.id!r}: at_when_measurement "
                        f"{m.at_when_measurement!r} not in measurements"
                    )
            # tran_settling_time trigger_event.stimulus_id must exist
            if m.trigger_event is not None:
                if m.trigger_event.stimulus_id not in stimulus_ids:
                    raise ValueError(
                        f"Measurement {m.id!r}: trigger_event.stimulus_id "
                        f"{m.trigger_event.stimulus_id!r} not in stimulus"
                    )

        # PassCriterion.measurement must exist
        for pc in self.pass_criteria:
            if pc.measurement not in measurement_by_id:
                raise ValueError(
                    f"PassCriterion: measurement {pc.measurement!r} not in measurements"
                )

        # Stimulus/Loading ports must reference DUT roles
        for s in self.stimulus:
            unknown = [r for r in s.ports if r not in roles]
            if unknown:
                raise ValueError(
                    f"Stimulus {s.id!r}: ports {unknown} not in DUT roles {sorted(roles)}"
                )
            if s.scope is Scope.ANALYSIS and s.scope_analysis_id not in analysis_by_id:
                raise ValueError(
                    f"Stimulus {s.id!r}: scope_analysis_id "
                    f"{s.scope_analysis_id!r} not in analyses"
                )

        for ld in self.loading:
            for r in (ld.from_role, ld.to_role):
                if r not in roles:
                    raise ValueError(
                        f"Loading {ld.id!r}: role {r!r} not in DUT roles {sorted(roles)}"
                    )
            if ld.scope is Scope.ANALYSIS and ld.scope_analysis_id not in analysis_by_id:
                raise ValueError(
                    f"Loading {ld.id!r}: scope_analysis_id "
                    f"{ld.scope_analysis_id!r} not in analyses"
                )

        # DcAnalysis.sweep_source_role + NoiseAnalysis.output_role / input_stimulus_id
        for a in self.analyses:
            if isinstance(a, DcAnalysis) and a.sweep_source_role is not None:
                if a.sweep_source_role not in roles:
                    raise ValueError(
                        f"DcAnalysis {a.id!r}: sweep_source_role {a.sweep_source_role!r} "
                        f"not in DUT roles {sorted(roles)}"
                    )
            if isinstance(a, NoiseAnalysis):
                if a.output_role not in roles:
                    raise ValueError(
                        f"NoiseAnalysis {a.id!r}: output_role {a.output_role!r} "
                        f"not in DUT roles {sorted(roles)}"
                    )
                if a.input_stimulus_id not in stimulus_ids:
                    raise ValueError(
                        f"NoiseAnalysis {a.id!r}: input_stimulus_id "
                        f"{a.input_stimulus_id!r} not in stimulus"
                    )

        return self


# ───────────────────────── semantic equivalence ──────────────────────────
#
# Two TestPlans are *semantically equivalent* when they specify the same
# test, regardless of:
#   - metadata (`meta.id`, `meta.nl_spec`) — these are labels, not content
#   - element order in unordered lists (analyses, stimulus, loading,
#     measurements, pass_criteria, corners) — they all run together
#
# Order DOES matter in `dut.subckt_ports` because that is the SPICE
# instantiation call order; changing it changes the emitted netlist.
#
# Implementation: canonicalize both sides (strip ignored fields, sort
# unordered lists by a stable JSON key), then compare as plain dicts.

_UNORDERED_LIST_FIELDS = (
    "analyses",
    "stimulus",
    "loading",
    "measurements",
    "pass_criteria",
    "corners",
)
_META_FIELDS_IGNORED_FOR_EQUIVALENCE = ("id", "nl_spec")


def _stable_key(item: Any) -> str:
    """Deterministic string key for sorting any JSON-serializable item."""
    return json.dumps(item, sort_keys=True, default=str)


def canonical_form(plan: TestPlan) -> dict[str, Any]:
    """Return the canonical-dict view of a TestPlan for semantic comparison.

    Exposed because it is useful when a test fails — diffing the two canonical
    forms shows exactly which field broke equivalence.
    """
    d = plan.model_dump(mode="json")
    for f in _META_FIELDS_IGNORED_FOR_EQUIVALENCE:
        d["meta"].pop(f, None)
    for field in _UNORDERED_LIST_FIELDS:
        d[field] = sorted(d.get(field, []), key=_stable_key)
    return d


def semantic_equivalent(a: TestPlan, b: TestPlan) -> bool:
    """True iff `a` and `b` specify the same test (see canonical_form rules)."""
    return canonical_form(a) == canonical_form(b)
