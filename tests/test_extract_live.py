"""Live LLM-extraction tests — gated on env-var presence.

Each provider's test is independent. Skipped cleanly when its keys are
absent, so the regular `uv run pytest` still passes in a clean environment.

To exercise the Anthropic path:
    export ANTHROPIC_API_KEY=sk-ant-...
    # optional: export ANTHROPIC_MODEL=claude-sonnet-4-6
    uv run pytest tests/test_extract_live.py::test_extract_with_anthropic -v -s

To exercise the OpenAI-compatible path (any backend — OpenRouter / Xiaomi
MiMo / Alibaba / vLLM / etc.):
    export OPENAI_COMPAT_API_KEY=...
    export OPENAI_COMPAT_BASE_URL=https://openrouter.ai/api/v1
    export OPENAI_COMPAT_MODEL=anthropic/claude-sonnet-4.6
    uv run pytest tests/test_extract_live.py::test_extract_with_openai_compatible -v -s
"""

from __future__ import annotations

import os

import pytest

from spec2testbench.evaluate import evaluate_extraction
from spec2testbench.extract import (
    DutMetadata,
    extract_with_anthropic,
    extract_with_openai_compatible,
)
from spec2testbench.ir import TestPlan


def test_extract_with_anthropic(
    gold_ir: TestPlan,
    nl_spec_01: str,
    dut_01: DutMetadata,
    plan_id_01: str,
) -> None:
    if not os.environ.get("ANTHROPIC_API_KEY"):
        pytest.skip("ANTHROPIC_API_KEY not set — skipping Anthropic live extraction")

    model = os.environ.get("ANTHROPIC_MODEL", "claude-sonnet-4-6")
    extracted = extract_with_anthropic(
        nl_spec=nl_spec_01,
        dut=dut_01,
        plan_id=plan_id_01,
        model=model,
    )

    print(f"\n[anthropic / {model}] extracted plan id = {extracted.meta.id}")
    report = evaluate_extraction(extracted, gold_ir)
    print(report.summary())
    assert report.equivalent, "Extracted IR differs from gold (see diff above)"


def test_extract_with_openai_compatible(
    gold_ir: TestPlan,
    nl_spec_01: str,
    dut_01: DutMetadata,
    plan_id_01: str,
) -> None:
    api_key = os.environ.get("OPENAI_COMPAT_API_KEY")
    base_url = os.environ.get("OPENAI_COMPAT_BASE_URL")
    model = os.environ.get("OPENAI_COMPAT_MODEL")
    if not (api_key and base_url and model):
        pytest.skip(
            "OPENAI_COMPAT_API_KEY / OPENAI_COMPAT_BASE_URL / OPENAI_COMPAT_MODEL "
            "must all be set — skipping OpenAI-compatible live extraction"
        )

    extracted = extract_with_openai_compatible(
        nl_spec=nl_spec_01,
        dut=dut_01,
        plan_id=plan_id_01,
        api_key=api_key,
        base_url=base_url,
        model=model,
    )

    print(f"\n[openai-compat / base={base_url} model={model}] extracted plan id = {extracted.meta.id}")
    report = evaluate_extraction(extracted, gold_ir)
    print(report.summary())
    assert report.equivalent, "Extracted IR differs from gold (see diff above)"
