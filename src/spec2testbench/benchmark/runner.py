"""Stage-1 benchmark runner.

Iterates the case registry, invokes one extractor (anthropic | openai-compatible),
evaluates each result against the gold IR, and writes both a structured JSON
report and a human-readable text report under ``benchmark/results/``.

CLI:

    # Anthropic (uses $ANTHROPIC_API_KEY)
    python -m spec2testbench.benchmark.runner --provider anthropic \\
        --model claude-sonnet-4-6

    # OpenAI-compatible (OpenRouter, Xiaomi MiMo, Alibaba DashScope, vLLM, ...)
    python -m spec2testbench.benchmark.runner --provider openai-compatible \\
        --base-url https://openrouter.ai/api/v1 \\
        --model anthropic/claude-sonnet-4-6 \\
        --api-key $OPENROUTER_API_KEY

    # Dry-run: validate every gold IR without calling any LLM
    python -m spec2testbench.benchmark.runner --dry-run

    # Filter to a subset of cases
    python -m spec2testbench.benchmark.runner --provider anthropic \\
        --case-id a1_diff_pair_gain_ugb --case-id t1_slew_rate_rising

The JSON report's schema mirrors the dataclasses in this module (see
``BenchmarkReport`` and ``CaseResult``).
"""

from __future__ import annotations

import argparse
import json
import os
import sys
import time
from dataclasses import asdict, dataclass, field
from pathlib import Path
from typing import Any, Literal

from spec2testbench.benchmark.cases import CASES, BenchmarkCase
from spec2testbench.evaluate import evaluate_extraction
from spec2testbench.extract import (
    ExtractionError,
    extract_with_anthropic,
    extract_with_openai_compatible,
)
from spec2testbench.ir import TestPlan


# ───────────────────────── result schema ─────────────────────────


@dataclass
class CaseResult:
    case_id: str
    status: Literal["pass", "fail", "error", "skipped"]
    duration_s: float
    differences: list[str] = field(default_factory=list)
    error: str | None = None
    extracted_plan_json: dict[str, Any] | None = None


@dataclass
class BenchmarkReport:
    timestamp: str
    provider: str
    model: str | None
    base_url: str | None
    n_total: int
    n_pass: int
    n_fail: int
    n_error: int
    n_skipped: int
    cases: list[CaseResult] = field(default_factory=list)

    @property
    def pass_rate(self) -> float:
        attempted = self.n_pass + self.n_fail + self.n_error
        return (self.n_pass / attempted) if attempted else 0.0


# ───────────────────────── extractor dispatch ─────────────────────────


def _extract(
    case: BenchmarkCase,
    *,
    provider: str,
    model: str | None,
    api_key: str | None,
    base_url: str | None,
) -> TestPlan:
    if provider == "anthropic":
        return extract_with_anthropic(
            nl_spec=case.nl_spec,
            dut=case.dut,
            plan_id=case.case_id,
            api_key=api_key,
            model=model or "claude-sonnet-4-6",
        )
    if provider == "openai-compatible":
        if not base_url:
            raise ValueError("--base-url is required for provider=openai-compatible")
        if not model:
            raise ValueError("--model is required for provider=openai-compatible")
        if not api_key:
            raise ValueError(
                "--api-key (or env) is required for provider=openai-compatible"
            )
        return extract_with_openai_compatible(
            nl_spec=case.nl_spec,
            dut=case.dut,
            plan_id=case.case_id,
            api_key=api_key,
            base_url=base_url,
            model=model,
        )
    raise ValueError(f"Unknown provider: {provider!r}")


# ───────────────────────── core driver ─────────────────────────


def run_benchmark(
    *,
    provider: str,
    model: str | None = None,
    api_key: str | None = None,
    base_url: str | None = None,
    case_ids: list[str] | None = None,
    dry_run: bool = False,
    on_progress: Any = None,
) -> BenchmarkReport:
    """Run the benchmark and return a structured report.

    Args:
        provider: "anthropic" | "openai-compatible" | "dry-run".
        case_ids: subset of CASES keys to run; None ⇒ all.
        dry_run: skip LLM, only validate that gold IRs round-trip.
        on_progress: optional callable receiving (idx, n, case_id, status) per case.
    """
    selected = CASES if case_ids is None else {
        cid: CASES[cid] for cid in case_ids if cid in CASES
    }
    if case_ids:
        missing = [cid for cid in case_ids if cid not in CASES]
        if missing:
            raise ValueError(f"Unknown case_id(s): {missing}")

    report = BenchmarkReport(
        timestamp=time.strftime("%Y-%m-%dT%H:%M:%S%z"),
        provider=provider if not dry_run else "dry-run",
        model=model if not dry_run else None,
        base_url=base_url if not dry_run else None,
        n_total=len(selected),
        n_pass=0, n_fail=0, n_error=0, n_skipped=0,
    )

    for idx, (cid, case) in enumerate(selected.items(), start=1):
        t0 = time.time()
        try:
            gold = case.gold_builder()

            if dry_run:
                # Skip LLM: confirm gold builds and self-equivalent; report skipped.
                evaluate_extraction(gold, gold)
                result = CaseResult(
                    case_id=cid, status="skipped",
                    duration_s=time.time() - t0,
                    differences=[],
                )
                report.n_skipped += 1
            else:
                extracted = _extract(
                    case,
                    provider=provider, model=model,
                    api_key=api_key, base_url=base_url,
                )
                report_eval = evaluate_extraction(extracted, gold)
                duration = time.time() - t0
                if report_eval.equivalent:
                    result = CaseResult(
                        case_id=cid, status="pass",
                        duration_s=duration,
                        extracted_plan_json=extracted.model_dump(mode="json"),
                    )
                    report.n_pass += 1
                else:
                    result = CaseResult(
                        case_id=cid, status="fail",
                        duration_s=duration,
                        differences=list(report_eval.differences),
                        extracted_plan_json=extracted.model_dump(mode="json"),
                    )
                    report.n_fail += 1

        except ExtractionError as e:
            result = CaseResult(
                case_id=cid, status="error",
                duration_s=time.time() - t0,
                error=f"ExtractionError: {e}",
            )
            report.n_error += 1
        except Exception as e:  # noqa: BLE001
            result = CaseResult(
                case_id=cid, status="error",
                duration_s=time.time() - t0,
                error=f"{type(e).__name__}: {e}",
            )
            report.n_error += 1

        report.cases.append(result)
        if on_progress is not None:
            on_progress(idx, len(selected), cid, result.status)

    return report


# ───────────────────────── reporting ─────────────────────────


def _print_progress(idx: int, n: int, cid: str, status: str) -> None:
    sym = {"pass": "✅", "fail": "❌", "error": "💥", "skipped": "·"}[status]
    print(f"[{idx:2d}/{n}] {sym}  {cid}", flush=True)


def _format_summary(report: BenchmarkReport) -> str:
    n_attempted = report.n_pass + report.n_fail + report.n_error
    rate = (100.0 * report.n_pass / n_attempted) if n_attempted else 0.0
    lines = [
        "",
        "═" * 72,
        f"Provider:    {report.provider}",
        f"Model:       {report.model or '-'}",
        f"Base URL:    {report.base_url or '-'}",
        f"Timestamp:   {report.timestamp}",
        "─" * 72,
        f"Pass:        {report.n_pass}/{n_attempted}  ({rate:.1f}%)" if n_attempted else "Pass:        — (dry-run)",
        f"Fail:        {report.n_fail}",
        f"Error:       {report.n_error}",
        f"Skipped:     {report.n_skipped}",
        "═" * 72,
    ]
    if report.n_fail or report.n_error:
        lines.append("\nFailures / errors:")
        for c in report.cases:
            if c.status in ("fail", "error"):
                lines.append(f"\n  {c.case_id}  [{c.status}]")
                if c.error:
                    lines.append(f"    error: {c.error}")
                for d in c.differences[:8]:
                    lines.append(f"    - {d}")
                if len(c.differences) > 8:
                    lines.append(f"    ... and {len(c.differences) - 8} more diffs")
    return "\n".join(lines)


def _write_reports(report: BenchmarkReport, results_dir: Path) -> tuple[Path, Path]:
    results_dir.mkdir(parents=True, exist_ok=True)
    stamp = time.strftime("%Y-%m-%dT%H-%M-%S")
    name = f"{stamp}_{report.provider}"
    json_path = results_dir / f"{name}.json"
    text_path = results_dir / f"{name}.txt"

    json_payload = {
        **{k: v for k, v in asdict(report).items() if k != "cases"},
        "cases": [asdict(c) for c in report.cases],
    }
    json_path.write_text(json.dumps(json_payload, indent=2, ensure_ascii=False))
    text_path.write_text(_format_summary(report))
    return json_path, text_path


# ───────────────────────── CLI ─────────────────────────


def _build_parser() -> argparse.ArgumentParser:
    p = argparse.ArgumentParser(
        prog="python -m spec2testbench.benchmark.runner",
        description="Run the Stage-1 NL→IR benchmark and write a report.",
    )
    p.add_argument(
        "--provider",
        choices=["anthropic", "openai-compatible"],
        help="Which extractor backend to use. Required unless --dry-run.",
    )
    p.add_argument("--model", help="Model id (e.g. claude-sonnet-4-6, gpt-4o, etc.).")
    p.add_argument(
        "--api-key",
        help="API key. If omitted: $ANTHROPIC_API_KEY for anthropic, "
        "$OPENAI_COMPAT_API_KEY for openai-compatible.",
    )
    p.add_argument(
        "--base-url",
        help="OpenAI-protocol base URL (required for provider=openai-compatible). "
        "If omitted: $OPENAI_COMPAT_BASE_URL.",
    )
    p.add_argument(
        "--case-id", action="append", dest="case_ids",
        help="Run only this case_id (repeatable). Default: all cases.",
    )
    p.add_argument(
        "--dry-run", action="store_true",
        help="Skip the LLM: only validate that every gold IR builds + canonicalizes.",
    )
    p.add_argument(
        "--results-dir",
        default=str(Path(__file__).parent / "results"),
        help="Where to write JSON + text reports. Default: benchmark/results/.",
    )
    return p


def main(argv: list[str] | None = None) -> int:
    args = _build_parser().parse_args(argv)

    if not args.dry_run and not args.provider:
        print("error: --provider is required (or use --dry-run).", file=sys.stderr)
        return 2

    api_key = args.api_key
    base_url = args.base_url
    if args.provider == "anthropic" and api_key is None:
        api_key = os.environ.get("ANTHROPIC_API_KEY")
    if args.provider == "openai-compatible":
        api_key = api_key or os.environ.get("OPENAI_COMPAT_API_KEY")
        base_url = base_url or os.environ.get("OPENAI_COMPAT_BASE_URL")

    print(f"Loading {len(CASES)} benchmark cases...", flush=True)
    if args.case_ids:
        print(f"Filtering to: {', '.join(args.case_ids)}", flush=True)

    report = run_benchmark(
        provider=args.provider or "anthropic",  # unused if dry-run
        model=args.model,
        api_key=api_key,
        base_url=base_url,
        case_ids=args.case_ids,
        dry_run=args.dry_run,
        on_progress=_print_progress,
    )

    print(_format_summary(report))

    json_path, text_path = _write_reports(report, Path(args.results_dir))
    print(f"\nReports written:\n  {json_path}\n  {text_path}")

    # Exit code: 0 if everything passed (or dry-run), 1 if any fail/error.
    if report.n_fail or report.n_error:
        return 1
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
