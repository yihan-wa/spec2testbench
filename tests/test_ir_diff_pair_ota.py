"""Round-trip the gold IR from examples/01_diff_pair_ota/trace.md against ir.TestPlan.

If this test passes, the v0 schema can express the running example end-to-end —
which means the 11 schema gaps (A–K) are all resolvable by the current design.

The `gold_ir` fixture lives in conftest.py so other test files can share it.
"""

from __future__ import annotations

import pytest

from spec2testbench.ir import (
    ComparisonOp,
    Corner,
    TestPlan,
)


def test_gold_ir_constructs(gold_ir: TestPlan) -> None:
    """The example can be expressed using only fields defined by the v0 schema."""
    assert gold_ir.meta.id == "01_diff_pair_ota"
    assert len(gold_ir.analyses) == 1
    assert len(gold_ir.measurements) == 2
    # Gap B: both measurements reference the SAME analysis (1-to-N).
    assert {m.from_analysis for m in gold_ir.measurements} == {"ac_smallsig"}


def test_json_round_trip_is_identity(gold_ir: TestPlan) -> None:
    """dump → load gives back an equal model. Required for any persistence / agent loop."""
    raw = gold_ir.model_dump_json()
    revived = TestPlan.model_validate_json(raw)
    assert revived == gold_ir


def test_gap_G_direction_required_for_crossing(gold_ir: TestPlan) -> None:
    """Gap G: crossing primitive without direction must be rejected."""
    bad = gold_ir.model_dump()
    bad["measurements"][1]["direction"] = None
    with pytest.raises(ValueError, match=r"requires field\(s\) \['direction'\]"):
        TestPlan.model_validate(bad)


def test_gap_F_asymptote_takes_no_params(gold_ir: TestPlan) -> None:
    """Asymptote primitive must not carry crossing params (avoids silent misuse)."""
    bad = gold_ir.model_dump()
    bad["measurements"][0]["target_magnitude"] = 1.0
    with pytest.raises(ValueError, match=r"must not set field\(s\) \['target_magnitude'\]"):
        TestPlan.model_validate(bad)


def test_gap_B_xref_validation(gold_ir: TestPlan) -> None:
    """Measurement.from_analysis must reference a real analysis id."""
    bad = gold_ir.model_dump()
    bad["measurements"][0]["from_analysis"] = "nonexistent"
    with pytest.raises(ValueError, match="from_analysis 'nonexistent' not in analyses"):
        TestPlan.model_validate(bad)


def test_gap_A_unknown_role_rejected(gold_ir: TestPlan) -> None:
    """Stimulus referencing a role not in DUT.subckt_ports is rejected."""
    bad = gold_ir.model_dump()
    bad["stimulus"][0]["ports"] = ["inp", "not_a_real_role"]
    with pytest.raises(ValueError, match="not_a_real_role"):
        TestPlan.model_validate(bad)


def test_gap_J_subckt_port_order_preserved(gold_ir: TestPlan) -> None:
    """SubcktPort list order is the SPICE call order — round-trip must preserve it."""
    expected_order = ["vinp", "vinn", "vout", "vbias", "vdd", "0"]
    raw = gold_ir.model_dump_json()
    revived = TestPlan.model_validate_json(raw)
    assert [p.name for p in revived.dut.subckt_ports] == expected_order


def test_gap_K_strict_vs_inclusive_ops(gold_ir: TestPlan) -> None:
    """DC gain spec uses '>' (gt, strict), UGB uses '>=' (ge, inclusive)."""
    pcs = {pc.measurement: pc for pc in gold_ir.pass_criteria}
    assert pcs["dc_gain"].op is ComparisonOp.GT
    assert pcs["ugb"].op is ComparisonOp.GE


def test_gap_H_corner_supply_override_is_optional(gold_ir: TestPlan) -> None:
    """v0 example doesn't set supply_voltage_override; field defaults to None."""
    assert gold_ir.corners[0].supply_voltage_override is None
    # And it can be set when needed:
    c = Corner(process="FF", temperature_celsius=85.0, supply_voltage_override=1.98)
    assert c.supply_voltage_override == 1.98
