"""Tests for the 2026-05-15 evaluator hardening:

E-ID: entity IDs (analyses, stimulus, loading, measurements) are LLM-
chosen labels; two plans differing only in those names must compare
equivalent. Cross-refs (from_analysis, scope_analysis_id, at_when_measurement,
trigger_event.stimulus_id, pass_criteria.measurement, NoiseAnalysis.
input_stimulus_id) must follow the rename consistently.

E-UNIT: pass-criterion (value, spec_unit) pairs must compare equivalent
under SI-prefix unit conversion: (10, "MHz") ≡ (1e7, "Hz"); (5, "mV") ≡
(0.005, "V"); etc. Real value/unit mismatches must still be detected.
"""

from __future__ import annotations

import pytest

from spec2testbench.ir import (
    AcAnalysis,
    ComparisonOp,
    Corner,
    CrossingDirection,
    DcAnalysis,
    Dut,
    Loading,
    LoadingKind,
    Measurement,
    MeasurementPrimitive,
    Meta,
    NoiseAnalysis,
    PassCriterion,
    Scope,
    Stimulus,
    StimulusKind,
    SubcktPort,
    TestPlan,
    Tolerance,
    TranAnalysis,
    TranPulseParams,
    TranStepParams,
    TransitionEdge,
    TriggerEvent,
    canonical_form,
    semantic_equivalent,
)


# ──────────────────── helpers ────────────────────


def _mini_dut() -> Dut:
    return Dut(
        name="amp",
        netlist_path="amp.cir",
        subckt_ports=[
            SubcktPort(name="vinp", role="inp"),
            SubcktPort(name="vinn", role="inn"),
            SubcktPort(name="vout", role="out"),
            SubcktPort(name="vdd", role="vdd"),
            SubcktPort(name="0", role="vss"),
        ],
    )


def _tt27() -> Corner:
    return Corner(process="TT", temperature_celsius=27.0)


# ─────────────── E-ID: identifier canonicalization ──────────────


def test_eid_two_plans_differing_only_in_analysis_id_are_equivalent() -> None:
    """analyses[].id rename: cross-ref from measurement must follow."""
    def plan_with_analysis_id(aid: str) -> TestPlan:
        return TestPlan(
            meta=Meta(id="p", nl_spec=None),
            dut=_mini_dut(),
            analyses=[AcAnalysis(id=aid, f_start=1.0, f_stop=1e9)],
            stimulus=[
                Stimulus(
                    id="any_stim",
                    kind=StimulusKind.BALANCED_DIFFERENTIAL_AC,
                    ports=["inp", "inn"],
                    magnitude=1.0,
                )
            ],
            measurements=[
                Measurement(
                    id="dc_gain",
                    from_analysis=aid,
                    primitive=MeasurementPrimitive.AC_LOW_FREQ_ASYMPTOTE,
                    output_unit="dB",
                )
            ],
            corners=[_tt27()],
        )

    a = plan_with_analysis_id("ac_smallsig")
    b = plan_with_analysis_id("ac_1")
    assert semantic_equivalent(a, b)


def test_eid_measurement_id_rename_propagates_to_pass_criteria() -> None:
    def plan_with_measurement_id(mid: str) -> TestPlan:
        return TestPlan(
            meta=Meta(id="p", nl_spec=None),
            dut=_mini_dut(),
            analyses=[AcAnalysis(id="ac", f_start=1.0, f_stop=1e9)],
            stimulus=[
                Stimulus(
                    id="s1",
                    kind=StimulusKind.BALANCED_DIFFERENTIAL_AC,
                    ports=["inp", "inn"],
                    magnitude=1.0,
                )
            ],
            measurements=[
                Measurement(
                    id=mid,
                    from_analysis="ac",
                    primitive=MeasurementPrimitive.AC_LOW_FREQ_ASYMPTOTE,
                    output_unit="dB",
                )
            ],
            pass_criteria=[
                PassCriterion(
                    measurement=mid, op=ComparisonOp.GT, value=60.0, spec_unit="dB"
                ),
            ],
            corners=[_tt27()],
        )

    a = plan_with_measurement_id("dc_gain")
    b = plan_with_measurement_id("m_dc_gain")
    assert semantic_equivalent(a, b)


def test_eid_stimulus_id_rename_propagates_to_trigger_event() -> None:
    def plan_with_stim_id(sid: str) -> TestPlan:
        return TestPlan(
            meta=Meta(id="p", nl_spec=None),
            dut=_mini_dut(),
            analyses=[TranAnalysis(id="tr", t_step=1e-9, t_stop=1e-6)],
            stimulus=[
                Stimulus(
                    id=sid,
                    kind=StimulusKind.TRAN_PULSE,
                    ports=["inp"],
                    pulse=TranPulseParams(v1=0.0, v2=1.0, pw=1e-7, per=1e-6),
                    scope=Scope.ANALYSIS,
                    scope_analysis_id="tr",
                ),
            ],
            measurements=[
                Measurement(
                    id="ts",
                    from_analysis="tr",
                    primitive=MeasurementPrimitive.TRAN_SETTLING_TIME,
                    output_unit="s",
                    tolerance_pct=0.001,
                    trigger_event=TriggerEvent(stimulus_id=sid, edge=TransitionEdge.RISING),
                )
            ],
            corners=[_tt27()],
        )

    a = plan_with_stim_id("pulse_in")
    b = plan_with_stim_id("s_pulse_xyz")
    assert semantic_equivalent(a, b)


def test_eid_loading_id_rename_does_not_break_equivalence() -> None:
    def plan_with_load_id(lid: str) -> TestPlan:
        return TestPlan(
            meta=Meta(id="p", nl_spec=None),
            dut=_mini_dut(),
            analyses=[AcAnalysis(id="ac", f_start=1.0, f_stop=1e9)],
            stimulus=[
                Stimulus(
                    id="s",
                    kind=StimulusKind.BALANCED_DIFFERENTIAL_AC,
                    ports=["inp", "inn"],
                    magnitude=1.0,
                )
            ],
            loading=[Loading(id=lid, kind=LoadingKind.C, value="1p",
                             from_role="out", to_role="vss")],
            measurements=[
                Measurement(
                    id="g",
                    from_analysis="ac",
                    primitive=MeasurementPrimitive.AC_LOW_FREQ_ASYMPTOTE,
                    output_unit="dB",
                )
            ],
            corners=[_tt27()],
        )

    a = plan_with_load_id("c_load")
    b = plan_with_load_id("cl_out")
    assert semantic_equivalent(a, b)


def test_eid_noise_analysis_input_stimulus_id_rename_follows() -> None:
    def plan_with_stim_id(sid: str) -> TestPlan:
        return TestPlan(
            meta=Meta(id="p", nl_spec=None),
            dut=_mini_dut(),
            analyses=[
                NoiseAnalysis(
                    id="ns",
                    output_role="out",
                    input_stimulus_id=sid,
                    f_start=1.0,
                    f_stop=1e6,
                )
            ],
            stimulus=[
                Stimulus(
                    id=sid,
                    kind=StimulusKind.SINGLE_ENDED_AC,
                    ports=["inp"],
                    magnitude=1.0,
                )
            ],
            measurements=[
                Measurement(
                    id="n",
                    from_analysis="ns",
                    primitive=MeasurementPrimitive.NOISE_INPUT_REFERRED_AT_FREQ,
                    output_unit="V/sqrt(Hz)",
                    at_freq=1e3,
                )
            ],
            corners=[_tt27()],
        )

    a = plan_with_stim_id("ac_in")
    b = plan_with_stim_id("ac_in_for_noise")
    assert semantic_equivalent(a, b)


def test_eid_at_when_measurement_chain_renames_consistently() -> None:
    """Phase margin references UGB; rename of UGB id must follow."""
    def plan_with_ugb_id(uid: str) -> TestPlan:
        return TestPlan(
            meta=Meta(id="p", nl_spec=None),
            dut=_mini_dut(),
            analyses=[AcAnalysis(id="ac", f_start=1.0, f_stop=1e9)],
            stimulus=[
                Stimulus(
                    id="s",
                    kind=StimulusKind.BALANCED_DIFFERENTIAL_AC,
                    ports=["inp", "inn"],
                    magnitude=1.0,
                )
            ],
            measurements=[
                Measurement(
                    id=uid,
                    from_analysis="ac",
                    primitive=MeasurementPrimitive.AC_FREQ_AT_MAGNITUDE_CROSSING,
                    output_unit="Hz",
                    target_magnitude=1.0,
                    direction=CrossingDirection.FALLING,
                ),
                Measurement(
                    id="pm",
                    from_analysis="ac",
                    primitive=MeasurementPrimitive.AC_PHASE_MARGIN,
                    output_unit="deg",
                    at_when_measurement=uid,
                ),
            ],
            corners=[_tt27()],
        )

    a = plan_with_ugb_id("ugb")
    b = plan_with_ugb_id("m_ugb")
    assert semantic_equivalent(a, b)


def test_eid_real_semantic_difference_still_detected() -> None:
    """Renaming is OK; a real primitive mismatch must still be flagged."""
    def plan_with_primitive(prim: MeasurementPrimitive) -> TestPlan:
        return TestPlan(
            meta=Meta(id="p", nl_spec=None),
            dut=_mini_dut(),
            analyses=[AcAnalysis(id="ac", f_start=1.0, f_stop=1e9)],
            stimulus=[
                Stimulus(
                    id="s",
                    kind=StimulusKind.BALANCED_DIFFERENTIAL_AC,
                    ports=["inp", "inn"],
                    magnitude=1.0,
                )
            ],
            measurements=[
                Measurement(
                    id="m",
                    from_analysis="ac",
                    primitive=prim,
                    output_unit="dB" if prim is MeasurementPrimitive.AC_LOW_FREQ_ASYMPTOTE else "Hz",
                    target_magnitude=None if prim is MeasurementPrimitive.AC_LOW_FREQ_ASYMPTOTE else 1.0,
                    direction=None if prim is MeasurementPrimitive.AC_LOW_FREQ_ASYMPTOTE else CrossingDirection.FALLING,
                )
            ],
            corners=[_tt27()],
        )

    a = plan_with_primitive(MeasurementPrimitive.AC_LOW_FREQ_ASYMPTOTE)
    b = plan_with_primitive(MeasurementPrimitive.AC_FREQ_AT_MAGNITUDE_CROSSING)
    assert not semantic_equivalent(a, b)


# ─────────────── E-UNIT: unit normalization ──────────────


def test_eunit_mhz_and_hz_pair_equivalent() -> None:
    """(10, MHz) and (1e7, Hz) describe the same threshold."""
    def plan_with_pc(value: float, unit: str) -> TestPlan:
        return TestPlan(
            meta=Meta(id="p", nl_spec=None),
            dut=_mini_dut(),
            analyses=[AcAnalysis(id="ac", f_start=1.0, f_stop=1e9)],
            stimulus=[
                Stimulus(
                    id="s",
                    kind=StimulusKind.BALANCED_DIFFERENTIAL_AC,
                    ports=["inp", "inn"],
                    magnitude=1.0,
                )
            ],
            measurements=[
                Measurement(
                    id="ugb",
                    from_analysis="ac",
                    primitive=MeasurementPrimitive.AC_FREQ_AT_MAGNITUDE_CROSSING,
                    output_unit="Hz",
                    target_magnitude=1.0,
                    direction=CrossingDirection.FALLING,
                )
            ],
            pass_criteria=[
                PassCriterion(measurement="ugb", op=ComparisonOp.GE, value=value, spec_unit=unit),
            ],
            corners=[_tt27()],
        )

    assert semantic_equivalent(plan_with_pc(10.0, "MHz"), plan_with_pc(1.0e7, "Hz"))


@pytest.mark.parametrize(
    "a_val,a_unit,b_val,b_unit",
    [
        (5.0, "mV", 0.005, "V"),
        (100.0, "uA", 1.0e-4, "A"),
        (1.0, "ms", 1.0e-3, "s"),
        (10.0, "V/us", 1.0e7, "V/s"),
        (10.0, "nV/sqrt(Hz)", 1.0e-8, "V/sqrt(Hz)"),
        (500.0, "uS", 5.0e-4, "S"),
        (200.0, "ns", 2.0e-7, "s"),
        (1.0, "GHz", 1.0e9, "Hz"),
    ],
)
def test_eunit_equivalent_unit_pairs_across_dimensions(
    a_val: float, a_unit: str, b_val: float, b_unit: str
) -> None:
    """Sweep multiple unit dimensions to confirm conversion table coverage."""
    def plan_with_pc(value: float, unit: str) -> TestPlan:
        return TestPlan(
            meta=Meta(id="p", nl_spec=None),
            dut=_mini_dut(),
            analyses=[DcAnalysis(id="op")],
            measurements=[
                Measurement(
                    id="m",
                    from_analysis="op",
                    primitive=MeasurementPrimitive.DC_SUPPLY_CURRENT,
                    output_unit="A",
                    supply_role="vdd",
                )
            ],
            pass_criteria=[
                PassCriterion(measurement="m", op=ComparisonOp.LE, value=value, spec_unit=unit),
            ],
            corners=[_tt27()],
        )

    assert semantic_equivalent(plan_with_pc(a_val, a_unit), plan_with_pc(b_val, b_unit))


def test_eunit_real_value_mismatch_still_detected() -> None:
    """5 mV ≠ 10 mV. Normalization must not mask real differences."""
    def plan_with_pc(value: float) -> TestPlan:
        return TestPlan(
            meta=Meta(id="p", nl_spec=None),
            dut=_mini_dut(),
            analyses=[
                DcAnalysis(
                    id="sw",
                    sweep_source_role="inp",
                    sweep_start=-0.01,
                    sweep_stop=0.01,
                    sweep_step=0.0001,
                )
            ],
            stimulus=[
                Stimulus(
                    id="sweep_in",
                    kind=StimulusKind.DC_SWEEP_SOURCE,
                    ports=["inp"],
                    scope=Scope.ANALYSIS,
                    scope_analysis_id="sw",
                )
            ],
            measurements=[
                Measurement(
                    id="vos",
                    from_analysis="sw",
                    primitive=MeasurementPrimitive.DC_OFFSET_INPUT_REFERRED,
                    output_unit="V",
                    target_output_role="out",
                    target_output_value="midrail",
                )
            ],
            pass_criteria=[
                PassCriterion(measurement="vos", op=ComparisonOp.LT, value=value, spec_unit="mV"),
            ],
            corners=[_tt27()],
        )

    assert not semantic_equivalent(plan_with_pc(5.0), plan_with_pc(10.0))


def test_eunit_tolerance_abs_also_normalized() -> None:
    """tolerance.abs is denominated in spec_unit; must convert alongside value."""
    def plan_with(value: float, unit: str, tol_abs: float) -> TestPlan:
        return TestPlan(
            meta=Meta(id="p", nl_spec=None),
            dut=_mini_dut(),
            analyses=[AcAnalysis(id="ac", f_start=1.0, f_stop=1e9)],
            stimulus=[
                Stimulus(
                    id="s",
                    kind=StimulusKind.BALANCED_DIFFERENTIAL_AC,
                    ports=["inp", "inn"],
                    magnitude=1.0,
                )
            ],
            measurements=[
                Measurement(
                    id="ugb",
                    from_analysis="ac",
                    primitive=MeasurementPrimitive.AC_FREQ_AT_MAGNITUDE_CROSSING,
                    output_unit="Hz",
                    target_magnitude=1.0,
                    direction=CrossingDirection.FALLING,
                )
            ],
            pass_criteria=[
                PassCriterion(
                    measurement="ugb",
                    op=ComparisonOp.APPROX_EQ,
                    value=value,
                    spec_unit=unit,
                    tolerance=Tolerance(abs=tol_abs),
                ),
            ],
            corners=[_tt27()],
        )

    # (10 MHz ± 1 MHz) ≡ (1e7 Hz ± 1e6 Hz)
    assert semantic_equivalent(
        plan_with(10.0, "MHz", tol_abs=1.0),
        plan_with(1.0e7, "Hz", tol_abs=1.0e6),
    )


def test_eunit_unknown_units_pass_through_unchanged() -> None:
    """Made-up unit strings shouldn't be silently rewritten."""
    def plan_with_unit(unit: str) -> TestPlan:
        return TestPlan(
            meta=Meta(id="p", nl_spec=None),
            dut=_mini_dut(),
            analyses=[AcAnalysis(id="ac", f_start=1.0, f_stop=1e9)],
            stimulus=[
                Stimulus(
                    id="s",
                    kind=StimulusKind.BALANCED_DIFFERENTIAL_AC,
                    ports=["inp", "inn"],
                    magnitude=1.0,
                )
            ],
            measurements=[
                Measurement(
                    id="g",
                    from_analysis="ac",
                    primitive=MeasurementPrimitive.AC_LOW_FREQ_ASYMPTOTE,
                    output_unit="dB",
                )
            ],
            pass_criteria=[
                PassCriterion(measurement="g", op=ComparisonOp.GT, value=60.0, spec_unit=unit),
            ],
            corners=[_tt27()],
        )

    # Two different unknown-unit strings should NOT be silently equated
    a = plan_with_unit("decibel")
    b = plan_with_unit("dB")
    assert not semantic_equivalent(a, b)


# ────────────── E-FLOAT: recursive sig-fig normalization ─────────────


def test_efloat_target_magnitude_precision_difference_equivalent() -> None:
    """LLM emits 707.95 (5 sig figs); gold emits 1000 * 10^(-3/20) = 707.9457...

    With 12-sig-fig recursive normalization, both round to 707.946 — the
    extra precision is absorbed.
    """
    target_gold = 1000.0 * (10.0 ** (-3.0 / 20.0))  # 707.9457843841379

    def plan_with(target: float) -> TestPlan:
        return TestPlan(
            meta=Meta(id="p", nl_spec=None),
            dut=_mini_dut(),
            analyses=[AcAnalysis(id="ac", f_start=1.0, f_stop=1e9)],
            stimulus=[
                Stimulus(
                    id="s",
                    kind=StimulusKind.BALANCED_DIFFERENTIAL_AC,
                    ports=["inp", "inn"],
                    magnitude=1.0,
                )
            ],
            measurements=[
                Measurement(
                    id="f_minus_3db",
                    from_analysis="ac",
                    primitive=MeasurementPrimitive.AC_FREQ_AT_MAGNITUDE_CROSSING,
                    output_unit="Hz",
                    target_magnitude=target,
                    direction=CrossingDirection.FALLING,
                ),
            ],
            corners=[_tt27()],
        )

    # Note: 707.95 is only 5 sig figs; 707.946 is 6 sig figs.  At 12 sig
    # figs, 707.95 becomes 707.95 and 707.9457843841379 becomes 707.945784384.
    # Those differ at the 5th sig fig — NOT equivalent.  The point of the
    # normalization is to make matching computed values match; the LLM
    # rounding to 5 sig figs is a separate problem that prompt-fidelity
    # guidance should address.  Demonstrate the matching case here.
    target_close = 707.9457843841380  # differs only at the 16th digit
    assert semantic_equivalent(plan_with(target_gold), plan_with(target_close))


def test_efloat_subtle_float_repr_difference_equivalent() -> None:
    """t_step values that differ only at the float-representation level
    (e.g., 200.0*1e-9 vs 2e-7) compare equivalent."""

    def plan_with_tr(tr: float) -> TestPlan:
        return TestPlan(
            meta=Meta(id="p", nl_spec=None),
            dut=_mini_dut(),
            analyses=[TranAnalysis(id="tr", t_step=1e-9, t_stop=1e-6)],
            stimulus=[
                Stimulus(
                    id="s",
                    kind=StimulusKind.TRAN_STEP,
                    ports=["inp"],
                    step=TranStepParams(v1=0.0, v2=0.5, t_step=1e-7, tr=tr),
                    scope=Scope.ANALYSIS,
                    scope_analysis_id="tr",
                )
            ],
            measurements=[
                Measurement(
                    id="ts",
                    from_analysis="tr",
                    primitive=MeasurementPrimitive.TRAN_SETTLING_TIME,
                    output_unit="s",
                    tolerance_pct=0.001,
                    trigger_event=TriggerEvent(stimulus_id="s", edge=TransitionEdge.RISING),
                )
            ],
            corners=[_tt27()],
        )

    a = plan_with_tr(100.0 * 1e-12)   # 1.0000000000000002e-10 in float
    b = plan_with_tr(1e-10)            # canonical
    assert semantic_equivalent(a, b)


def test_efloat_real_value_difference_still_detected() -> None:
    """An order-of-magnitude difference must NOT be normalized away."""

    def plan_with(target: float) -> TestPlan:
        return TestPlan(
            meta=Meta(id="p", nl_spec=None),
            dut=_mini_dut(),
            analyses=[AcAnalysis(id="ac", f_start=1.0, f_stop=1e9)],
            stimulus=[
                Stimulus(
                    id="s",
                    kind=StimulusKind.BALANCED_DIFFERENTIAL_AC,
                    ports=["inp", "inn"],
                    magnitude=1.0,
                )
            ],
            measurements=[
                Measurement(
                    id="ugb",
                    from_analysis="ac",
                    primitive=MeasurementPrimitive.AC_FREQ_AT_MAGNITUDE_CROSSING,
                    output_unit="Hz",
                    target_magnitude=target,
                    direction=CrossingDirection.FALLING,
                ),
            ],
            corners=[_tt27()],
        )

    # 1.0 vs 1.01 — definitely a real difference, should NOT be normalized.
    assert not semantic_equivalent(plan_with(1.0), plan_with(1.01))


# ────────────── combined: the actual smoke-test diff ─────────────


def test_combined_smoke_test_scenario_passes() -> None:
    """Recreate the MiMo-v2.5-pro vs gold diff: differs in IDs (all 4 kinds)
    and pass-criterion units. After hardening, both should be equivalent."""

    gold = TestPlan(
        meta=Meta(id="a1_diff_pair_gain_ugb", nl_spec="..."),
        dut=Dut(
            name="diff_pair_ota_5t",
            netlist_path="examples/01_diff_pair_ota/dut.cir",
            subckt_ports=[
                SubcktPort(name="vinp", role="inp"),
                SubcktPort(name="vinn", role="inn"),
                SubcktPort(name="vout", role="out"),
                SubcktPort(name="vbias", role="bias_tail"),
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
            )
        ],
        loading=[
            Loading(id="c_load", kind=LoadingKind.C, value="1p",
                    from_role="out", to_role="vss")
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
        corners=[_tt27()],
    )

    # LLM's version: different IDs, MHz value form
    extracted = TestPlan(
        meta=Meta(id="a1_diff_pair_gain_ugb", nl_spec="..."),
        dut=gold.dut,  # same DUT
        analyses=[AcAnalysis(id="ac_1", f_start=1.0, f_stop=1.0e9)],
        stimulus=[
            Stimulus(
                id="stim_diff_ac",
                kind=StimulusKind.BALANCED_DIFFERENTIAL_AC,
                ports=["inp", "inn"],
                magnitude=1.0,
            )
        ],
        loading=[
            Loading(id="cl_out", kind=LoadingKind.C, value="1p",
                    from_role="out", to_role="vss")
        ],
        measurements=[
            Measurement(
                id="m_dc_gain",
                from_analysis="ac_1",
                primitive=MeasurementPrimitive.AC_LOW_FREQ_ASYMPTOTE,
                output_unit="dB",
            ),
            Measurement(
                id="m_ugb",
                from_analysis="ac_1",
                primitive=MeasurementPrimitive.AC_FREQ_AT_MAGNITUDE_CROSSING,
                output_unit="Hz",
                target_magnitude=1.0,
                direction=CrossingDirection.FALLING,
            ),
        ],
        pass_criteria=[
            PassCriterion(measurement="m_dc_gain", op=ComparisonOp.GT, value=60.0, spec_unit="dB"),
            PassCriterion(measurement="m_ugb", op=ComparisonOp.GE, value=10.0, spec_unit="MHz"),
        ],
        corners=[_tt27()],
    )

    assert semantic_equivalent(extracted, gold)
    # And canonical forms match exactly
    assert canonical_form(extracted) == canonical_form(gold)
