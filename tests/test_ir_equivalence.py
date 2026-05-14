"""Tests for semantic_equivalent — the relation that decides when two
TestPlans specify the same test.

Equivalence rules:
- meta.id and meta.nl_spec are IGNORED (labels, not content)
- analyses / stimulus / loading / measurements / pass_criteria / corners are SET-LIKE (order ignored)
- dut.subckt_ports is SEQUENCE-LIKE (order matters — SPICE call order)
- Pydantic default-value filling means explicit vs implicit defaults are the same instance.
"""

from __future__ import annotations

import copy

import pytest

from spec2testbench.ir import (
    AcAnalysis,
    ComparisonOp,
    Corner,
    CrossingDirection,
    Dut,
    Loading,
    LoadingKind,
    Measurement,
    MeasurementPrimitive,
    Meta,
    PassCriterion,
    Stimulus,
    StimulusKind,
    SubcktPort,
    TestPlan,
    canonical_form,
    semantic_equivalent,
)


@pytest.fixture
def base_plan() -> TestPlan:
    """A minimal but realistic plan with multiple items in each unordered list."""
    return TestPlan(
        meta=Meta(id="base", nl_spec="original NL text"),
        dut=Dut(
            name="dut",
            subckt_ports=[
                SubcktPort(name="vinp", role="inp"),
                SubcktPort(name="vinn", role="inn"),
                SubcktPort(name="vout", role="out"),
                SubcktPort(name="vdd", role="vdd"),
                SubcktPort(name="0", role="vss"),
            ],
        ),
        analyses=[AcAnalysis(id="ac_smallsig", f_start=1.0, f_stop=1.0e9)],
        stimulus=[
            Stimulus(
                id="diff_ac",
                kind=StimulusKind.BALANCED_DIFFERENTIAL_AC,
                ports=["inp", "inn"],
                magnitude=1.0,
            ),
        ],
        loading=[
            Loading(id="cload", kind=LoadingKind.C, value="1p", from_role="out", to_role="vss"),
        ],
        measurements=[
            Measurement(
                id="dc_gain",
                from_analysis="ac_smallsig",
                primitive=MeasurementPrimitive.AC_LOW_FREQ_ASYMPTOTE,
                output_unit="dB",
            ),
            Measurement(
                id="ugb",
                from_analysis="ac_smallsig",
                primitive=MeasurementPrimitive.AC_FREQ_AT_MAGNITUDE_CROSSING,
                output_unit="Hz",
                target_magnitude=1.0,
                direction=CrossingDirection.FALLING,
            ),
        ],
        pass_criteria=[
            PassCriterion(measurement="dc_gain", op=ComparisonOp.GT, value=60.0, spec_unit="dB"),
            PassCriterion(measurement="ugb", op=ComparisonOp.GE, value=1.0e7, spec_unit="Hz"),
        ],
        corners=[Corner(process="TT", temperature_celsius=27.0)],
    )


# ───────────── identity / round-trip ─────────────


def test_reflexive(base_plan: TestPlan) -> None:
    assert semantic_equivalent(base_plan, base_plan)


def test_round_trip_equivalent(base_plan: TestPlan) -> None:
    revived = TestPlan.model_validate_json(base_plan.model_dump_json())
    assert semantic_equivalent(base_plan, revived)


# ───────────── meta fields are ignored ─────────────


def test_id_difference_is_ignored(base_plan: TestPlan) -> None:
    other = base_plan.model_copy(update={"meta": Meta(id="renamed", nl_spec=base_plan.meta.nl_spec)})
    assert semantic_equivalent(base_plan, other)


def test_nl_spec_difference_is_ignored(base_plan: TestPlan) -> None:
    other = base_plan.model_copy(update={"meta": Meta(id=base_plan.meta.id, nl_spec="paraphrased")})
    assert semantic_equivalent(base_plan, other)


# ───────────── unordered lists — order does not matter ─────────────


def test_measurement_order_ignored(base_plan: TestPlan) -> None:
    reordered = base_plan.model_copy(update={"measurements": list(reversed(base_plan.measurements))})
    assert semantic_equivalent(base_plan, reordered)


def test_pass_criteria_order_ignored(base_plan: TestPlan) -> None:
    reordered = base_plan.model_copy(
        update={"pass_criteria": list(reversed(base_plan.pass_criteria))}
    )
    assert semantic_equivalent(base_plan, reordered)


def test_corners_order_ignored() -> None:
    """Build two plans differing only in corner ordering; assert equivalent."""
    common = dict(
        meta=Meta(id="x"),
        dut=Dut(name="d", subckt_ports=[SubcktPort(name="vout", role="out")]),
        analyses=[AcAnalysis(id="a", f_start=1.0, f_stop=1.0e9)],
        measurements=[
            Measurement(
                id="m",
                from_analysis="a",
                primitive=MeasurementPrimitive.AC_LOW_FREQ_ASYMPTOTE,
                output_unit="dB",
            )
        ],
    )
    a = TestPlan(
        **common,
        corners=[
            Corner(process="TT", temperature_celsius=27.0),
            Corner(process="FF", temperature_celsius=85.0),
        ],
    )
    b = TestPlan(
        **common,
        corners=[
            Corner(process="FF", temperature_celsius=85.0),
            Corner(process="TT", temperature_celsius=27.0),
        ],
    )
    assert semantic_equivalent(a, b)


# ───────────── subckt_ports order matters ─────────────


def test_subckt_port_order_matters(base_plan: TestPlan) -> None:
    """Reordering DUT subckt ports changes SPICE call order — must NOT be equivalent."""
    ports = list(base_plan.dut.subckt_ports)
    ports[0], ports[1] = ports[1], ports[0]  # swap vinp / vinn
    other = base_plan.model_copy(update={"dut": base_plan.dut.model_copy(update={"subckt_ports": ports})})
    assert not semantic_equivalent(base_plan, other)


# ───────────── content differences DO break equivalence ─────────────


def test_pass_threshold_value_matters(base_plan: TestPlan) -> None:
    raw = base_plan.model_dump()
    raw["pass_criteria"][0]["value"] = 70.0  # change DC gain threshold 60 → 70
    other = TestPlan.model_validate(raw)
    assert not semantic_equivalent(base_plan, other)


def test_crossing_direction_matters(base_plan: TestPlan) -> None:
    """Gap G in action: changing direction on UGB primitive breaks equivalence."""
    raw = base_plan.model_dump()
    for m in raw["measurements"]:
        if m["id"] == "ugb":
            m["direction"] = "rising"
    other = TestPlan.model_validate(raw)
    assert not semantic_equivalent(base_plan, other)


def test_extra_corner_breaks_equivalence(base_plan: TestPlan) -> None:
    other = base_plan.model_copy(
        update={
            "corners": list(base_plan.corners)
            + [Corner(process="FF", temperature_celsius=85.0)]
        }
    )
    assert not semantic_equivalent(base_plan, other)


# ───────────── implicit defaults ≡ explicit defaults ─────────────


def test_explicit_default_matches_implicit_default(base_plan: TestPlan) -> None:
    """Explicitly writing the default sweep_style should equal omitting it."""
    raw = base_plan.model_dump()
    raw["analyses"][0].pop("sweep_style", None)
    raw["analyses"][0].pop("points_per_decade", None)
    other = TestPlan.model_validate(raw)
    assert semantic_equivalent(base_plan, other)


# ───────────── debugging hook: canonical_form is a dict ─────────────


def test_canonical_form_is_dict(base_plan: TestPlan) -> None:
    cf = canonical_form(base_plan)
    assert isinstance(cf, dict)
    # meta.id is stripped from the canonical form:
    assert "id" not in cf["meta"]
    assert "nl_spec" not in cf["meta"]


# ───────────── transitivity sanity ─────────────


def test_transitive_under_label_changes(base_plan: TestPlan) -> None:
    """a == b and b == c implies a == c, even when changes are only in metadata."""
    a = base_plan
    b = a.model_copy(update={"meta": Meta(id="B", nl_spec="b")})
    c = b.model_copy(update={"meta": Meta(id="C", nl_spec="c")})
    assert semantic_equivalent(a, b) and semantic_equivalent(b, c)
    assert semantic_equivalent(a, c)


# unused but useful when this file grows
_ = copy
