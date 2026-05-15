# Stage-1 Benchmark

A 20-case suite that measures how accurately an LLM converts a natural-language
analog test spec into a strictly-structured `TestPlan` IR.

## What's in scope

- **What it tests**: the extractor only (NL spec → IR). No simulator runs.
- **What "pass" means**: the extracted IR is `semantic_equivalent` to a
  hand-curated gold IR (ignores metadata + element order in unordered lists;
  preserves subckt port order).
- **What it doesn't test**: emitter correctness, simulator integration,
  end-to-end verdict accuracy — those land in Step 5 and later.

## Case distribution

| Analysis | Count | Cases |
|---|---:|---|
| AC    | 7 | gain+UGB, phase margin, gain/phase at f, −3 dB, strict ops, MHz units, 2 corners |
| TRAN  | 6 | slew rate (rising / both edges), settling, overshoot, peak-to-peak, THD |
| DC    | 5 | offset, swing range, Iq at .op, gm at bias, max output across corners |
| NOISE | 2 | PSD at f, integrated RMS over band |
| **Total** | **20** | covers all 16 primitives, all stimulus kinds, 1–2 corners |

The same 5-transistor differential-pair OTA DUT is used in every case. Only the
NL spec and the required measurements vary — this isolates the variable
("extractor's NL → IR accuracy") from confounders like topology-specific
quirks.

## Running

The benchmark exits non-zero if any case fails or errors; use that for CI.

### Dry-run (no LLM, validate gold IRs only)
```bash
python -m spec2testbench.benchmark.runner --dry-run
```

### Anthropic
```bash
export ANTHROPIC_API_KEY=sk-...
python -m spec2testbench.benchmark.runner \
    --provider anthropic --model claude-sonnet-4-6
```

### OpenAI-compatible (OpenRouter / Xiaomi MiMo / DashScope / vLLM / ...)
```bash
export OPENAI_COMPAT_API_KEY=...
export OPENAI_COMPAT_BASE_URL=https://openrouter.ai/api/v1
python -m spec2testbench.benchmark.runner \
    --provider openai-compatible \
    --model anthropic/claude-sonnet-4-6
```

### Subset (single case or several)
```bash
python -m spec2testbench.benchmark.runner --provider anthropic \
    --case-id a1_diff_pair_gain_ugb \
    --case-id t1_slew_rate_rising
```

## Reports

Each run writes two artifacts under `benchmark/results/`:

- `<timestamp>_<provider>.json` — full machine-readable report (per-case
  status, diffs, extracted IR JSON, errors).
- `<timestamp>_<provider>.txt` — human-readable summary (counts, failures
  with truncated diffs).

`results/` is gitignored except for `.gitkeep` and any explicitly committed
reference report.

## Cost

20 cases × ~3K input tokens × ~1.5K output tokens (worst case). At Sonnet 4.6
list prices: **~$0.15** per full run. Cheap enough to run on every
extractor-prompt change.

## Extending the case set

To add a new case:

1. Author the NL spec, DUT metadata (if not the standard 5T OTA), and the
   gold `TestPlan` builder in `cases.py`.
2. Add the case to the `CASES` list with a unique snake-case `case_id`.
3. The offline guard tests (`tests/test_benchmark_cases.py`) will
   automatically pick it up — they validate every gold IR builds,
   canonicalizes, and round-trips.

When adding a primitive the IR does not yet support: extend `ir.py` first
(see `_PRIMITIVE_PARAM_SPEC`), update `extract.py`'s `_SYSTEM_PROMPT`, add
offline tests in `tests/test_ir_extended_primitives.py`, *then* author the
case. Do not author cases that cannot be expressed in the current IR.
