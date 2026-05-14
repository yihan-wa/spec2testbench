"""Shared fixtures and constants for the spec2testbench test suite.

Currently hosts the artifacts for `examples/01_diff_pair_ota` so tests across
files (round-trip, equivalence, live extraction) can speak about the same
gold IR + NL spec + DUT metadata.
"""

from __future__ import annotations

import pytest

from spec2testbench.extract import DutMetadata
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
)

# ─────────────── Example 01: 5-tran diff-pair OTA ─────────────────

NL_SPEC_01 = (
    "We need to verify the small-signal performance of our 5-transistor "
    "differential-pair OTA. The DC gain should exceed 60 dB, and the "
    "unity-gain bandwidth should be at least 10 MHz when driving a 1 pF "
    "capacitive load on the output. Please run AC analysis from 1 Hz up "
    "to 1 GHz. TT corner at 27 °C is sufficient for now — we'll add "
    "corners later."
)

DUT_01 = DutMetadata(
    name="diff_pair_ota_5t",
    netlist_path="examples/01_diff_pair_ota/dut.cir",
    subckt_ports=(
        ("vinp", "inp"),
        ("vinn", "inn"),
        ("vout", "out"),
        ("vbias", "bias_tail"),
        ("vdd", "vdd"),
        ("0", "vss"),
    ),
)

PLAN_ID_01 = "01_diff_pair_ota"


def make_gold_ir_01() -> TestPlan:
    """Programmatic construction of the gold IR for example 01.

    Mirrors trace.md §2 (the hand-written gold) translated into the v0
    TestPlan schema (which resolves the 11 gaps A–K).
    """
    return TestPlan(
        meta=Meta(id=PLAN_ID_01, nl_spec=NL_SPEC_01),
        dut=Dut(
            name=DUT_01.name,
            netlist_path=DUT_01.netlist_path,
            subckt_ports=[SubcktPort(name=n, role=r) for n, r in DUT_01.subckt_ports],
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
            Loading(
                id="c_load",
                kind=LoadingKind.C,
                value="1p",
                from_role="out",
                to_role="vss",
            )
        ],
        measurements=[
            Measurement(
                id="dc_gain",
                from_analysis="ac_smallsig",
                primitive=MeasurementPrimitive.AC_LOW_FREQ_ASYMPTOTE,
                on_role="out",
                output_unit="dB",
            ),
            Measurement(
                id="ugb",
                from_analysis="ac_smallsig",
                primitive=MeasurementPrimitive.AC_FREQ_AT_MAGNITUDE_CROSSING,
                on_role="out",
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


@pytest.fixture
def gold_ir() -> TestPlan:
    """Fixture form of make_gold_ir_01."""
    return make_gold_ir_01()


@pytest.fixture
def nl_spec_01() -> str:
    return NL_SPEC_01


@pytest.fixture
def dut_01() -> DutMetadata:
    return DUT_01


@pytest.fixture
def plan_id_01() -> str:
    return PLAN_ID_01
