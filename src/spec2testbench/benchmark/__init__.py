"""Stage-1 benchmark: NL spec → TestPlan IR extraction accuracy.

20 hand-curated (NL spec, DUT metadata, gold IR) cases spanning the four
v0 analysis types (AC / TRAN / DC / NOISE) and all 16 measurement primitives.

Public entry points:
- `cases.CASES` — case registry (dict by case_id)
- `runner.run_benchmark(...)` — programmatic runner
- `python -m spec2testbench.benchmark.runner ...` — CLI
"""

from .cases import CASES, BenchmarkCase

__all__ = ["CASES", "BenchmarkCase"]
