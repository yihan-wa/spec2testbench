"""Offline guard for the benchmark/cases.py registry.

These tests run with NO network and NO LLM access. They catch authoring
mistakes before any LLM is involved by:

- Loading every BenchmarkCase
- Building every gold TestPlan (full pydantic validation, cross-refs, primitive validators)
- Canonicalizing every gold TestPlan (round-trip through model_dump → sorted)
- Confirming each case_id is unique and well-formed
- Confirming distribution across analysis types matches the design (AC 7 + TRAN 6 + DC 5 + NOISE 2)
- Confirming every primitive in the IR is exercised by at least one case
"""

from __future__ import annotations

import re

import pytest

from spec2testbench.benchmark import CASES, BenchmarkCase
from spec2testbench.ir import (
    AnalysisType,
    MeasurementPrimitive,
    PRIMITIVE_TO_ANALYSIS_TYPE,
    TestPlan,
    canonical_form,
    semantic_equivalent,
)


# ───────────────────────── per-case ─────────────────────────


@pytest.mark.parametrize("case", CASES.values(), ids=lambda c: c.case_id)
def test_case_gold_builder_produces_valid_test_plan(case: BenchmarkCase) -> None:
    plan = case.gold_builder()
    assert isinstance(plan, TestPlan)
    # round-trip canonicalization works
    canonical_form(plan)
    # equivalent to itself
    assert semantic_equivalent(plan, plan)


@pytest.mark.parametrize("case", CASES.values(), ids=lambda c: c.case_id)
def test_case_id_in_meta_matches_registry(case: BenchmarkCase) -> None:
    """meta.id should equal case_id — gives the runner a stable plan label."""
    plan = case.gold_builder()
    assert plan.meta.id == case.case_id


@pytest.mark.parametrize("case", CASES.values(), ids=lambda c: c.case_id)
def test_case_id_is_snake_case(case: BenchmarkCase) -> None:
    assert re.fullmatch(r"[a-z][a-z0-9_]*", case.case_id), (
        f"case_id {case.case_id!r} should be snake_case"
    )


@pytest.mark.parametrize("case", CASES.values(), ids=lambda c: c.case_id)
def test_case_nl_spec_carried_into_meta(case: BenchmarkCase) -> None:
    plan = case.gold_builder()
    assert plan.meta.nl_spec == case.nl_spec


# ───────────────────────── aggregate ─────────────────────────


def test_total_case_count_is_20() -> None:
    assert len(CASES) == 20


def test_case_ids_unique() -> None:
    ids = [c.case_id for c in CASES.values()]
    assert len(ids) == len(set(ids))


def test_analysis_distribution() -> None:
    """Distribution per design: AC 7 + TRAN 6 + DC 5 + NOISE 2 = 20."""
    counts = {AnalysisType.AC: 0, AnalysisType.TRAN: 0, AnalysisType.DC: 0, AnalysisType.NOISE: 0}
    for case in CASES.values():
        plan = case.gold_builder()
        # A case "belongs to" the analysis type of its primary measurement.
        # Picking the primitive of the first measurement is sufficient for
        # this distribution check — each case has a clear primary kind.
        primary = plan.measurements[0].primitive
        counts[PRIMITIVE_TO_ANALYSIS_TYPE[primary]] += 1
    assert counts == {
        AnalysisType.AC: 7,
        AnalysisType.TRAN: 6,
        AnalysisType.DC: 5,
        AnalysisType.NOISE: 2,
    }


def test_every_primitive_exercised() -> None:
    """Every primitive in the IR vocabulary must appear in at least one case."""
    used: set[MeasurementPrimitive] = set()
    for case in CASES.values():
        plan = case.gold_builder()
        for m in plan.measurements:
            used.add(m.primitive)
    unused = set(MeasurementPrimitive) - used
    assert not unused, f"primitives never exercised: {sorted(p.value for p in unused)}"


def test_distinct_corner_counts_show_multi_corner_coverage() -> None:
    """At least one case must declare more than 1 corner (multi-corner coverage)."""
    multi = [c.case_id for c in CASES.values() if len(c.gold_builder().corners) > 1]
    assert multi, "no case declares more than one corner"
