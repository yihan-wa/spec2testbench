"""Tests covering the IR extension done 2026-05-15 for Step 4:
- 4 analysis types (AC + TRAN + DC + NOISE) with the discriminated union
- 14 new measurement primitives and their cross-field validators
- 4 new stimulus kinds (tran_pulse, tran_sine, tran_step, dc_sweep_source)
- cross-ref checks for the new fields (at_when_measurement, trigger_event,
  DcAnalysis.sweep_source_role, NoiseAnalysis.output_role/input_stimulus_id,
  primitive-vs-analysis type matching).

Goal: every new primitive has at least one positive round-trip and one
negative validator test; every new analysis type has the same; every new
cross-ref is exercised at least once.
"""

from __future__ import annotations

import pytest

from spec2testbench.ir import (
    AcAnalysis,
    AnalysisType,
    ComparisonOp,
    Corner,
    CrossingDirection,
    DcAnalysis,
    Dut,
    LoadingKind,
    Measurement,
    MeasurementPrimitive,
    Meta,
    NoiseAnalysis,
    NoiseReferenceSide,
    PassCriterion,
    Scope,
    Stimulus,
    StimulusKind,
    SubcktPort,
    SwingExtreme,
    TestPlan,
    Tolerance,
    TranAnalysis,
    TranPulseParams,
    TranSineParams,
    TranStepParams,
    TransitionEdge,
    TriggerEvent,
    canonical_form,
    semantic_equivalent,
)

# ───────────────────────── shared minimal DUT ─────────────────────────


def _mini_dut() -> Dut:
    return Dut(
        name="amp",
        netlist_path="amp.cir",
        subckt_ports=[
            SubcktPort(name="vinp", role="inp"),
            SubcktPort(name="vinn", role="inn"),
            SubcktPort(name="vout", role="out"),
            SubcktPort(name="vbias", role="bias_tail"),
            SubcktPort(name="vdd", role="vdd"),
            SubcktPort(name="0", role="vss"),
        ],
    )


def _meta(pid: str) -> Meta:
    return Meta(id=pid, nl_spec=None)


def _corner_tt27() -> Corner:
    return Corner(process="TT", temperature_celsius=27.0)


# ───────────────────── analysis-model positive paths ──────────────────


def test_tran_analysis_roundtrip() -> None:
    a = TranAnalysis(id="tr1", t_step=1e-9, t_stop=1e-6)
    assert a.type is AnalysisType.TRAN
    revived = TranAnalysis.model_validate_json(a.model_dump_json())
    assert revived == a


def test_dc_analysis_op_only() -> None:
    a = DcAnalysis(id="dc_op")
    assert a.type is AnalysisType.DC
    assert a.sweep_source_role is None


def test_dc_analysis_sweep_roundtrip() -> None:
    a = DcAnalysis(
        id="dc_sw",
        sweep_source_role="inp",
        sweep_start=-0.1,
        sweep_stop=0.1,
        sweep_step=0.001,
    )
    revived = DcAnalysis.model_validate_json(a.model_dump_json())
    assert revived == a


def test_noise_analysis_roundtrip() -> None:
    a = NoiseAnalysis(
        id="ns1",
        output_role="out",
        input_stimulus_id="ac_in",
        f_start=1.0,
        f_stop=1e9,
    )
    revived = NoiseAnalysis.model_validate_json(a.model_dump_json())
    assert revived == a


def test_discriminated_union_dispatches_by_type() -> None:
    """A TestPlan with mixed analyses should pick the right subclass for each."""
    plan = TestPlan(
        meta=_meta("u"),
        dut=_mini_dut(),
        analyses=[
            AcAnalysis(id="ac1", f_start=1.0, f_stop=1e9),
            TranAnalysis(id="tr1", t_step=1e-9, t_stop=1e-6),
            DcAnalysis(id="dc1"),
        ],
        measurements=[
            Measurement(
                id="m_gain",
                from_analysis="ac1",
                primitive=MeasurementPrimitive.AC_LOW_FREQ_ASYMPTOTE,
                output_unit="dB",
            )
        ],
        corners=[_corner_tt27()],
    )
    types = [a.type for a in plan.analyses]
    assert types == [AnalysisType.AC, AnalysisType.TRAN, AnalysisType.DC]


# ──────────────────── analysis-model negative paths ────────────────────


def test_tran_t_stop_must_exceed_t_start() -> None:
    with pytest.raises(ValueError, match="t_stop"):
        TranAnalysis(id="bad", t_step=1e-9, t_stop=1e-6, t_start=2e-6)


def test_dc_partial_sweep_rejected() -> None:
    with pytest.raises(ValueError, match="all set together"):
        DcAnalysis(id="bad", sweep_source_role="inp", sweep_start=0.0)


def test_dc_sweep_step_zero_rejected() -> None:
    with pytest.raises(ValueError, match="sweep_step"):
        DcAnalysis(
            id="bad",
            sweep_source_role="inp",
            sweep_start=-0.1,
            sweep_stop=0.1,
            sweep_step=0.0,
        )


def test_dc_sweep_step_sign_inconsistent_rejected() -> None:
    with pytest.raises(ValueError, match="sweep_step sign"):
        DcAnalysis(
            id="bad",
            sweep_source_role="inp",
            sweep_start=0.1,   # higher than stop
            sweep_stop=-0.1,
            sweep_step=0.001,  # positive step → goes wrong way
        )


def test_noise_f_stop_must_exceed_f_start() -> None:
    with pytest.raises(ValueError, match="f_stop"):
        NoiseAnalysis(
            id="bad",
            output_role="out",
            input_stimulus_id="ac_in",
            f_start=1e9,
            f_stop=1.0,
        )


# ───────────────────── stimulus kind positive paths ───────────────────


def test_stimulus_tran_pulse_valid() -> None:
    s = Stimulus(
        id="pulse_in",
        kind=StimulusKind.TRAN_PULSE,
        ports=["inp"],
        pulse=TranPulseParams(v1=0.0, v2=1.0, pw=1e-6, per=2e-6),
    )
    revived = Stimulus.model_validate_json(s.model_dump_json())
    assert revived == s


def test_stimulus_tran_sine_valid() -> None:
    s = Stimulus(
        id="sin_in",
        kind=StimulusKind.TRAN_SINE,
        ports=["inp"],
        sine=TranSineParams(amplitude=0.1, freq=1e3),
    )
    assert s.sine.amplitude == 0.1


def test_stimulus_tran_step_valid() -> None:
    s = Stimulus(
        id="step_in",
        kind=StimulusKind.TRAN_STEP,
        ports=["inp"],
        step=TranStepParams(v1=0.0, v2=1.0, t_step=1e-9),
    )
    assert s.step.v2 == 1.0


def test_stimulus_dc_sweep_source_no_inline_params() -> None:
    s = Stimulus(
        id="sweep_in",
        kind=StimulusKind.DC_SWEEP_SOURCE,
        ports=["inp"],
    )
    assert s.magnitude is None and s.pulse is None


# ──────────────────── stimulus kind negative paths ────────────────────


def test_stimulus_pulse_kind_without_pulse_params_rejected() -> None:
    with pytest.raises(ValueError, match="must set 'pulse'"):
        Stimulus(id="bad", kind=StimulusKind.TRAN_PULSE, ports=["inp"])


def test_stimulus_pulse_kind_with_extra_magnitude_rejected() -> None:
    with pytest.raises(ValueError, match="must not set 'magnitude'"):
        Stimulus(
            id="bad",
            kind=StimulusKind.TRAN_PULSE,
            ports=["inp"],
            pulse=TranPulseParams(v1=0.0, v2=1.0, pw=1e-6, per=2e-6),
            magnitude=0.1,
        )


def test_stimulus_balanced_diff_requires_two_ports() -> None:
    with pytest.raises(ValueError, match="2 ports"):
        Stimulus(
            id="bad",
            kind=StimulusKind.BALANCED_DIFFERENTIAL_AC,
            ports=["inp"],
            magnitude=1.0,
        )


def test_stimulus_single_ended_requires_one_port() -> None:
    with pytest.raises(ValueError, match="1 port"):
        Stimulus(
            id="bad",
            kind=StimulusKind.SINGLE_ENDED_AC,
            ports=["inp", "inn"],
            magnitude=1.0,
        )


# ───────────── measurement primitive positive paths (the 14 new) ──────


@pytest.fixture
def ac_only_plan_factory():
    """Returns a builder that wraps a single measurement into a valid TestPlan."""

    def _build(measurement: Measurement, *, extra_measurements: list[Measurement] | None = None,
               extra_stimuli: list[Stimulus] | None = None) -> TestPlan:
        return TestPlan(
            meta=_meta(f"plan_{measurement.id}"),
            dut=_mini_dut(),
            analyses=[
                AcAnalysis(id="ac1", f_start=1.0, f_stop=1e9),
                TranAnalysis(id="tr1", t_step=1e-9, t_stop=1e-4),
                DcAnalysis(
                    id="dc1",
                    sweep_source_role="inp",
                    sweep_start=-0.1,
                    sweep_stop=0.1,
                    sweep_step=0.001,
                ),
                DcAnalysis(id="dc_op"),
                NoiseAnalysis(
                    id="ns1",
                    output_role="out",
                    input_stimulus_id="ac_in",
                    f_start=1.0,
                    f_stop=1e9,
                ),
            ],
            stimulus=[
                Stimulus(
                    id="ac_in",
                    kind=StimulusKind.SINGLE_ENDED_AC,
                    ports=["inp"],
                    magnitude=1.0,
                ),
                Stimulus(
                    id="pulse_in",
                    kind=StimulusKind.TRAN_PULSE,
                    ports=["inp"],
                    pulse=TranPulseParams(v1=0.0, v2=1.0, pw=5e-6, per=1e-5),
                ),
                *(extra_stimuli or []),
            ],
            measurements=[measurement, *(extra_measurements or [])],
            corners=[_corner_tt27()],
        )

    return _build


def test_ac_phase_at_freq_with_at_freq(ac_only_plan_factory) -> None:
    m = Measurement(
        id="phase_1m",
        from_analysis="ac1",
        primitive=MeasurementPrimitive.AC_PHASE_AT_FREQ,
        output_unit="deg",
        at_freq=1e6,
    )
    plan = ac_only_plan_factory(m)
    assert plan.measurements[0].at_freq == 1e6


def test_ac_phase_at_freq_with_at_when_measurement(ac_only_plan_factory) -> None:
    ugb = Measurement(
        id="ugb",
        from_analysis="ac1",
        primitive=MeasurementPrimitive.AC_FREQ_AT_MAGNITUDE_CROSSING,
        output_unit="Hz",
        target_magnitude=1.0,
        direction=CrossingDirection.FALLING,
    )
    phase = Measurement(
        id="phase_at_ugb",
        from_analysis="ac1",
        primitive=MeasurementPrimitive.AC_PHASE_AT_FREQ,
        output_unit="deg",
        at_when_measurement="ugb",
    )
    plan = ac_only_plan_factory(ugb, extra_measurements=[phase])
    assert plan.measurements[1].at_when_measurement == "ugb"


def test_ac_magnitude_at_freq(ac_only_plan_factory) -> None:
    m = Measurement(
        id="gain_100k",
        from_analysis="ac1",
        primitive=MeasurementPrimitive.AC_MAGNITUDE_AT_FREQ,
        output_unit="dB",
        at_freq=1e5,
    )
    plan = ac_only_plan_factory(m)
    assert plan.measurements[0].at_freq == 1e5


def test_ac_phase_margin(ac_only_plan_factory) -> None:
    ugb = Measurement(
        id="ugb",
        from_analysis="ac1",
        primitive=MeasurementPrimitive.AC_FREQ_AT_MAGNITUDE_CROSSING,
        output_unit="Hz",
        target_magnitude=1.0,
        direction=CrossingDirection.FALLING,
    )
    pm = Measurement(
        id="pm",
        from_analysis="ac1",
        primitive=MeasurementPrimitive.AC_PHASE_MARGIN,
        output_unit="deg",
        at_when_measurement="ugb",
    )
    plan = ac_only_plan_factory(ugb, extra_measurements=[pm])
    assert plan.measurements[1].at_when_measurement == "ugb"


def test_tran_slew_rate(ac_only_plan_factory) -> None:
    m = Measurement(
        id="sr",
        from_analysis="tr1",
        primitive=MeasurementPrimitive.TRAN_SLEW_RATE,
        output_unit="V/s",
        edge=TransitionEdge.RISING,
        window=(0.0, 5e-6),
    )
    plan = ac_only_plan_factory(m)
    assert plan.measurements[0].edge is TransitionEdge.RISING


def test_tran_settling_time(ac_only_plan_factory) -> None:
    m = Measurement(
        id="ts",
        from_analysis="tr1",
        primitive=MeasurementPrimitive.TRAN_SETTLING_TIME,
        output_unit="s",
        tolerance_pct=0.001,
        trigger_event=TriggerEvent(stimulus_id="pulse_in", edge=TransitionEdge.RISING),
    )
    plan = ac_only_plan_factory(m)
    assert plan.measurements[0].trigger_event.stimulus_id == "pulse_in"


def test_tran_overshoot_pct(ac_only_plan_factory) -> None:
    m = Measurement(
        id="os",
        from_analysis="tr1",
        primitive=MeasurementPrimitive.TRAN_OVERSHOOT_PCT,
        output_unit="%",
    )
    plan = ac_only_plan_factory(m)
    assert plan.measurements[0].primitive is MeasurementPrimitive.TRAN_OVERSHOOT_PCT


def test_tran_peak_to_peak(ac_only_plan_factory) -> None:
    m = Measurement(
        id="vpp",
        from_analysis="tr1",
        primitive=MeasurementPrimitive.TRAN_PEAK_TO_PEAK,
        output_unit="V",
        window=(1e-7, 1e-4),
    )
    plan = ac_only_plan_factory(m)
    assert plan.measurements[0].window == (1e-7, 1e-4)


def test_tran_thd(ac_only_plan_factory) -> None:
    m = Measurement(
        id="thd",
        from_analysis="tr1",
        primitive=MeasurementPrimitive.TRAN_THD,
        output_unit="%",
        fundamental_freq=1e3,
        num_harmonics=9,
    )
    plan = ac_only_plan_factory(m)
    assert plan.measurements[0].fundamental_freq == 1e3


def test_dc_offset_input_referred_midrail(ac_only_plan_factory) -> None:
    m = Measurement(
        id="vos",
        from_analysis="dc1",
        primitive=MeasurementPrimitive.DC_OFFSET_INPUT_REFERRED,
        output_unit="V",
        target_output_role="out",
        target_output_value="midrail",
    )
    plan = ac_only_plan_factory(m)
    assert plan.measurements[0].target_output_value == "midrail"


def test_dc_offset_input_referred_numeric_target(ac_only_plan_factory) -> None:
    m = Measurement(
        id="vos",
        from_analysis="dc1",
        primitive=MeasurementPrimitive.DC_OFFSET_INPUT_REFERRED,
        output_unit="V",
        target_output_role="out",
        target_output_value=0.9,
    )
    plan = ac_only_plan_factory(m)
    assert plan.measurements[0].target_output_value == 0.9


def test_dc_output_swing_range_max(ac_only_plan_factory) -> None:
    m = Measurement(
        id="vmax",
        from_analysis="dc1",
        primitive=MeasurementPrimitive.DC_OUTPUT_SWING_RANGE,
        output_unit="V",
        extreme=SwingExtreme.MAX,
    )
    plan = ac_only_plan_factory(m)
    assert plan.measurements[0].extreme is SwingExtreme.MAX


def test_dc_supply_current(ac_only_plan_factory) -> None:
    m = Measurement(
        id="iq",
        from_analysis="dc_op",
        primitive=MeasurementPrimitive.DC_SUPPLY_CURRENT,
        output_unit="A",
        supply_role="vdd",
    )
    plan = ac_only_plan_factory(m)
    assert plan.measurements[0].supply_role == "vdd"


def test_dc_gm(ac_only_plan_factory) -> None:
    m = Measurement(
        id="gm",
        from_analysis="dc1",
        primitive=MeasurementPrimitive.DC_GM,
        output_unit="S",
        input_role="inp",
        output_role="out",
        at_bias_value=0.0,
    )
    plan = ac_only_plan_factory(m)
    assert plan.measurements[0].at_bias_value == 0.0


def test_noise_input_referred_at_freq(ac_only_plan_factory) -> None:
    m = Measurement(
        id="ni_1m",
        from_analysis="ns1",
        primitive=MeasurementPrimitive.NOISE_INPUT_REFERRED_AT_FREQ,
        output_unit="V/sqrt(Hz)",
        at_freq=1e6,
    )
    plan = ac_only_plan_factory(m)
    assert plan.measurements[0].at_freq == 1e6


def test_noise_integrated_rms(ac_only_plan_factory) -> None:
    m = Measurement(
        id="ni_int",
        from_analysis="ns1",
        primitive=MeasurementPrimitive.NOISE_INTEGRATED_RMS,
        output_unit="V",
        f_low=100.0,
        f_high=1e5,
        referred_to=NoiseReferenceSide.INPUT,
    )
    plan = ac_only_plan_factory(m)
    assert plan.measurements[0].referred_to is NoiseReferenceSide.INPUT


# ────────────── measurement primitive negative paths ──────────────


def test_ac_phase_at_freq_rejects_both_at_freq_and_at_when() -> None:
    with pytest.raises(ValueError, match="exactly one"):
        Measurement(
            id="bad",
            from_analysis="ac1",
            primitive=MeasurementPrimitive.AC_PHASE_AT_FREQ,
            output_unit="deg",
            at_freq=1e6,
            at_when_measurement="ugb",
        )


def test_ac_phase_at_freq_rejects_neither() -> None:
    with pytest.raises(ValueError, match="exactly one"):
        Measurement(
            id="bad",
            from_analysis="ac1",
            primitive=MeasurementPrimitive.AC_PHASE_AT_FREQ,
            output_unit="deg",
        )


def test_tran_slew_rate_missing_edge_rejected() -> None:
    with pytest.raises(ValueError, match=r"requires field\(s\) \['edge'\]"):
        Measurement(
            id="bad",
            from_analysis="tr1",
            primitive=MeasurementPrimitive.TRAN_SLEW_RATE,
            output_unit="V/s",
        )


def test_dc_gm_missing_bias_rejected() -> None:
    with pytest.raises(ValueError, match=r"requires field"):
        Measurement(
            id="bad",
            from_analysis="dc1",
            primitive=MeasurementPrimitive.DC_GM,
            output_unit="S",
            input_role="inp",
            output_role="out",
        )


def test_noise_integrated_rms_rejects_extra_at_freq() -> None:
    with pytest.raises(ValueError, match=r"must not set field"):
        Measurement(
            id="bad",
            from_analysis="ns1",
            primitive=MeasurementPrimitive.NOISE_INTEGRATED_RMS,
            output_unit="V",
            f_low=100.0,
            f_high=1e5,
            referred_to=NoiseReferenceSide.INPUT,
            at_freq=1e3,
        )


def test_tran_overshoot_invalid_window_rejected() -> None:
    with pytest.raises(ValueError, match="window"):
        Measurement(
            id="bad",
            from_analysis="tr1",
            primitive=MeasurementPrimitive.TRAN_OVERSHOOT_PCT,
            output_unit="%",
            window=(1.0, 0.5),
        )


# ───────────────────── cross-ref / wiring tests ─────────────────────


def test_primitive_analysis_type_mismatch_rejected() -> None:
    """A TRAN primitive must not reference an AC analysis."""
    with pytest.raises(ValueError, match="TRAN analysis"):
        TestPlan(
            meta=_meta("bad"),
            dut=_mini_dut(),
            analyses=[AcAnalysis(id="ac1", f_start=1.0, f_stop=1e9)],
            measurements=[
                Measurement(
                    id="sr",
                    from_analysis="ac1",  # ← AC, but slew_rate needs TRAN
                    primitive=MeasurementPrimitive.TRAN_SLEW_RATE,
                    output_unit="V/s",
                    edge=TransitionEdge.RISING,
                )
            ],
            corners=[_corner_tt27()],
        )


def test_at_when_measurement_unknown_id_rejected() -> None:
    with pytest.raises(ValueError, match="at_when_measurement"):
        TestPlan(
            meta=_meta("bad"),
            dut=_mini_dut(),
            analyses=[AcAnalysis(id="ac1", f_start=1.0, f_stop=1e9)],
            measurements=[
                Measurement(
                    id="pm",
                    from_analysis="ac1",
                    primitive=MeasurementPrimitive.AC_PHASE_MARGIN,
                    output_unit="deg",
                    at_when_measurement="ugb_that_does_not_exist",
                )
            ],
            corners=[_corner_tt27()],
        )


def test_at_when_measurement_self_reference_rejected() -> None:
    with pytest.raises(ValueError, match="cannot reference self"):
        TestPlan(
            meta=_meta("bad"),
            dut=_mini_dut(),
            analyses=[AcAnalysis(id="ac1", f_start=1.0, f_stop=1e9)],
            measurements=[
                Measurement(
                    id="pm",
                    from_analysis="ac1",
                    primitive=MeasurementPrimitive.AC_PHASE_MARGIN,
                    output_unit="deg",
                    at_when_measurement="pm",
                )
            ],
            corners=[_corner_tt27()],
        )


def test_trigger_event_unknown_stimulus_rejected() -> None:
    with pytest.raises(ValueError, match="trigger_event.stimulus_id"):
        TestPlan(
            meta=_meta("bad"),
            dut=_mini_dut(),
            analyses=[TranAnalysis(id="tr1", t_step=1e-9, t_stop=1e-4)],
            measurements=[
                Measurement(
                    id="ts",
                    from_analysis="tr1",
                    primitive=MeasurementPrimitive.TRAN_SETTLING_TIME,
                    output_unit="s",
                    tolerance_pct=0.001,
                    trigger_event=TriggerEvent(
                        stimulus_id="ghost", edge=TransitionEdge.RISING
                    ),
                )
            ],
            corners=[_corner_tt27()],
        )


def test_dc_supply_role_must_be_in_dut() -> None:
    with pytest.raises(ValueError, match="supply_role"):
        TestPlan(
            meta=_meta("bad"),
            dut=_mini_dut(),
            analyses=[DcAnalysis(id="dc_op")],
            measurements=[
                Measurement(
                    id="iq",
                    from_analysis="dc_op",
                    primitive=MeasurementPrimitive.DC_SUPPLY_CURRENT,
                    output_unit="A",
                    supply_role="vcc_does_not_exist",
                )
            ],
            corners=[_corner_tt27()],
        )


def test_dc_analysis_sweep_role_must_be_in_dut() -> None:
    with pytest.raises(ValueError, match="sweep_source_role"):
        TestPlan(
            meta=_meta("bad"),
            dut=_mini_dut(),
            analyses=[
                DcAnalysis(
                    id="dc_sw",
                    sweep_source_role="phantom",
                    sweep_start=0.0,
                    sweep_stop=1.0,
                    sweep_step=0.01,
                )
            ],
            measurements=[
                Measurement(
                    id="m",
                    from_analysis="dc_sw",
                    primitive=MeasurementPrimitive.DC_OUTPUT_SWING_RANGE,
                    output_unit="V",
                    extreme=SwingExtreme.MAX,
                )
            ],
            corners=[_corner_tt27()],
        )


def test_noise_analysis_input_stimulus_must_exist() -> None:
    with pytest.raises(ValueError, match="input_stimulus_id"):
        TestPlan(
            meta=_meta("bad"),
            dut=_mini_dut(),
            analyses=[
                NoiseAnalysis(
                    id="ns1",
                    output_role="out",
                    input_stimulus_id="ghost",
                    f_start=1.0,
                    f_stop=1e6,
                )
            ],
            stimulus=[],
            measurements=[
                Measurement(
                    id="ni",
                    from_analysis="ns1",
                    primitive=MeasurementPrimitive.NOISE_INPUT_REFERRED_AT_FREQ,
                    output_unit="V/sqrt(Hz)",
                    at_freq=1e3,
                )
            ],
            corners=[_corner_tt27()],
        )


# ───────────────────── semantic equivalence on extended IR ─────────────


def test_semantic_equivalence_ignores_measurement_order_on_extended_plan() -> None:
    """Two measurements on the same plan in different orders must be equivalent."""
    base_args = dict(
        meta=_meta("seq"),
        dut=_mini_dut(),
        analyses=[
            AcAnalysis(id="ac1", f_start=1.0, f_stop=1e9),
            TranAnalysis(id="tr1", t_step=1e-9, t_stop=1e-4),
        ],
        stimulus=[
            Stimulus(
                id="ac_in",
                kind=StimulusKind.SINGLE_ENDED_AC,
                ports=["inp"],
                magnitude=1.0,
            ),
        ],
        corners=[_corner_tt27()],
    )
    m_gain = Measurement(
        id="g",
        from_analysis="ac1",
        primitive=MeasurementPrimitive.AC_LOW_FREQ_ASYMPTOTE,
        output_unit="dB",
    )
    m_pp = Measurement(
        id="vpp",
        from_analysis="tr1",
        primitive=MeasurementPrimitive.TRAN_PEAK_TO_PEAK,
        output_unit="V",
    )
    p1 = TestPlan(measurements=[m_gain, m_pp], **base_args)
    p2 = TestPlan(measurements=[m_pp, m_gain], **base_args)
    assert semantic_equivalent(p1, p2)
    assert canonical_form(p1) == canonical_form(p2)


def test_full_plan_using_many_primitives_roundtrip() -> None:
    """End-to-end smoke: a TestPlan touching every analysis type round-trips."""
    plan = TestPlan(
        meta=_meta("multi"),
        dut=_mini_dut(),
        analyses=[
            AcAnalysis(id="ac1", f_start=1.0, f_stop=1e9),
            TranAnalysis(id="tr1", t_step=1e-9, t_stop=1e-4),
            DcAnalysis(
                id="dc_sw",
                sweep_source_role="inp",
                sweep_start=-0.1,
                sweep_stop=0.1,
                sweep_step=0.001,
            ),
            DcAnalysis(id="dc_op"),
            NoiseAnalysis(
                id="ns1",
                output_role="out",
                input_stimulus_id="ac_in",
                f_start=1.0,
                f_stop=1e9,
            ),
        ],
        stimulus=[
            Stimulus(
                id="ac_in",
                kind=StimulusKind.BALANCED_DIFFERENTIAL_AC,
                ports=["inp", "inn"],
                magnitude=1.0,
            ),
            Stimulus(
                id="pulse_in",
                kind=StimulusKind.TRAN_PULSE,
                ports=["inp"],
                pulse=TranPulseParams(v1=0.0, v2=1.0, pw=5e-6, per=1e-5),
                scope=Scope.ANALYSIS,
                scope_analysis_id="tr1",
            ),
        ],
        measurements=[
            Measurement(
                id="g_dc",
                from_analysis="ac1",
                primitive=MeasurementPrimitive.AC_LOW_FREQ_ASYMPTOTE,
                output_unit="dB",
            ),
            Measurement(
                id="sr",
                from_analysis="tr1",
                primitive=MeasurementPrimitive.TRAN_SLEW_RATE,
                output_unit="V/s",
                edge=TransitionEdge.RISING,
            ),
            Measurement(
                id="iq",
                from_analysis="dc_op",
                primitive=MeasurementPrimitive.DC_SUPPLY_CURRENT,
                output_unit="A",
                supply_role="vdd",
            ),
            Measurement(
                id="ni",
                from_analysis="ns1",
                primitive=MeasurementPrimitive.NOISE_INPUT_REFERRED_AT_FREQ,
                output_unit="V/sqrt(Hz)",
                at_freq=1e6,
            ),
        ],
        pass_criteria=[
            PassCriterion(measurement="g_dc", op=ComparisonOp.GT, value=60.0, spec_unit="dB"),
            PassCriterion(measurement="sr", op=ComparisonOp.GT, value=1e7, spec_unit="V/us"),
            PassCriterion(measurement="iq", op=ComparisonOp.LE, value=1e-4, spec_unit="A"),
            PassCriterion(
                measurement="ni", op=ComparisonOp.LT, value=1e-8,
                spec_unit="V/sqrt(Hz)",
                tolerance=Tolerance(rel=0.1),
            ),
        ],
        corners=[_corner_tt27()],
    )
    revived = TestPlan.model_validate_json(plan.model_dump_json())
    assert semantic_equivalent(plan, revived)


# ─────────────── unused-import keep-happy ───────────────
_ = (Tolerance, LoadingKind, Scope, AnalysisType)
