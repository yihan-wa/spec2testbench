"""TestPlan IR — the structured intermediate between NL spec and emitted netlist.

Designed to resolve the 11 schema gaps (A–K) surfaced by the manual end-to-end
walkthrough in ``examples/01_diff_pair_ota/trace.md``. Each gap's resolution is
called out inline next to the field that carries it. Gaps not addressed here
are deferred deliberately:

- Gap I (VDD / ibias / Vin_common_mode): deferred to a future ``PDKContext``
  data structure; intentionally NOT representable in the IR.
- Emitter knowledge debts K1–K14: out of scope for Step 2 (Step 5).
- Evaluator transform table E1–E2: out of scope for Step 2 (Step 4).

The IR is intentionally split into seven top-level sections — a single flat
JSON record collapses as soon as one analysis feeds multiple measurements.
"""

from __future__ import annotations

import json
from enum import Enum
from typing import Any, ClassVar, Literal

from pydantic import BaseModel, ConfigDict, Field, model_validator


# ───────────────────────────── enums ──────────────────────────────


class SweepStyle(str, Enum):
    """Frequency-sweep spacing for AC. Gap C."""

    DEC = "dec"
    OCT = "oct"
    LIN = "lin"


class AnalysisType(str, Enum):
    AC = "AC"
    # Reserved for v0+: DC, TRAN, NOISE — add when a second running example needs them.


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


class LoadingKind(str, Enum):
    C = "C"
    R = "R"
    L = "L"


class CrossingDirection(str, Enum):
    """Direction qualifier for crossing-style measurements. Gap G."""

    RISING = "rising"
    FALLING = "falling"
    ANY = "any"


class MeasurementPrimitive(str, Enum):
    """Closed library of measurement primitives. Gap F + G.

    v0 ships exactly the two primitives the running example needs.
    Extend deliberately as new running examples introduce new spec kinds —
    do not add speculatively.
    """

    AC_LOW_FREQ_ASYMPTOTE = "ac_low_freq_asymptote"  # → DC gain (Gap F)
    AC_FREQ_AT_MAGNITUDE_CROSSING = "ac_freq_at_magnitude_crossing"  # → UGB (Gap G)


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


# v0 only ships AC. When more types arrive, switch to a discriminated union.
Analysis = AcAnalysis


class Stimulus(BaseModel):
    """A signal-source description referencing DUT port roles. Gap D."""

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
    dc_value: float | None = Field(
        default=None,
        description="For DC_voltage / bias-pin stimuli.",
    )
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


class Measurement(BaseModel):
    """A named scalar derived from an analysis run. Gap F + G."""

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
        '(e.g. "dB", "Hz", "V", "A"). Drives evaluator unit reconciliation.',
    )

    # primitive-specific parameters (all optional at base level; validators below enforce)
    target_magnitude: float | None = Field(
        default=None,
        description="Required for AC_FREQ_AT_MAGNITUDE_CROSSING; the |H| value to find.",
    )
    direction: CrossingDirection | None = Field(
        default=None,
        description="Required for AC_FREQ_AT_MAGNITUDE_CROSSING; crossing direction. Gap G.",
    )

    @model_validator(mode="after")
    def _primitive_params(self) -> Measurement:
        p = self.primitive
        if p is MeasurementPrimitive.AC_FREQ_AT_MAGNITUDE_CROSSING:
            if self.target_magnitude is None or self.direction is None:
                raise ValueError(
                    f"primitive {p} requires both target_magnitude and direction"
                )
        elif p is MeasurementPrimitive.AC_LOW_FREQ_ASYMPTOTE:
            if self.target_magnitude is not None or self.direction is not None:
                raise ValueError(
                    f"primitive {p} takes no target_magnitude / direction; got "
                    f"target_magnitude={self.target_magnitude}, direction={self.direction}"
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
        analysis_ids = {a.id for a in self.analyses}
        measurement_ids = {m.id for m in self.measurements}
        roles = self.dut.roles

        # Measurement.from_analysis must exist
        for m in self.measurements:
            if m.from_analysis not in analysis_ids:
                raise ValueError(
                    f"Measurement {m.id!r}: from_analysis {m.from_analysis!r} not in analyses"
                )
            if m.on_role not in roles:
                raise ValueError(
                    f"Measurement {m.id!r}: on_role {m.on_role!r} not in DUT roles {sorted(roles)}"
                )

        # PassCriterion.measurement must exist
        for pc in self.pass_criteria:
            if pc.measurement not in measurement_ids:
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
            if s.scope is Scope.ANALYSIS and s.scope_analysis_id not in analysis_ids:
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
            if ld.scope is Scope.ANALYSIS and ld.scope_analysis_id not in analysis_ids:
                raise ValueError(
                    f"Loading {ld.id!r}: scope_analysis_id "
                    f"{ld.scope_analysis_id!r} not in analyses"
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
