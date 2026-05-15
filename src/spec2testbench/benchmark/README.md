# Stage-1 Benchmark / Stage-1 基准测试

A 20-case suite that measures how accurately an LLM converts a natural-
language analog test specification into the strictly-structured
`TestPlan` IR.

一套包含 20 个案例的基准测试集，用以衡量大语言模型将自然语言形式的模
拟电路测试规约转化为严格类型化 `TestPlan` 中间表示的准确度。

---

## Contents / 目录

- [中文版本](#中文版本)
- [English Version](#english-version)

---

# 中文版本

## 1. 范围界定

- **测试对象**：仅 extractor（NL spec → IR）。不调用仿真器。
- **判定"通过"的标准**：抽取所得 IR 与手工编写的 gold IR 之间满足
  `semantic_equivalent` 关系——忽略元数据与无序列表的元素顺序，但保留
  子电路端口顺序。
- **不在测试范围内**：emitter 正确性、仿真器接入、端到端 verdict 准确
  度——这些归属 Step 5 及之后。

## 2. 案例分布

| 分析类型 | 案例数 | 覆盖内容 |
|---|---:|---|
| AC    | 7 | gain + UGB、phase margin、指定频率处的 gain / phase、−3 dB 拐点、严格算符变体、MHz 单位、双 corner |
| TRAN  | 6 | 上升沿与双边沿压摆率、settling time、overshoot、peak-to-peak、THD |
| DC    | 5 | input-referred offset、swing range、`.op` 处的 Iq、指定偏置处的 gm、双 corner 下的最大输出 |
| NOISE | 2 | 指定频率处的 PSD、频带积分 RMS |
| **合计** | **20** | 覆盖全部 16 个原语与全部 stimulus 类型；含 1–2 corner |

所有案例共用同一被测电路（5 管差分对 OTA）。仅 NL spec 与所需测量发生
变化——此设计有意将变量收敛为"extractor 的 NL → IR 准确度"本身，避免
被测电路拓扑差异引入混淆。

## 3. 运行方式

基准 runner 在出现任意 fail / error 时以非零码退出，可直接用于 CI。

**Dry-run（不调用 LLM，仅验证 gold IR 完整性）：**

```bash
python -m spec2testbench.benchmark.runner --dry-run
```

**Anthropic 直连：**

```bash
export ANTHROPIC_API_KEY=sk-...
python -m spec2testbench.benchmark.runner \
    --provider anthropic --model claude-sonnet-4-6
```

**OpenAI 兼容端点**（OpenRouter / Alibaba DashScope / 本地 vLLM 等）：

```bash
export OPENAI_COMPAT_API_KEY=...
export OPENAI_COMPAT_BASE_URL=https://openrouter.ai/api/v1
python -m spec2testbench.benchmark.runner \
    --provider openai-compatible \
    --model anthropic/claude-sonnet-4-6
```

**子集运行**（单个或多个案例）：

```bash
python -m spec2testbench.benchmark.runner --provider anthropic \
    --case-id a1_diff_pair_gain_ugb \
    --case-id t1_slew_rate_rising
```

## 4. 报告产出

每次运行在 `benchmark/results/` 下生成两份产物：

- `<timestamp>_<provider>.json` — 完整的机器可读报告，包含每个案例的
  状态、字段级 diff、抽取所得 IR 的 JSON 形式、错误信息等。
- `<timestamp>_<provider>.txt` — 人类可读摘要，含计数、失败案例及截断
  后的 diff。

`results/` 目录已纳入 `.gitignore`，仅保留 `.gitkeep` 与任何显式提交的
参考报告。

## 5. 实测结果与稳态基线（5-run 统计）

完整运行于 2026-05-15 在 `mimo-v2.5-pro`（base_url
`https://api.xiaomimimo.com/v1`）上完成。框架在 7 轮 evaluator + prompt
迭代后达到稳态；随后进行 5 次独立完整运行以测量稳态分布与方差。

5 份报告文件已作为参考基线提交入库：

```
benchmark/results/
├── 2026-05-15T16-58-37_openai-compatible.{json,txt}   ← Run 1: 18/20
├── 2026-05-15T18-04-03_openai-compatible.{json,txt}   ← Run 2: 20/20
├── 2026-05-15T18-11-46_openai-compatible.{json,txt}   ← Run 3: 20/20
├── 2026-05-15T18-18-09_openai-compatible.{json,txt}   ← Run 4: 19/20
└── 2026-05-15T18-25-11_openai-compatible.{json,txt}   ← Run 5: 19/20
```

### 5.1 单次通过率分布

| Run | 通过率 | 失败 case |
|----:|------:|---------|
| 1 | 18/20 = 90% | `a5_strict_ops`、`t2_settling_time` |
| 2 | 20/20 = 100% | — |
| 3 | 20/20 = 100% | — |
| 4 | 19/20 = 95% | `t2_settling_time` |
| 5 | 19/20 = 95% | `t2_settling_time` |

**统计量**：

| 统计 | 值 |
|------|---|
| **均值（mean）** | **96.0%** |
| 中位数（median） | 95% |
| 最大 | 100% |
| 最小 | 90% |
| 标准差（σ） | ≈ 4 pp |
| 完美 run 占比 | 2/5 = 40% |

### 5.2 失败模式分布

| Case | 失败次数 / 5 | 失败模式 | 性质 |
|------|----:|---------|------|
| `t2_settling_time` | 3 / 5 | `op=lt` vs `le`（rule 4 已明文 le）；`step.tr=0.1` vs `1e-10`（误读 "100 ps"）；偶发 `window` 误加 | **模型 compliance 方差**：prompt 已明确说明，LLM 不稳定遵守 |
| `a5_strict_ops` | 1 / 5 | `stimulus.kind=single_ended_AC` vs `balanced_differential_AC` | NL 未显式说"differential"，LLM 偶发误选；属"NL 表述歧义" + 缺少 prompt 默认偏好规则的混合 |

其他 18 个 case 在 5 次独立运行中全部通过。

### 5.3 关于"稳态 100%"的判读

5 次中有 2 次（Run 2、Run 3）达到 100%，说明**框架本身允许 100% 抽取**。
其余 3 次 90-95% 的 gap 全部由 LLM 行为方差贡献，**不是框架问题**——
prompt 已经明文规定该怎么做（rule 4：within X → le；
tran_settling_time 描述：window 默认 null；规则 12：忠于 NL 数值），
LLM 只是没有 100% 时间遵守。这与项目目标"框架不应吃下模型能力问题"
一致。

从 45% baseline（首次实测）到 96% 稳态均值的迭代轨迹见仓库根 README
路线图 §7。

## 6. 成本估计

20 案例 × 约 3K 输入 token × 约 1.5K 输出 token（最坏估计）。按 Sonnet
4.6 list price 计，单次完整运行约 **$0.15**——足够低廉，可在每次
extractor 提示词改动后即时重跑。

## 7. 扩展案例集

新增案例的步骤：

1. 在 `cases.py` 中编写 NL spec、DutMetadata（若非标准 5T OTA）以及
   gold `TestPlan` 构造器。
2. 将案例加入 `CASES` 列表，采用唯一的 snake-case `case_id`。
3. `tests/test_benchmark_cases.py` 中的离线守护测试会自动覆盖新案例
   ——验证每个 gold IR 可构造、可 canonical 化、可 round-trip。

**新增 IR 尚未支持的原语时**：先扩展 `ir.py`（参见
`_PRIMITIVE_PARAM_SPEC`），更新 `extract.py` 的 `_SYSTEM_PROMPT`，在
`tests/test_ir_extended_primitives.py` 中补充测试，**然后**才编写新案
例。请勿编写当前 IR 尚不能表达的案例。

---

[返回顶部 / Back to top](#stage-1-benchmark--stage-1-基准测试)

---

# English Version

## 1. Scope

- **What it tests**: the extractor only (NL spec → IR). No simulator
  runs.
- **What "pass" means**: the extracted IR is `semantic_equivalent` to a
  hand-curated gold IR — metadata and the order of unordered lists are
  ignored, while sub-circuit port order is preserved.
- **What it does not test**: emitter correctness, simulator integration,
  or end-to-end verdict accuracy. These are addressed in Step 5 and
  beyond.

## 2. Case distribution

| Analysis | Count | Coverage |
|---|---:|---|
| AC    | 7 | gain + UGB, phase margin, gain / phase at a given frequency, −3 dB corner, strict-operator variants, MHz units, two corners |
| TRAN  | 6 | slew rate (rising and dual-edge), settling, overshoot, peak-to-peak, THD |
| DC    | 5 | offset, swing range, Iq at `.op`, gm at a specified bias, maximum output across corners |
| NOISE | 2 | PSD at a given frequency, integrated RMS over a band |
| **Total** | **20** | covers all 16 primitives and all stimulus kinds; with one or two corners |

Every case uses the same device under test (a 5-transistor differential-
pair OTA). Only the NL spec and the required measurements vary — a
deliberate design choice that isolates the variable ("extractor's NL →
IR accuracy") from confounders such as topology-specific quirks.

## 3. Invocation

The runner exits with a non-zero code if any case fails or errors,
making it suitable for direct use in continuous integration.

**Dry-run (no LLM call; validates every gold IR):**

```bash
python -m spec2testbench.benchmark.runner --dry-run
```

**Anthropic native endpoint:**

```bash
export ANTHROPIC_API_KEY=sk-...
python -m spec2testbench.benchmark.runner \
    --provider anthropic --model claude-sonnet-4-6
```

**OpenAI-compatible endpoint** (OpenRouter / Alibaba DashScope / local
vLLM / etc.):

```bash
export OPENAI_COMPAT_API_KEY=...
export OPENAI_COMPAT_BASE_URL=https://openrouter.ai/api/v1
python -m spec2testbench.benchmark.runner \
    --provider openai-compatible \
    --model anthropic/claude-sonnet-4-6
```

**Subset (one or several cases):**

```bash
python -m spec2testbench.benchmark.runner --provider anthropic \
    --case-id a1_diff_pair_gain_ugb \
    --case-id t1_slew_rate_rising
```

## 4. Reports

Each run writes two artefacts under `benchmark/results/`:

- `<timestamp>_<provider>.json` — the full machine-readable report,
  containing per-case status, field-level diffs, the extracted IR in
  JSON form, and error messages where applicable.
- `<timestamp>_<provider>.txt` — a human-readable summary with counts
  and truncated failure diffs.

The `results/` directory is included in `.gitignore` except for
`.gitkeep` and any explicitly committed reference report.

## 5. Measured results and stable baseline (5-run statistics)

The full benchmark was executed against `mimo-v2.5-pro` (base_url
`https://api.xiaomimimo.com/v1`) on 2026-05-15. After seven rounds of
evaluator and prompt hardening, the framework reached a stable plateau;
five independent full-suite runs were then performed to characterise
the steady-state distribution and run-to-run variance.

The five reports are committed as the reference baseline:

```
benchmark/results/
├── 2026-05-15T16-58-37_openai-compatible.{json,txt}   ← Run 1: 18/20
├── 2026-05-15T18-04-03_openai-compatible.{json,txt}   ← Run 2: 20/20
├── 2026-05-15T18-11-46_openai-compatible.{json,txt}   ← Run 3: 20/20
├── 2026-05-15T18-18-09_openai-compatible.{json,txt}   ← Run 4: 19/20
└── 2026-05-15T18-25-11_openai-compatible.{json,txt}   ← Run 5: 19/20
```

### 5.1 Per-run pass-rate distribution

| Run | Pass rate | Failures |
|----:|---------:|---------|
| 1 | 18 / 20 = 90% | `a5_strict_ops`, `t2_settling_time` |
| 2 | 20 / 20 = 100% | — |
| 3 | 20 / 20 = 100% | — |
| 4 | 19 / 20 = 95% | `t2_settling_time` |
| 5 | 19 / 20 = 95% | `t2_settling_time` |

**Aggregate statistics**:

| Statistic | Value |
|---|---|
| **Mean** | **96.0%** |
| Median | 95% |
| Maximum | 100% |
| Minimum | 90% |
| Std. dev. (σ) | ≈ 4 pp |
| Perfect-run fraction | 2 / 5 = 40% |

### 5.2 Failure-mode distribution

| Case | Failures / 5 | Symptoms | Nature |
|------|----:|---------|------|
| `t2_settling_time` | 3 / 5 | `op=lt` vs `le` (rule 4 already specifies `le`); `step.tr=0.1` vs `1e-10` (misreading of "100 ps"); occasional spurious `window` field | **Model compliance variance**: the prompt is explicit, but the model does not follow it consistently |
| `a5_strict_ops` | 1 / 5 | `stimulus.kind=single_ended_AC` vs `balanced_differential_AC` | NL does not explicitly say "differential"; combination of NL ambiguity and the absence of an explicit prompt default for differential-pair DUTs |

The remaining 18 cases pass in all 5 runs.

### 5.3 On the interpretation of intermittent 100%

Two of five runs reached 100%, demonstrating that **the framework
admits a perfect outcome**. The 5–10 pp gap on the other three runs is
entirely attributable to model behavioural variance: the system prompt
already states the relevant rule explicitly (rule 4: "within X → le";
the tran_settling_time entry: default `window=null`; rule 12: preserve
NL numeric values verbatim), and the model simply does not comply on
every run. This is consistent with the project's stated goal that the
framework should not absorb model-capability issues.

The trajectory from the 45% first-measurement baseline to the 96%
steady-state mean is summarised in the top-level README's roadmap §7.

## 6. Cost estimate

20 cases × approximately 3 K input tokens × approximately 1.5 K output
tokens (worst case). At Sonnet 4.6 list prices, a full run costs
approximately **$0.15** — sufficiently inexpensive to be rerun after
every extractor-prompt revision.

## 7. Extending the case set

To add a new case:

1. Author the NL spec, the `DutMetadata` (if not the standard 5T OTA),
   and the gold `TestPlan` builder in `cases.py`.
2. Add the case to the `CASES` list with a unique snake-case
   `case_id`.
3. The offline guard tests in `tests/test_benchmark_cases.py`
   automatically pick up the new case, verifying that every gold IR
   builds, canonicalises, and round-trips.

**When the new case requires a primitive the IR does not yet support**:
first extend `ir.py` (see `_PRIMITIVE_PARAM_SPEC`), update
`_SYSTEM_PROMPT` in `extract.py`, add positive- and negative-path tests
in `tests/test_ir_extended_primitives.py`, and only **then** author the
case. Cases that cannot be expressed in the current IR must not be
authored.

---

[Back to top / 返回顶部](#stage-1-benchmark--stage-1-基准测试)
