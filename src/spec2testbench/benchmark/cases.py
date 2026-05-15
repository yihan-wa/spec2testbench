"""Hand-curated benchmark cases for Stage-1 (NL → IR) extraction.

Distribution by analysis type: AC 7 + TRAN 6 + DC 5 + NOISE 2 = 20.
Each case is a (NL spec, DUT metadata, gold-IR builder) triple.

All 20 cases share the same 5-transistor differential-pair OTA DUT — only the
NL spec and required measurements vary. This is deliberate experimental design:
the variable we want to measure is the extractor's NL → IR accuracy, not its
ability to handle multiple netlist topologies.

Cases are authored such that:
- every primitive is exercised at least once (most twice or more)
- every stimulus kind that targets a v0 analysis is exercised
- single-corner and multi-corner specs both appear
- different NL phrasings of the same semantic (e.g. ≥ vs minimum vs at least)
  appear so the extractor's operator selection is stressed
- units span dB, Hz / MHz, V / mV, A / uA, s / ns, V/s / V/us, V/sqrt(Hz)
"""

from __future__ import annotations

from dataclasses import dataclass
from typing import Callable

from spec2testbench.extract import DutMetadata
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
    NoiseReferenceSide,
    PassCriterion,
    Scope,
    Stimulus,
    StimulusKind,
    SubcktPort,
    SwingExtreme,
    TestPlan,
    TranAnalysis,
    TranPulseParams,
    TranSineParams,
    TranStepParams,
    TransitionEdge,
    TriggerEvent,
)


# ──────────────────── shared DUT ────────────────────

_DUT_SUBCKT_PORTS = (
    ("vinp", "inp"),
    ("vinn", "inn"),
    ("vout", "out"),
    ("vbias", "bias_tail"),
    ("vdd", "vdd"),
    ("0", "vss"),
)

_DUT_METADATA = DutMetadata(
    name="diff_pair_ota_5t",
    netlist_path="examples/01_diff_pair_ota/dut.cir",
    subckt_ports=_DUT_SUBCKT_PORTS,
)


def _dut_ir() -> Dut:
    return Dut(
        name=_DUT_METADATA.name,
        netlist_path=_DUT_METADATA.netlist_path,
        subckt_ports=[SubcktPort(name=n, role=r) for n, r in _DUT_SUBCKT_PORTS],
    )


def _tt27() -> Corner:
    return Corner(process="TT", temperature_celsius=27.0)


def _cap_load_1p() -> Loading:
    return Loading(id="c_load", kind=LoadingKind.C, value="1p", from_role="out", to_role="vss")


def _cap_load_2p() -> Loading:
    return Loading(id="c_load", kind=LoadingKind.C, value="2p", from_role="out", to_role="vss")


def _diff_ac_stimulus(magnitude: float = 1.0) -> Stimulus:
    return Stimulus(
        id="diff_ac",
        kind=StimulusKind.BALANCED_DIFFERENTIAL_AC,
        ports=["inp", "inn"],
        magnitude=magnitude,
    )


def _single_ended_ac_stimulus() -> Stimulus:
    """Used as the input source for NoiseAnalysis."""
    return Stimulus(
        id="ac_in_for_noise",
        kind=StimulusKind.SINGLE_ENDED_AC,
        ports=["inp"],
        magnitude=1.0,
    )


# ──────────────────── case record ────────────────────


@dataclass(frozen=True)
class BenchmarkCase:
    case_id: str
    description: str
    nl_spec: str
    dut: DutMetadata
    gold_builder: Callable[[], TestPlan]


# ══════════════════════════════════════════════════════════════════════
#                           AC cases (7)
# ══════════════════════════════════════════════════════════════════════


_NL_A1 = (
    "We need to verify the small-signal performance of our 5-transistor "
    "differential-pair OTA. The DC gain should exceed 60 dB, and the "
    "unity-gain bandwidth should be at least 10 MHz when driving a 1 pF "
    "capacitive load on the output. Please run AC analysis from 1 Hz up "
    "to 1 GHz. TT corner at 27 °C is sufficient for now — we'll add "
    "corners later."
)


def _gold_a1() -> TestPlan:
    """Baseline case (same content as examples/01_diff_pair_ota)."""
    return TestPlan(
        meta=Meta(id="a1_diff_pair_gain_ugb", nl_spec=_NL_A1),
        dut=_dut_ir(),
        analyses=[AcAnalysis(id="ac_smallsig", f_start=1.0, f_stop=1.0e9)],
        stimulus=[_diff_ac_stimulus()],
        loading=[_cap_load_1p()],
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
        corners=[_tt27()],
    )


_NL_A2 = (
    "Verify that the diff-pair OTA has a phase margin of at least 60 degrees "
    "with a 1 pF load on the output. Use AC analysis from 1 Hz to 1 GHz. "
    "TT corner at 27 °C."
)


def _gold_a2() -> TestPlan:
    return TestPlan(
        meta=Meta(id="a2_phase_margin", nl_spec=_NL_A2),
        dut=_dut_ir(),
        analyses=[AcAnalysis(id="ac_smallsig", f_start=1.0, f_stop=1.0e9)],
        stimulus=[_diff_ac_stimulus()],
        loading=[_cap_load_1p()],
        measurements=[
            Measurement(
                id="ugb",
                from_analysis="ac_smallsig",
                primitive=MeasurementPrimitive.AC_FREQ_AT_MAGNITUDE_CROSSING,
                on_role="out",
                output_unit="Hz",
                target_magnitude=1.0,
                direction=CrossingDirection.FALLING,
            ),
            Measurement(
                id="pm",
                from_analysis="ac_smallsig",
                primitive=MeasurementPrimitive.AC_PHASE_MARGIN,
                on_role="out",
                output_unit="deg",
                at_when_measurement="ugb",
            ),
        ],
        pass_criteria=[
            PassCriterion(measurement="pm", op=ComparisonOp.GE, value=60.0, spec_unit="deg"),
        ],
        corners=[_tt27()],
    )


_NL_A3 = (
    "At 100 kHz the differential OTA must have at least 40 dB of voltage gain "
    "and the phase response at the same frequency must not lag beyond −45°. "
    "Run AC analysis from 1 Hz to 100 MHz with a 1 pF load. TT corner, 27 °C."
)


def _gold_a3() -> TestPlan:
    return TestPlan(
        meta=Meta(id="a3_gain_at_100k", nl_spec=_NL_A3),
        dut=_dut_ir(),
        analyses=[AcAnalysis(id="ac_smallsig", f_start=1.0, f_stop=1.0e8)],
        stimulus=[_diff_ac_stimulus()],
        loading=[_cap_load_1p()],
        measurements=[
            Measurement(
                id="gain_100k",
                from_analysis="ac_smallsig",
                primitive=MeasurementPrimitive.AC_MAGNITUDE_AT_FREQ,
                on_role="out",
                output_unit="dB",
                at_freq=1.0e5,
            ),
            Measurement(
                id="phase_100k",
                from_analysis="ac_smallsig",
                primitive=MeasurementPrimitive.AC_PHASE_AT_FREQ,
                on_role="out",
                output_unit="deg",
                at_freq=1.0e5,
            ),
        ],
        pass_criteria=[
            PassCriterion(
                measurement="gain_100k", op=ComparisonOp.GE, value=40.0, spec_unit="dB"
            ),
            PassCriterion(
                measurement="phase_100k", op=ComparisonOp.GE, value=-45.0, spec_unit="deg"
            ),
        ],
        corners=[_tt27()],
    )


_NL_A4 = (
    "Locate the −3 dB high-frequency rolloff of our diff-pair amp. Given a DC "
    "gain of 60 dB (linear ≈ 1000), the −3 dB crossing should occur at a "
    "frequency of at least 1 MHz. AC sweep 1 Hz to 1 GHz, 1 pF load on output, "
    "TT 27 °C."
)


def _gold_a4() -> TestPlan:
    # −3 dB below 60 dB DC gain ⇒ linear target = 1000 · 10^(−3/20) ≈ 707.946
    target = 1000.0 * (10.0 ** (-3.0 / 20.0))
    return TestPlan(
        meta=Meta(id="a4_minus_3db_corner", nl_spec=_NL_A4),
        dut=_dut_ir(),
        analyses=[AcAnalysis(id="ac_smallsig", f_start=1.0, f_stop=1.0e9)],
        stimulus=[_diff_ac_stimulus()],
        loading=[_cap_load_1p()],
        measurements=[
            Measurement(
                id="f_minus_3db",
                from_analysis="ac_smallsig",
                primitive=MeasurementPrimitive.AC_FREQ_AT_MAGNITUDE_CROSSING,
                on_role="out",
                output_unit="Hz",
                target_magnitude=target,
                direction=CrossingDirection.FALLING,
            ),
        ],
        pass_criteria=[
            PassCriterion(
                measurement="f_minus_3db", op=ComparisonOp.GE, value=1.0e6, spec_unit="Hz"
            ),
        ],
        corners=[_tt27()],
    )


_NL_A5 = (
    "DC gain shall strictly exceed 65 dB; the unity-gain bandwidth must be no "
    "less than 20 MHz. AC sweep from 1 Hz to 1 GHz, 1 pF capacitive load, TT "
    "27 °C. Anything failing either threshold is unacceptable."
)


def _gold_a5() -> TestPlan:
    return TestPlan(
        meta=Meta(id="a5_strict_ops", nl_spec=_NL_A5),
        dut=_dut_ir(),
        analyses=[AcAnalysis(id="ac_smallsig", f_start=1.0, f_stop=1.0e9)],
        stimulus=[_diff_ac_stimulus()],
        loading=[_cap_load_1p()],
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
            PassCriterion(measurement="dc_gain", op=ComparisonOp.GT, value=65.0, spec_unit="dB"),
            PassCriterion(measurement="ugb", op=ComparisonOp.GE, value=2.0e7, spec_unit="Hz"),
        ],
        corners=[_tt27()],
    )


_NL_A6 = (
    "Open-loop gain should exceed 55 dB at low frequency, with a unity-gain "
    "bandwidth of at least 15 MHz. Use a 2 pF load and AC analysis from 10 Hz "
    "to 500 MHz. TT corner, 27 °C."
)


def _gold_a6() -> TestPlan:
    return TestPlan(
        meta=Meta(id="a6_units_mhz_phrasing", nl_spec=_NL_A6),
        dut=_dut_ir(),
        analyses=[AcAnalysis(id="ac_smallsig", f_start=10.0, f_stop=5.0e8)],
        stimulus=[_diff_ac_stimulus()],
        loading=[_cap_load_2p()],
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
            PassCriterion(measurement="dc_gain", op=ComparisonOp.GT, value=55.0, spec_unit="dB"),
            PassCriterion(measurement="ugb", op=ComparisonOp.GE, value=15.0, spec_unit="MHz"),
        ],
        corners=[_tt27()],
    )


_NL_A7 = (
    "Across TT at 27 °C and SS at −40 °C corners, the diff-pair OTA must "
    "achieve DC gain at least 60 dB and UGB at least 10 MHz. AC 1 Hz to 1 GHz, "
    "1 pF load."
)


def _gold_a7() -> TestPlan:
    return TestPlan(
        meta=Meta(id="a7_two_corners", nl_spec=_NL_A7),
        dut=_dut_ir(),
        analyses=[AcAnalysis(id="ac_smallsig", f_start=1.0, f_stop=1.0e9)],
        stimulus=[_diff_ac_stimulus()],
        loading=[_cap_load_1p()],
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
            PassCriterion(measurement="dc_gain", op=ComparisonOp.GE, value=60.0, spec_unit="dB"),
            PassCriterion(measurement="ugb", op=ComparisonOp.GE, value=1.0e7, spec_unit="Hz"),
        ],
        corners=[
            Corner(process="TT", temperature_celsius=27.0),
            Corner(process="SS", temperature_celsius=-40.0),
        ],
    )


# ══════════════════════════════════════════════════════════════════════
#                          TRAN cases (6)
# ══════════════════════════════════════════════════════════════════════


_NL_T1 = (
    "Apply a 0 V → 1 V pulse to the differential input of the diff-pair OTA "
    "(1 ns rise time, 5 μs pulse width, 10 μs period) and verify the "
    "rising-edge slew rate exceeds 10 V/μs. Simulate 20 μs at 10 ns step. "
    "TT 27 °C, 1 pF load on output."
)


def _gold_t1() -> TestPlan:
    return TestPlan(
        meta=Meta(id="t1_slew_rate_rising", nl_spec=_NL_T1),
        dut=_dut_ir(),
        analyses=[TranAnalysis(id="tran_step", t_step=1e-8, t_stop=2e-5)],
        stimulus=[
            Stimulus(
                id="pulse_diff",
                kind=StimulusKind.TRAN_PULSE,
                ports=["inp"],
                pulse=TranPulseParams(v1=0.0, v2=1.0, tr=1e-9, tf=1e-9, pw=5e-6, per=1e-5),
                scope=Scope.ANALYSIS,
                scope_analysis_id="tran_step",
            ),
        ],
        loading=[_cap_load_1p()],
        measurements=[
            Measurement(
                id="sr_rising",
                from_analysis="tran_step",
                primitive=MeasurementPrimitive.TRAN_SLEW_RATE,
                on_role="out",
                output_unit="V/s",
                edge=TransitionEdge.RISING,
            ),
        ],
        pass_criteria=[
            PassCriterion(
                measurement="sr_rising", op=ComparisonOp.GT, value=10.0, spec_unit="V/us"
            ),
        ],
        corners=[_tt27()],
    )


_NL_T2 = (
    "Apply a 0 → 0.5 V step at the differential input at t = 100 ns "
    "(100 ps rise time). The output must settle to within ±0.1% of its final "
    "value within 200 ns of the trigger edge. Simulate 1 μs at 1 ns step. "
    "1 pF load, TT 27 °C."
)


def _gold_t2() -> TestPlan:
    return TestPlan(
        meta=Meta(id="t2_settling_time", nl_spec=_NL_T2),
        dut=_dut_ir(),
        analyses=[TranAnalysis(id="tran_step", t_step=1e-9, t_stop=1e-6)],
        stimulus=[
            Stimulus(
                id="step_diff",
                kind=StimulusKind.TRAN_STEP,
                ports=["inp"],
                step=TranStepParams(v1=0.0, v2=0.5, t_step=1e-7, tr=1e-10),
                scope=Scope.ANALYSIS,
                scope_analysis_id="tran_step",
            ),
        ],
        loading=[_cap_load_1p()],
        measurements=[
            Measurement(
                id="ts",
                from_analysis="tran_step",
                primitive=MeasurementPrimitive.TRAN_SETTLING_TIME,
                on_role="out",
                output_unit="s",
                tolerance_pct=0.001,
                trigger_event=TriggerEvent(
                    stimulus_id="step_diff", edge=TransitionEdge.RISING
                ),
            ),
        ],
        pass_criteria=[
            PassCriterion(measurement="ts", op=ComparisonOp.LT, value=200.0, spec_unit="ns"),
        ],
        corners=[_tt27()],
    )


_NL_T3 = (
    "On the same 0 → 0.5 V step response (1 pF load), the output overshoot "
    "must remain below 5%. Simulate 1 μs at 1 ns step, TT 27 °C."
)


def _gold_t3() -> TestPlan:
    return TestPlan(
        meta=Meta(id="t3_overshoot", nl_spec=_NL_T3),
        dut=_dut_ir(),
        analyses=[TranAnalysis(id="tran_step", t_step=1e-9, t_stop=1e-6)],
        stimulus=[
            Stimulus(
                id="step_diff",
                kind=StimulusKind.TRAN_STEP,
                ports=["inp"],
                step=TranStepParams(v1=0.0, v2=0.5, t_step=1e-7, tr=1e-10),
                scope=Scope.ANALYSIS,
                scope_analysis_id="tran_step",
            ),
        ],
        loading=[_cap_load_1p()],
        measurements=[
            Measurement(
                id="overshoot",
                from_analysis="tran_step",
                primitive=MeasurementPrimitive.TRAN_OVERSHOOT_PCT,
                on_role="out",
                output_unit="%",
            ),
        ],
        pass_criteria=[
            PassCriterion(
                measurement="overshoot", op=ComparisonOp.LT, value=5.0, spec_unit="%"
            ),
        ],
        corners=[_tt27()],
    )


_NL_T4 = (
    "Drive the diff-pair OTA with a 100 kHz, 0.8 V amplitude single-tone "
    "sine input on the positive leg and confirm the peak-to-peak output "
    "voltage is at least 1.5 V. Simulate 100 μs at 100 ns step. TT 27 °C, "
    "1 pF load."
)


def _gold_t4() -> TestPlan:
    return TestPlan(
        meta=Meta(id="t4_peak_to_peak_swing", nl_spec=_NL_T4),
        dut=_dut_ir(),
        analyses=[TranAnalysis(id="tran_sine", t_step=1e-7, t_stop=1e-4)],
        stimulus=[
            Stimulus(
                id="sin_in",
                kind=StimulusKind.TRAN_SINE,
                ports=["inp"],
                sine=TranSineParams(dc_offset=0.0, amplitude=0.8, freq=1.0e5),
                scope=Scope.ANALYSIS,
                scope_analysis_id="tran_sine",
            ),
        ],
        loading=[_cap_load_1p()],
        measurements=[
            Measurement(
                id="vpp",
                from_analysis="tran_sine",
                primitive=MeasurementPrimitive.TRAN_PEAK_TO_PEAK,
                on_role="out",
                output_unit="V",
            ),
        ],
        pass_criteria=[
            PassCriterion(measurement="vpp", op=ComparisonOp.GE, value=1.5, spec_unit="V"),
        ],
        corners=[_tt27()],
    )


_NL_T5 = (
    "At a 1 kHz single-tone input with 0.5 V amplitude, total harmonic "
    "distortion (first 9 harmonics) must be below 1%. Simulate 5 ms at "
    "1 μs step. TT 27 °C, 1 pF load."
)


def _gold_t5() -> TestPlan:
    return TestPlan(
        meta=Meta(id="t5_thd_1k", nl_spec=_NL_T5),
        dut=_dut_ir(),
        analyses=[TranAnalysis(id="tran_thd", t_step=1e-6, t_stop=5e-3)],
        stimulus=[
            Stimulus(
                id="sin_in",
                kind=StimulusKind.TRAN_SINE,
                ports=["inp"],
                sine=TranSineParams(dc_offset=0.0, amplitude=0.5, freq=1.0e3),
                scope=Scope.ANALYSIS,
                scope_analysis_id="tran_thd",
            ),
        ],
        loading=[_cap_load_1p()],
        measurements=[
            Measurement(
                id="thd",
                from_analysis="tran_thd",
                primitive=MeasurementPrimitive.TRAN_THD,
                on_role="out",
                output_unit="%",
                fundamental_freq=1.0e3,
                num_harmonics=9,
            ),
        ],
        pass_criteria=[
            PassCriterion(measurement="thd", op=ComparisonOp.LT, value=1.0, spec_unit="%"),
        ],
        corners=[_tt27()],
    )


_NL_T6 = (
    "Drive the diff-pair OTA with a 1 MHz, 50% duty-cycle square-wave input "
    "swinging 0 V to 1.2 V (rise/fall time 1 ns). The slew rate on both "
    "rising and falling edges must exceed 8 V/μs. Simulate 10 μs at 1 ns "
    "step, TT 27 °C, 1 pF load."
)


def _gold_t6() -> TestPlan:
    return TestPlan(
        meta=Meta(id="t6_slew_rate_both_edges", nl_spec=_NL_T6),
        dut=_dut_ir(),
        analyses=[TranAnalysis(id="tran_sq", t_step=1e-9, t_stop=1e-5)],
        stimulus=[
            Stimulus(
                id="sq_in",
                kind=StimulusKind.TRAN_PULSE,
                ports=["inp"],
                pulse=TranPulseParams(v1=0.0, v2=1.2, tr=1e-9, tf=1e-9, pw=5e-7, per=1e-6),
                scope=Scope.ANALYSIS,
                scope_analysis_id="tran_sq",
            ),
        ],
        loading=[_cap_load_1p()],
        measurements=[
            Measurement(
                id="sr",
                from_analysis="tran_sq",
                primitive=MeasurementPrimitive.TRAN_SLEW_RATE,
                on_role="out",
                output_unit="V/s",
                edge=TransitionEdge.BOTH,
            ),
        ],
        pass_criteria=[
            PassCriterion(measurement="sr", op=ComparisonOp.GT, value=8.0, spec_unit="V/us"),
        ],
        corners=[_tt27()],
    )


# ══════════════════════════════════════════════════════════════════════
#                            DC cases (5)
# ══════════════════════════════════════════════════════════════════════


_NL_D1 = (
    "Sweep the differential input from −10 mV to +10 mV in 100 μV steps and "
    "extract the input-referred offset (i.e., the input voltage at which the "
    "output crosses mid-rail). The offset must be below 5 mV in magnitude. "
    "TT 27 °C, 1 pF load."
)


def _gold_d1() -> TestPlan:
    return TestPlan(
        meta=Meta(id="d1_input_referred_offset", nl_spec=_NL_D1),
        dut=_dut_ir(),
        analyses=[
            DcAnalysis(
                id="dc_sweep_in",
                sweep_source_role="inp",
                sweep_start=-0.010,
                sweep_stop=0.010,
                sweep_step=0.0001,
            )
        ],
        stimulus=[
            Stimulus(
                id="sweep_in",
                kind=StimulusKind.DC_SWEEP_SOURCE,
                ports=["inp"],
                scope=Scope.ANALYSIS,
                scope_analysis_id="dc_sweep_in",
            ),
        ],
        loading=[_cap_load_1p()],
        measurements=[
            Measurement(
                id="vos",
                from_analysis="dc_sweep_in",
                primitive=MeasurementPrimitive.DC_OFFSET_INPUT_REFERRED,
                on_role="out",
                output_unit="V",
                target_output_role="out",
                target_output_value="midrail",
            ),
        ],
        pass_criteria=[
            PassCriterion(measurement="vos", op=ComparisonOp.LT, value=5.0, spec_unit="mV"),
        ],
        corners=[_tt27()],
    )


_NL_D2 = (
    "Sweep the differential input from 0 V to 1.8 V in 10 mV steps and "
    "verify the output swing range (max minus min) exceeds 1.5 V. "
    "TT 27 °C, 1 pF load."
)


def _gold_d2() -> TestPlan:
    return TestPlan(
        meta=Meta(id="d2_output_swing_range", nl_spec=_NL_D2),
        dut=_dut_ir(),
        analyses=[
            DcAnalysis(
                id="dc_sweep_in",
                sweep_source_role="inp",
                sweep_start=0.0,
                sweep_stop=1.8,
                sweep_step=0.010,
            )
        ],
        stimulus=[
            Stimulus(
                id="sweep_in",
                kind=StimulusKind.DC_SWEEP_SOURCE,
                ports=["inp"],
                scope=Scope.ANALYSIS,
                scope_analysis_id="dc_sweep_in",
            ),
        ],
        loading=[_cap_load_1p()],
        measurements=[
            Measurement(
                id="swing",
                from_analysis="dc_sweep_in",
                primitive=MeasurementPrimitive.DC_OUTPUT_SWING_RANGE,
                on_role="out",
                output_unit="V",
                extreme=SwingExtreme.RANGE,
            ),
        ],
        pass_criteria=[
            PassCriterion(measurement="swing", op=ComparisonOp.GT, value=1.5, spec_unit="V"),
        ],
        corners=[_tt27()],
    )


_NL_D3 = (
    "At the quiescent operating point (no input swing), the supply current "
    "drawn from VDD must remain at most 100 μA. TT 27 °C."
)


def _gold_d3() -> TestPlan:
    return TestPlan(
        meta=Meta(id="d3_quiescent_current", nl_spec=_NL_D3),
        dut=_dut_ir(),
        analyses=[DcAnalysis(id="dc_op")],
        stimulus=[],
        loading=[],
        measurements=[
            Measurement(
                id="iq",
                from_analysis="dc_op",
                primitive=MeasurementPrimitive.DC_SUPPLY_CURRENT,
                on_role="out",
                output_unit="A",
                supply_role="vdd",
            ),
        ],
        pass_criteria=[
            PassCriterion(measurement="iq", op=ComparisonOp.LE, value=100.0, spec_unit="uA"),
        ],
        corners=[_tt27()],
    )


_NL_D4 = (
    "At zero differential-input bias, the small-signal transconductance from "
    "the positive input to the output must exceed 500 μS. Use a fine DC sweep "
    "around 0 V (1 mV steps from −5 mV to +5 mV). TT 27 °C, 1 pF load."
)


def _gold_d4() -> TestPlan:
    return TestPlan(
        meta=Meta(id="d4_gm_at_zero", nl_spec=_NL_D4),
        dut=_dut_ir(),
        analyses=[
            DcAnalysis(
                id="dc_fine_sweep",
                sweep_source_role="inp",
                sweep_start=-0.005,
                sweep_stop=0.005,
                sweep_step=0.001,
            )
        ],
        stimulus=[
            Stimulus(
                id="sweep_in",
                kind=StimulusKind.DC_SWEEP_SOURCE,
                ports=["inp"],
                scope=Scope.ANALYSIS,
                scope_analysis_id="dc_fine_sweep",
            ),
        ],
        loading=[_cap_load_1p()],
        measurements=[
            Measurement(
                id="gm",
                from_analysis="dc_fine_sweep",
                primitive=MeasurementPrimitive.DC_GM,
                on_role="out",
                output_unit="S",
                input_role="inp",
                output_role="out",
                at_bias_value=0.0,
            ),
        ],
        pass_criteria=[
            PassCriterion(measurement="gm", op=ComparisonOp.GT, value=500.0, spec_unit="uS"),
        ],
        corners=[_tt27()],
    )


_NL_D5 = (
    "Across TT 27 °C and FF 85 °C corners, the maximum output voltage "
    "achievable on the diff-pair OTA must be at least 1.6 V. Sweep the "
    "differential input from 0 V to 1.8 V in 10 mV steps, 1 pF load."
)


def _gold_d5() -> TestPlan:
    return TestPlan(
        meta=Meta(id="d5_vout_max_corners", nl_spec=_NL_D5),
        dut=_dut_ir(),
        analyses=[
            DcAnalysis(
                id="dc_sweep_in",
                sweep_source_role="inp",
                sweep_start=0.0,
                sweep_stop=1.8,
                sweep_step=0.010,
            )
        ],
        stimulus=[
            Stimulus(
                id="sweep_in",
                kind=StimulusKind.DC_SWEEP_SOURCE,
                ports=["inp"],
                scope=Scope.ANALYSIS,
                scope_analysis_id="dc_sweep_in",
            ),
        ],
        loading=[_cap_load_1p()],
        measurements=[
            Measurement(
                id="vout_max",
                from_analysis="dc_sweep_in",
                primitive=MeasurementPrimitive.DC_OUTPUT_SWING_RANGE,
                on_role="out",
                output_unit="V",
                extreme=SwingExtreme.MAX,
            ),
        ],
        pass_criteria=[
            PassCriterion(measurement="vout_max", op=ComparisonOp.GE, value=1.6, spec_unit="V"),
        ],
        corners=[
            Corner(process="TT", temperature_celsius=27.0),
            Corner(process="FF", temperature_celsius=85.0),
        ],
    )


# ══════════════════════════════════════════════════════════════════════
#                          NOISE cases (2)
# ══════════════════════════════════════════════════════════════════════


_NL_N1 = (
    "Input-referred voltage-noise PSD at 1 kHz must be below 50 nV/√Hz. Run "
    "noise analysis from 0.1 Hz to 10 MHz using the AC source at the positive "
    "input as the reference. TT 27 °C, 1 pF load."
)


def _gold_n1() -> TestPlan:
    return TestPlan(
        meta=Meta(id="n1_psd_at_1k", nl_spec=_NL_N1),
        dut=_dut_ir(),
        analyses=[
            NoiseAnalysis(
                id="noise_sweep",
                output_role="out",
                input_stimulus_id="ac_in_for_noise",
                f_start=0.1,
                f_stop=1.0e7,
            )
        ],
        stimulus=[_single_ended_ac_stimulus()],
        loading=[_cap_load_1p()],
        measurements=[
            Measurement(
                id="psd_1k",
                from_analysis="noise_sweep",
                primitive=MeasurementPrimitive.NOISE_INPUT_REFERRED_AT_FREQ,
                on_role="out",
                output_unit="V/sqrt(Hz)",
                at_freq=1.0e3,
            ),
        ],
        pass_criteria=[
            PassCriterion(
                measurement="psd_1k", op=ComparisonOp.LT, value=50.0,
                spec_unit="nV/sqrt(Hz)",
            ),
        ],
        corners=[_tt27()],
    )


_NL_N2 = (
    "Integrated input-referred noise from 20 Hz to 20 kHz (audio band) must "
    "be below 10 μV RMS. Noise sweep 1 Hz to 1 MHz, TT 27 °C, 1 pF load. Use "
    "the AC source at the positive input as the noise reference."
)


def _gold_n2() -> TestPlan:
    return TestPlan(
        meta=Meta(id="n2_integrated_rms_audio", nl_spec=_NL_N2),
        dut=_dut_ir(),
        analyses=[
            NoiseAnalysis(
                id="noise_sweep",
                output_role="out",
                input_stimulus_id="ac_in_for_noise",
                f_start=1.0,
                f_stop=1.0e6,
            )
        ],
        stimulus=[_single_ended_ac_stimulus()],
        loading=[_cap_load_1p()],
        measurements=[
            Measurement(
                id="ni_int",
                from_analysis="noise_sweep",
                primitive=MeasurementPrimitive.NOISE_INTEGRATED_RMS,
                on_role="out",
                output_unit="V",
                f_low=20.0,
                f_high=20.0e3,
                referred_to=NoiseReferenceSide.INPUT,
            ),
        ],
        pass_criteria=[
            PassCriterion(
                measurement="ni_int", op=ComparisonOp.LT, value=10.0, spec_unit="uV"
            ),
        ],
        corners=[_tt27()],
    )


# ══════════════════════════════════════════════════════════════════════
#                              registry
# ══════════════════════════════════════════════════════════════════════


def _bc(case_id: str, desc: str, nl: str, builder: Callable[[], TestPlan]) -> BenchmarkCase:
    return BenchmarkCase(
        case_id=case_id,
        description=desc,
        nl_spec=nl,
        dut=_DUT_METADATA,
        gold_builder=builder,
    )


CASES: dict[str, BenchmarkCase] = {
    c.case_id: c
    for c in [
        # AC
        _bc("a1_diff_pair_gain_ugb", "AC: DC gain + UGB baseline", _NL_A1, _gold_a1),
        _bc("a2_phase_margin", "AC: phase margin via at_when_measurement", _NL_A2, _gold_a2),
        _bc("a3_gain_at_100k", "AC: ac_magnitude_at_freq", _NL_A3, _gold_a3),
        _bc("a4_minus_3db_corner", "AC: −3 dB rolloff via crossing primitive", _NL_A4, _gold_a4),
        _bc("a5_strict_ops", "AC: gt vs ge operator distinction", _NL_A5, _gold_a5),
        _bc("a6_units_mhz_phrasing", "AC: MHz spec-unit + 2 pF load", _NL_A6, _gold_a6),
        _bc("a7_two_corners", "AC: 2 PVT corners (TT/SS)", _NL_A7, _gold_a7),
        # TRAN
        _bc("t1_slew_rate_rising", "TRAN: rising-edge slew rate via tran_pulse", _NL_T1, _gold_t1),
        _bc(
            "t2_settling_time",
            "TRAN: settling time with tran_step + trigger_event",
            _NL_T2,
            _gold_t2,
        ),
        _bc("t3_overshoot", "TRAN: overshoot percentage", _NL_T3, _gold_t3),
        _bc(
            "t4_peak_to_peak_swing",
            "TRAN: peak-to-peak with tran_sine drive",
            _NL_T4,
            _gold_t4,
        ),
        _bc("t5_thd_1k", "TRAN: THD at 1 kHz with 9 harmonics", _NL_T5, _gold_t5),
        _bc(
            "t6_slew_rate_both_edges",
            "TRAN: slew rate on both edges via square wave",
            _NL_T6,
            _gold_t6,
        ),
        # DC
        _bc(
            "d1_input_referred_offset",
            "DC: input-referred offset via mid-rail target",
            _NL_D1,
            _gold_d1,
        ),
        _bc("d2_output_swing_range", "DC: output swing range (max-min)", _NL_D2, _gold_d2),
        _bc("d3_quiescent_current", "DC: supply current at .op", _NL_D3, _gold_d3),
        _bc("d4_gm_at_zero", "DC: small-signal gm at zero bias", _NL_D4, _gold_d4),
        _bc(
            "d5_vout_max_corners",
            "DC: max output across TT/FF corners",
            _NL_D5,
            _gold_d5,
        ),
        # NOISE
        _bc("n1_psd_at_1k", "NOISE: PSD at 1 kHz", _NL_N1, _gold_n1),
        _bc(
            "n2_integrated_rms_audio",
            "NOISE: integrated RMS over audio band",
            _NL_N2,
            _gold_n2,
        ),
    ]
}


assert len(CASES) == 20, f"expected exactly 20 cases, got {len(CASES)}"
