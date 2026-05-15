# spec2testbench

**A research codebase for evaluating whether large language models can convert
natural-language analog circuit verification specifications into structured,
executable testbenches.**
**研究代码库：评估大语言模型能否将自然语言形式的模拟电路验证规约转化为结构化、可执行的测试台。**

---

## Project status / 项目状态

| Stage | Description | Status |
|---|---|---|
| 1 | Manual end-to-end walkthrough on a reference example | Complete (2026-05-13) |
| 2 | Strict TestPlan IR schema with semantic-equivalence definition | Complete (2026-05-13) |
| 3 | Cross-provider LLM extractor + automated evaluator | Complete (2026-05-14) |
| 4 | 20-case Stage-1 benchmark (NL → IR extraction accuracy) | Code-complete (2026-05-15); benchmark execution pending |
| 5 | Stage-2 emitter (IR → ngspice netlist) + executability metric | Pending |
| 6 | End-to-end pipeline; comparison against direct generation | Pending |
| 7 | Failure-mode clustering and iteration planning | Pending |

The repository contains 157 offline tests covering the IR schema, semantic
equivalence, primitive validation, and the benchmark case registry. Linting
(`ruff`) passes. Live LLM extraction tests are gated on environment
variables and skip by default.

---

## Contents / 目录

- [中文部分](#中文版本)
  - [1. 研究动机](#1-研究动机)
  - [2. 系统总览](#2-系统总览)
  - [3. TestPlan 中间表示](#3-testplan-中间表示)
  - [4. 跨厂商 LLM 抽取](#4-跨厂商-llm-抽取)
  - [5. Stage-1 基准测试](#5-stage-1-基准测试)
  - [6. 实现细节](#6-实现细节)
  - [7. 路线图](#7-路线图)
  - [8. 设计原则](#8-设计原则)
  - [9. 常见问题](#9-常见问题)
- [English Section](#english-section)

---

# 中文版本

## 1. 研究动机

模拟集成电路设计的自动化研究（以下简称 *autoresearch*）依赖一个反馈闭环：
设计代理（LLM agent）提出电路方案，仿真器对其性能进行评估，代理据此迭代。
此闭环中，**测试台（testbench）承担"评判员"的角色**——它将抽象的指标
（DC gain、UGB、phase margin 等）翻译为可在 SPICE 类仿真器上执行的激励、
分析与判定逻辑。

然而，编写一份正确的 testbench 需要工程师在差分激励的对称性、工艺角的覆
盖、仿真器方言陷阱、单位一致性等众多细节上做出精确决策；这一工作通常以
"小时"为单位计量。代理的迭代速度通常以"分钟"乃至"秒"为单位，因此**人
工编写 testbench 构成了 autoresearch 的关键瓶颈**。

`spec2testbench` 旨在以 LLM 为工具，将"自然语言规约 → 可执行 testbench"
这一翻译过程自动化。本仓库面向两个并行目标：

1. **能力评估**：在量化框架下回答"当前的 LLM 是否具备这一翻译能力？"。
2. **诊断工具**：当能力不足时，给出**字段级的失败定位**，以引导后续模
   型与提示词改进。

由于这一翻译过程本质上是"具备专家知识的结构化语义映射"，LLM 是目前少
数能在此层级稳定操作的工具，使本研究具有时效性。

---

## 2. 系统总览

完整流水线分为三个阶段：

```
┌─────────────────┐
│  自然语言规约    │   NL spec：DC gain ≥ 60 dB, UGB ≥ 10 MHz, ...
└────────┬────────┘
         │  Stage 1：spec extraction           ← v0 已实装
         ▼
┌─────────────────┐
│  TestPlan IR    │   严格 JSON，pydantic 校验
└────────┬────────┘
         │  +  DUT netlist（上游输入）
         │  +  PDK context（来自工艺库）
         │  Stage 2：testbench emission        ← v0 留作 Step 5
         ▼
┌─────────────────┐
│  可执行 netlist  │   .cir / .spice
└────────┬────────┘
         │  Stage 3：simulate
         ▼
┌─────────────────┐
│   pass / fail   │   分阶段评估：extract / emit / sim / verdict
└─────────────────┘
```

**v0 已落地的部分**：Stage 1（NL → IR）以及对其的自动化评估。
**v0 未落地的部分**：Stage 2 与 Stage 3。emitter 接口已预留，目前抛出
`NotImplementedError`，预计于 Step 5 实装。

中间表示（IR）位于设计的核心：它将"测什么、怎么测、怎么判断通过"用一
份严格类型化的结构表达，成为 LLM 输出验证、emitter 设计、评估器比对的
共同基础。

---

## 3. TestPlan 中间表示

### 3.1 顶层结构

`TestPlan` 由七个顶层 section 组成：

```python
class TestPlan(BaseModel):
    meta:          Meta              # 元数据 + 原始 NL spec 文本
    dut:           Dut               # 被测电路标识 + 端口签名
    analyses:      list[Analysis]    # 仿真定义（多种类型的判别联合）
    stimulus:      list[Stimulus]    # 激励源
    loading:       list[Loading]     # 无源负载
    measurements:  list[Measurement] # 从仿真曲线导出的标量
    pass_criteria: list[PassCriterion]  # 判定规则
    corners:       list[Corner]      # 工艺/温度/电源角点
```

之所以采用分段式结构而非扁平 JSON，原因在于：当两个测量共享同一次仿真
（如 DC gain 与 UGB 均源自一次 AC 扫频）时，扁平结构无法消除冗余且容易
引发不一致。详细分析见 `examples/01_diff_pair_ota/trace.md` 中的
Gap-B 一节。

### 3.2 分析类型覆盖

IR 在 v0 中覆盖四类仿真分析，均为 ngspice 原生支持：

| 分析类型 | IR 模型 | ngspice 对应 |
|---|---|---|
| AC（小信号交流扫频） | `AcAnalysis` | `.ac <style> <pts> <f_start> <f_stop>` |
| TRAN（瞬态） | `TranAnalysis` | `.tran <t_step> <t_stop> <t_start> [uic]` |
| DC（工作点或单源扫描） | `DcAnalysis` | `.op` 或 `.dc <Vsrc> <start> <stop> <step>` |
| NOISE（噪声扫频） | `NoiseAnalysis` | `.noise v(<out>) <input_src> ...` |

其余 23 类（`stb`、`pss`、`pac`、`pnoise`、`hb` 等周期稳态 / 谐波平衡 /
S-参数家族）**未纳入 v0 enum**——因为 ngspice 不支持，强行加入会使 IR 表
达力与 emitter 实际能力脱节。这些分析将在后续接入 Spectre / Xyce 等仿真
器时另行扩展。

### 3.3 闭集测量原语

"DC gain"、"UGB"、"phase margin" 等概念在物理意义上并非数据字段，而是
**从仿真曲线提取标量的算法**。为消除自然语言字符串带来的 emitter / 评估
器约定漂移，IR 采用**闭集测量原语词表**。v0 共定义 16 个原语：

| 类别 | 原语 | 语义 | 必填参数 |
|---|---|---|---|
| AC | `ac_low_freq_asymptote` | \|H(f)\| 在 f = f_start 处 | — |
| AC | `ac_freq_at_magnitude_crossing` | \|H(f)\| 等于阈值时的频率 | `target_magnitude`、`direction` |
| AC | `ac_phase_at_freq` | ∠H(f) 在指定频率处 | `at_freq` 或 `at_when_measurement`（二者择一） |
| AC | `ac_magnitude_at_freq` | \|H(f)\| 在指定频率处 | `at_freq` |
| AC | `ac_phase_margin` | 180° + ∠H 在 UGB 处 | `at_when_measurement` |
| TRAN | `tran_slew_rate` | 阶跃过渡的 10%–90% 摆率 | `edge` |
| TRAN | `tran_settling_time` | 落入 ±tolerance 带的时间 | `tolerance_pct`、`trigger_event` |
| TRAN | `tran_overshoot_pct` | 过冲百分比 | — |
| TRAN | `tran_peak_to_peak` | 窗口内 max − min | — |
| TRAN | `tran_thd` | 单音输入的总谐波失真 | `fundamental_freq` |
| DC | `dc_offset_input_referred` | 输入折合失调电压 | `target_output_role`、`target_output_value` |
| DC | `dc_output_swing_range` | DC 扫描下输出极值 | `extreme` ∈ {min, max, range} |
| DC | `dc_supply_current` | 供电电流 | `supply_role` |
| DC | `dc_gm` | 小信号跨导 | `input_role`、`output_role`、`at_bias_value` |
| NOISE | `noise_input_referred_at_freq` | 输入折合噪声 PSD | `at_freq` |
| NOISE | `noise_integrated_rms` | 频带积分 RMS 噪声 | `f_low`、`f_high`、`referred_to` |

每个原语在 `Measurement` 模型上以**字段级 validator** 强制其必填参数存
在、禁用参数不出现；此外 `_cross_refs` validator 强制原语类型与所引用
`Analysis` 类型一致（例如 `tran_slew_rate` 不得引用 AC 分析）。这些约束
共同保证：当 LLM 输出错位时，pydantic 在最早的边界拒绝并给出可定位的错
误信息。

### 3.4 语义等价

为支持自动化基准评估，IR 定义了**语义等价**关系 `semantic_equivalent`：

- 忽略 `meta.id` 与 `meta.nl_spec`——视为标签而非内容。
- 六个顶层列表（`analyses`、`stimulus`、`loading`、`measurements`、
  `pass_criteria`、`corners`）被视为**无序集合**比较。
- `dut.subckt_ports` 保持**有序序列**比较——因其顺序对 SPICE
  子电路实例化语义敏感。
- 隐式默认值与显式默认值同一处理——由 pydantic 在序列化时统一展开。

不等价时，评估器返回字段级 diff 列表，定位至具体路径与值，例如：

```
measurements[1].direction: extracted='rising' gold='falling'
pass_criteria[0].value:    extracted=70.0       gold=60.0
```

---

## 4. 跨厂商 LLM 抽取

为避免对单一 LLM 厂商或中介库的耦合，extractor 模块采用"并列函数"模式：

```python
def extract_with_anthropic(
    nl_spec: str, dut: DutMetadata, *,
    plan_id: str, api_key: str | None = None,
    model: str = "claude-sonnet-4-6",
) -> TestPlan: ...

def extract_with_openai_compatible(
    nl_spec: str, dut: DutMetadata, *,
    plan_id: str, api_key: str, base_url: str, model: str,
) -> TestPlan: ...
```

两个函数共享：

- 同一份系统提示词（`_SYSTEM_PROMPT`）——所有"如何抽取"的知识集中于此。
- 同一份 JSON Schema：`TestPlan.model_json_schema()`，作为两端
  structured-output 机制的输入。
- 同一份 pydantic 反序列化与校验逻辑。

两者唯一差异为 SDK 适配层（约 30 行），分别对应 Anthropic native tool
use 与 OpenAI 协议 function calling。后者通过 `base_url` 参数可适配
OpenAI 原生端点、OpenRouter 网关、Alibaba DashScope、本地 vLLM 等任意
兼容 OpenAI 协议的服务。

**新增 LLM 厂商的成本**：再写一个 `extract_with_<provider>` 函数，约
30 行；**不引入** Provider 抽象基类或工厂。该决策的依据是：abstract
factory 在第二个厂商出现时收益尚未显化，而 LangChain 等胶水库会模糊
prompt caching、token usage、错误诊断等本研究关注的细节信号。

---

## 5. Stage-1 基准测试

### 5.1 案例集构造

为量化评估 NL → IR 的抽取准确度，本仓库提供 20 个手工设计的基准案例，
其分布如下：

| 分析类型 | 案例数 | 覆盖原语 |
|---|---:|---|
| AC    | 7 | 全部 5 个 AC 原语 |
| TRAN  | 6 | 全部 5 个 TRAN 原语 + 4 类 TRAN stimulus（PULSE/SINE/STEP/双边沿） |
| DC    | 5 | 全部 4 个 DC 原语 + DC sweep / .op 两种模式 |
| NOISE | 2 | 全部 2 个 NOISE 原语 |
| 合计  | 20 | 全部 16 个原语；多 corner 与单 corner 兼有 |

每个案例由三元组 `(NL spec, DutMetadata, gold IR builder)` 构成。所有案
例共用同一被测电路（5-管差分对 OTA），这一设计有意将变量收敛到"NL → IR
抽取本身"，避免被测电路拓扑差异引入的混淆因子。

完整注册表见 `src/spec2testbench/benchmark/cases.py`，其完整性由
`tests/test_benchmark_cases.py` 中的离线守护测试验证（参数化 86 项测试，
含每个 gold IR 的构造、round-trip、原语全覆盖、分布一致性等）。

### 5.2 评估方法

基准 runner 的工作流为：

1. 加载注册表中的所有（或子集）案例。
2. 对每个案例调用一个 extractor（`anthropic` 或 `openai-compatible`）。
3. 将抽取所得 IR 与 gold IR 调用 `evaluate_extraction(extracted, gold)`
   比较，得到二元判定与字段级 diff。
4. 聚合为 JSON + 文本格式的报告。

输出报告同时包含每案例的状态（pass / fail / error / skipped）、字段
diff、抽取耗时、抽取所得 IR 的 JSON 形式；可作为后续失败模式聚类的原
始数据。

### 5.3 运行方式

**离线 dry-run**（仅验证 gold IR 完整性，不调用 LLM）：

```bash
uv run python -m spec2testbench.benchmark.runner --dry-run
```

**Anthropic 直连**：

```bash
export ANTHROPIC_API_KEY=sk-ant-...
uv run python -m spec2testbench.benchmark.runner \
    --provider anthropic --model claude-sonnet-4-6
```

**OpenAI 兼容端点**（OpenRouter / DashScope / 本地 vLLM 等）：

```bash
export OPENAI_COMPAT_API_KEY=...
export OPENAI_COMPAT_BASE_URL=https://openrouter.ai/api/v1
uv run python -m spec2testbench.benchmark.runner \
    --provider openai-compatible \
    --model anthropic/claude-sonnet-4-6
```

**子集运行**：

```bash
uv run python -m spec2testbench.benchmark.runner --provider anthropic \
    --case-id a1_diff_pair_gain_ugb --case-id t1_slew_rate_rising
```

报告生成于 `src/spec2testbench/benchmark/results/` 目录。每次运行产出
`<timestamp>_<provider>.json` 与同名 `.txt`，其中 JSON 文件保留完整结
构以便后续二次分析。

> **状态备注**：当前 Stage-1 基准测试已**代码完整（code-complete）**，
> 但截至 README 更新时尚未在公开 LLM 端点上执行——`results/` 目录暂不
> 提供参考报告。读者可按上述命令自行运行以获得当前 LLM 的实测 pass
> rate 与失败聚类。

---

## 6. 实现细节

### 6.1 仓库结构

```
spec2testbench/
├── README.md                            # 本文档
├── pyproject.toml                       # uv 项目；依赖：pydantic / anthropic / openai / pytest / ruff
├── uv.lock                              # 可复现锁文件
│
├── examples/
│   └── 01_diff_pair_ota/                # Step 1 的参考示例
│       ├── trace.md                     # 端到端手工 walkthrough（含 27 项 punch list）
│       ├── dut.cir                      # 被测电路（5 个 MOSFET，level-1 模型）
│       ├── testbench.cir                # 手工编写的完整 ngspice testbench
│       └── testbench.log                # 实际仿真输出
│
├── src/spec2testbench/
│   ├── ir.py                            # TestPlan IR schema + 语义等价
│   ├── extract.py                       # 两个并列 LLM extractor
│   ├── evaluate.py                      # 抽取 IR 与 gold IR 的自动评估
│   └── benchmark/
│       ├── cases.py                     # 20 个基准案例注册表
│       ├── runner.py                    # CLI runner
│       ├── README.md                    # 基准测试使用说明
│       └── results/                     # 运行报告产出目录（gitignored）
│
└── tests/
    ├── conftest.py                      # 共享 fixture（gold IR、NL、DUT metadata）
    ├── test_ir_diff_pair_ota.py         # 9 项：示例 IR round-trip + 每个 gap 的验证
    ├── test_ir_equivalence.py           # 14 项：语义等价的边界行为
    ├── test_ir_extended_primitives.py   # 49 项：所有新原语的正负路径 + cross-ref
    ├── test_benchmark_cases.py          # 86 项：注册表守护
    └── test_extract_live.py             # 2 项：live LLM 抽取（API key 门控）
```

### 6.2 快速开始

环境要求：

- Python ≥ 3.13
- [uv](https://docs.astral.sh/uv/) — Python 项目与虚拟环境管理工具
- [ngspice](https://ngspice.sourceforge.io/) ≥ 45（仅在重跑参考示例仿真
  时需要）
- 至少一个 LLM API key（仅在 live extraction 或 benchmark 运行时需要）

安装：

```bash
git clone <fork-url>.git
cd spec2testbench
uv sync
```

执行全部离线测试：

```bash
uv run pytest
# 预期：157 passed, 2 skipped
```

重跑参考示例的 ngspice 仿真：

```bash
cd examples/01_diff_pair_ota
ngspice -b testbench.cir -o testbench.log
grep -E 'dc_gain_lin|ugb' testbench.log
# 预期：dc_gain_lin = 2.019711e+03   (= 66.11 dB)
#       ugb         = 3.206502e+07   (= 32.07 MHz)
```

### 6.3 测试与质量保证

| 测试文件 | 测试数 | 覆盖范围 |
|---|---:|---|
| `tests/test_ir_diff_pair_ota.py` | 9 | 示例 IR round-trip；每个 schema gap 的验证规则 |
| `tests/test_ir_equivalence.py` | 14 | 语义等价的边界（顺序无关、metadata 忽略、序列敏感性） |
| `tests/test_ir_extended_primitives.py` | 49 | 14 个新原语的正负路径；新 analysis 模型；新 stimulus 类型；cross-ref |
| `tests/test_benchmark_cases.py` | 86 | 20 个案例的 gold IR 构造；原语全覆盖；分布一致性 |
| `tests/test_extract_live.py` | 2 | 实际 LLM 抽取 vs gold IR（gated） |
| **合计** | **160（含 2 个 live skipped）** | |

Lint：`uv run ruff check src/ tests/` 通过。

---

## 7. 路线图

| Step | 描述 | 状态 |
|---|---|---|
| 1 | 选定参考示例，手工跑通端到端，生成 trace | 完成（2026-05-13） |
| 2 | 锁定 IR schema 与语义等价定义 | 完成（2026-05-13） |
| 3 | 实装 Stage 1 抽取 + 自动评估 | 完成（2026-05-14） |
| 4 | 扩展 IR 至 4 类分析、16 原语；构造 20 案例 benchmark；实装 runner | 代码完整（2026-05-15）；benchmark 实际运行待执行 |
| 5 | 实装 Stage 2 emitter（IR → ngspice netlist）+ executability 指标 | 待开始 |
| 6 | 端到端运行；与"直接生成 testbench"基线对比 | 待开始 |
| 7 | 失败模式聚类；确定下一轮迭代重点 | 待开始 |

---

## 8. 设计原则

以下原则贯穿全部代码，作为工程决策的依据：

1. **跨厂商优先（No vendor lock-in）**：以并列的 `extract_with_<provider>`
   函数实现多厂商支持，不引入 Provider 抽象层与胶水库。
2. **严格 schema，尽早失败（Strict schema, fail fast）**：`extra="forbid"`、
   闭集 enum、cross-field validator 共同保证 LLM 输出错位时在最早的边界
   被拒绝。
3. **闭集原语优于表达式 DSL（Closed primitives over expression DSL）**：v0
   只内置 16 个测量原语，扩展须由新示例驱动。
4. **避免过早抽象（No premature abstraction）**：在第 N 个具体实例出现
   之前不引入抽象层。
5. **trace 先于代码（Trace before code）**：每个新示例先以手工方式跑通
   pipeline 并产出 trace，新代码必须有 trace 暴露的需求作为依据。
6. **测试即 schema 验收（Tests are schema-acceptance tests）**：测试关注
   "schema 能否完整表达示例"以及"能否拒绝典型 LLM 错误"，而非测试代码
   逻辑本身。

---

## 9. 常见问题

**Q：为何 v0 仅做到 spec → IR，不直接产出 testbench？**
A：参考示例的 trace 暴露了 27 项设计/工程缺陷，分布在 4 个层次（IR
schema / emitter / PDKContext / 评估器）。同时推进各层会导致每层均不
深入。Step 2 先锁定 IR 层，使 Step 5 的 emitter 在稳定基底上构建。

**Q：为何默认 ngspice 而非 Spectre？**
A：ngspice 为开源工具，便于在 hermetic 环境下复现。`MeasurementPrimitive`
等抽象使得未来 emitter 可同时面向 Spectre、HSPICE 与 ngspice。

**Q：仅持有 OpenRouter 或其他第三方 OpenAI 兼容平台的 key，能否使用？**
A：可。使用 `extract_with_openai_compatible`，传入 `(api_key,
base_url, model)` 三元组即可；`tests/test_extract_live.py` 中的
`OPENAI_COMPAT_*` 环境变量即为此设计。

**Q：是否使用 LangChain？**
A：未使用。LangChain 的抽象层会模糊 prompt caching 信号、错误诊断、
token 用量等本研究关心的细节。两个 30 行并列函数在可控性上明显优于厚
重抽象层。

**Q：如何新增测量原语？**
A：步骤如下：
1. 通过新示例 + trace 暴露该原语的实际需求；
2. 在 `MeasurementPrimitive` enum 中添加新值；
3. 在 `_PRIMITIVE_PARAM_SPEC` 与 `Measurement._primitive_params`
   validator 中声明必填 / 禁用字段；
4. 在 `_SYSTEM_PROMPT` 中补充该原语的语义、必填字段、NL 触发表达；
5. 在 `tests/test_ir_extended_primitives.py` 中补充正负路径测试。

---

[返回顶部 / Back to top](#spec2testbench)

---

# English Section

## 1. Motivation

Automation research in analog integrated circuit design (hereafter
*autoresearch*) relies on a feedback loop in which a design agent — most
plausibly an LLM-based one — proposes a circuit, a simulator evaluates its
performance, and the agent iterates on the result. Within this loop, the
**testbench acts as the adjudicator**: it translates abstract performance
metrics (DC gain, UGB, phase margin, and so on) into the stimulus,
analysis, and verdict logic that a SPICE-class simulator can execute.

Authoring a correct testbench requires precise engineering decisions
across many concerns: symmetric differential excitation, corner coverage,
simulator-dialect pitfalls, and unit consistency. This task is typically
measured in person-hours. Because agent iteration runs in minutes or
seconds, **manual testbench authorship is the critical bottleneck for
autoresearch in this domain**.

`spec2testbench` investigates whether LLMs can automate the translation
"natural-language specification → executable testbench". The work
pursues two complementary objectives:

1. **Capability assessment** — to answer quantitatively whether current
   LLMs are sufficient for this translation.
2. **Diagnostic instrumentation** — when they are not, to produce
   **field-level localisation** of the failure so that subsequent model
   or prompt iteration is targeted rather than speculative.

The translation problem is fundamentally one of *structured semantic
mapping under expert constraints*, an area in which LLMs are presently
the most reliable available tool.

---

## 2. System Overview

The complete pipeline comprises three stages:

```
┌──────────────────────┐
│  Natural-language    │  e.g. "DC gain ≥ 60 dB, UGB ≥ 10 MHz, 1 pF load"
│       spec           │
└──────────┬───────────┘
           │  Stage 1: spec extraction         ← implemented in v0
           ▼
┌──────────────────────┐
│   TestPlan IR        │  strictly-typed JSON, pydantic-validated
└──────────┬───────────┘
           │  + DUT netlist (upstream input)
           │  + PDK context (from the foundry)
           │  Stage 2: testbench emission       ← deferred to Step 5
           ▼
┌──────────────────────┐
│  Executable netlist  │  .cir / .spice
└──────────┬───────────┘
           │  Stage 3: simulate
           ▼
┌──────────────────────┐
│      pass / fail     │  per-stage evaluation: extract / emit / sim / verdict
└──────────────────────┘
```

**Implemented in v0**: Stage 1 (NL → IR) and its automated evaluation
infrastructure. **Deferred**: Stages 2 and 3. The emitter interface is
reserved at the IR boundary and currently raises `NotImplementedError`;
its implementation is scheduled for Step 5.

The intermediate representation lies at the centre of the design: it
expresses *what to test, how to test it, and how to judge the outcome*
in a strictly-typed form that serves as the common contract for LLM
output validation, emitter generation, and benchmark evaluation.

---

## 3. The TestPlan Intermediate Representation

### 3.1 Top-level structure

`TestPlan` consists of seven top-level sections:

```python
class TestPlan(BaseModel):
    meta:          Meta              # metadata and original NL spec text
    dut:           Dut               # DUT identity and port signature
    analyses:      list[Analysis]    # simulation definitions (discriminated union)
    stimulus:      list[Stimulus]    # signal sources
    loading:       list[Loading]     # passive loads
    measurements:  list[Measurement] # scalars derived from analyses
    pass_criteria: list[PassCriterion]  # verdict rules
    corners:       list[Corner]      # process / temperature / supply corners
```

The sectioned structure rather than a flat JSON record was chosen
because two measurements often share a single analysis (for example,
DC gain and UGB both arise from one AC sweep); a flat record cannot
eliminate the resulting redundancy and is prone to inconsistency. The
detailed analysis is documented as Gap-B in
`examples/01_diff_pair_ota/trace.md`.

### 3.2 Analysis-type coverage

The IR currently covers four analysis types, all natively supported by
ngspice:

| Analysis type | IR model | ngspice form |
|---|---|---|
| AC (small-signal sweep) | `AcAnalysis` | `.ac <style> <pts> <f_start> <f_stop>` |
| TRAN (transient) | `TranAnalysis` | `.tran <t_step> <t_stop> <t_start> [uic]` |
| DC (operating point or single-source sweep) | `DcAnalysis` | `.op` or `.dc <Vsrc> <start> <stop> <step>` |
| NOISE (noise sweep) | `NoiseAnalysis` | `.noise v(<out>) <input_src> ...` |

The remaining 23 analyses commonly found in commercial RF-capable
simulators (`stb`, `pss`, `pac`, `pnoise`, `hb`, `qpss`, `envlp`,
`dcmatch`, `acmatch`, `sp`, `xf`, `sens`, `pz`, and so on) are
**deliberately excluded from the v0 enum**. ngspice does not execute
them, and admitting them would create the appearance of capability the
default emitter cannot deliver. They will be added when a future
simulator (Spectre / Xyce / commercial RF simulator) is integrated.

### 3.3 Closed measurement primitives

Concepts such as "DC gain", "UGB", and "phase margin" are not data
fields but **algorithms for extracting scalars from a simulation
curve**. To eliminate the contract drift introduced by free-form
strings, the IR adopts a closed **measurement-primitive vocabulary** of
16 entries:

| Family | Primitive | Semantics | Required parameters |
|---|---|---|---|
| AC | `ac_low_freq_asymptote` | \|H(f)\| at f = f_start | — |
| AC | `ac_freq_at_magnitude_crossing` | frequency at which \|H\| crosses threshold | `target_magnitude`, `direction` |
| AC | `ac_phase_at_freq` | ∠H(f) at a specified frequency | `at_freq` or `at_when_measurement` (one of) |
| AC | `ac_magnitude_at_freq` | \|H(f)\| at a specified frequency | `at_freq` |
| AC | `ac_phase_margin` | 180° + ∠H at UGB | `at_when_measurement` |
| TRAN | `tran_slew_rate` | 10%–90% slope of a transition | `edge` |
| TRAN | `tran_settling_time` | time to remain within ±tolerance | `tolerance_pct`, `trigger_event` |
| TRAN | `tran_overshoot_pct` | overshoot as percentage | — |
| TRAN | `tran_peak_to_peak` | max − min over window | — |
| TRAN | `tran_thd` | total harmonic distortion (single-tone) | `fundamental_freq` |
| DC | `dc_offset_input_referred` | input-referred offset voltage | `target_output_role`, `target_output_value` |
| DC | `dc_output_swing_range` | extreme of output over DC sweep | `extreme` ∈ {min, max, range} |
| DC | `dc_supply_current` | quiescent supply current | `supply_role` |
| DC | `dc_gm` | small-signal transconductance | `input_role`, `output_role`, `at_bias_value` |
| NOISE | `noise_input_referred_at_freq` | input-referred noise PSD | `at_freq` |
| NOISE | `noise_integrated_rms` | RMS noise integrated over a band | `f_low`, `f_high`, `referred_to` |

Each primitive's required-parameter signature is enforced by a
field-level validator on `Measurement`; disallowed parameters are
rejected. A separate `_cross_refs` validator enforces consistency
between a primitive and the type of its referenced `Analysis` (for
example, `tran_slew_rate` cannot reference an AC analysis). Together,
these constraints cause pydantic to reject malformed LLM output at the
earliest boundary with field-localised error messages.

### 3.4 Semantic equivalence

To enable automated benchmark evaluation, the IR defines a semantic-
equivalence relation `semantic_equivalent`:

- `meta.id` and `meta.nl_spec` are ignored, since they are labels
  rather than content.
- The six top-level lists (`analyses`, `stimulus`, `loading`,
  `measurements`, `pass_criteria`, `corners`) are compared as
  **set-like** collections; element order is ignored.
- `dut.subckt_ports` is compared as a **sequence**, since its order is
  semantically significant for SPICE sub-circuit instantiation.
- Implicit and explicit defaults are treated identically (pydantic
  expands them uniformly during serialisation).

When two IRs are not equivalent, the evaluator returns a field-level
diff list, for example:

```
measurements[1].direction: extracted='rising' gold='falling'
pass_criteria[0].value:    extracted=70.0       gold=60.0
```

---

## 4. Cross-Provider LLM Extraction

To avoid coupling to any single LLM vendor or mediator framework, the
extractor module adopts a parallel-function pattern:

```python
def extract_with_anthropic(
    nl_spec: str, dut: DutMetadata, *,
    plan_id: str, api_key: str | None = None,
    model: str = "claude-sonnet-4-6",
) -> TestPlan: ...

def extract_with_openai_compatible(
    nl_spec: str, dut: DutMetadata, *,
    plan_id: str, api_key: str, base_url: str, model: str,
) -> TestPlan: ...
```

Both functions share:

- The same system prompt (`_SYSTEM_PROMPT`), the sole location of
  extraction-related knowledge.
- The same JSON Schema, obtained from `TestPlan.model_json_schema()`
  and supplied to each provider's structured-output mechanism.
- The same pydantic deserialisation and validation logic.

They differ only in their SDK adapters (approximately 30 lines each):
Anthropic's native tool-use API and the OpenAI function-calling API
respectively. Through the `base_url` parameter, the latter is
compatible with OpenAI's native endpoint, the OpenRouter gateway,
Alibaba DashScope, local vLLM deployments, and any service exposing
the OpenAI protocol.

The cost of adding a new provider is one additional 30-line
`extract_with_<provider>` function. The design deliberately omits a
`Provider` base class or factory dispatch: such abstractions yield
their value only after multiple concrete implementations have
crystallised. Comparable mediator libraries (LangChain, LiteLLM)
obscure prompt-cache signalling, token-usage telemetry, and error
diagnostics — all of which are central to this research's questions.

---

## 5. Stage-1 Benchmark

### 5.1 Case construction

To quantify NL → IR extraction accuracy, the repository provides 20
hand-curated benchmark cases with the following distribution:

| Analysis type | Cases | Primitives covered |
|---|---:|---|
| AC    | 7 | all 5 AC primitives |
| TRAN  | 6 | all 5 TRAN primitives + 4 stimulus shapes (PULSE / SINE / STEP / dual-edge) |
| DC    | 5 | all 4 DC primitives + sweep and operating-point modes |
| NOISE | 2 | both NOISE primitives |
| Total | 20 | all 16 primitives; single- and multi-corner coverage |

Each case consists of a triple `(NL spec, DutMetadata, gold IR
builder)`. All cases share the same device under test (the
5-transistor differential-pair OTA), a design choice that isolates the
NL → IR extraction step from confounding effects of varying circuit
topology.

The complete registry resides in
`src/spec2testbench/benchmark/cases.py` and is guarded by offline
parameterised tests in `tests/test_benchmark_cases.py` (86 assertions
covering gold-IR construction, round-tripping, primitive coverage, and
distributional consistency).

### 5.2 Evaluation methodology

The benchmark runner operates as follows:

1. Load the registry (or a user-specified subset).
2. For each case, invoke an extractor (`anthropic` or
   `openai-compatible`).
3. Compare the extracted IR with the gold IR via
   `evaluate_extraction(extracted, gold)`, yielding a Boolean verdict
   and a field-level diff list.
4. Aggregate the results into a JSON and a plain-text report.

The JSON report retains per-case status (pass / fail / error /
skipped), diffs, extraction latency, and the full JSON of the
extracted IR, supporting downstream failure-mode clustering.

### 5.3 Invocation

Offline dry-run (validates every gold IR; no LLM call):

```bash
uv run python -m spec2testbench.benchmark.runner --dry-run
```

Anthropic native endpoint:

```bash
export ANTHROPIC_API_KEY=sk-ant-...
uv run python -m spec2testbench.benchmark.runner \
    --provider anthropic --model claude-sonnet-4-6
```

OpenAI-protocol endpoint (OpenRouter / DashScope / local vLLM / etc.):

```bash
export OPENAI_COMPAT_API_KEY=...
export OPENAI_COMPAT_BASE_URL=https://openrouter.ai/api/v1
uv run python -m spec2testbench.benchmark.runner \
    --provider openai-compatible \
    --model anthropic/claude-sonnet-4-6
```

Subset:

```bash
uv run python -m spec2testbench.benchmark.runner --provider anthropic \
    --case-id a1_diff_pair_gain_ugb --case-id t1_slew_rate_rising
```

Reports are written to `src/spec2testbench/benchmark/results/` as
`<timestamp>_<provider>.json` and the corresponding `.txt`. The JSON
form retains complete structural detail for later analysis.

> **Status note.** The Stage-1 benchmark is **code-complete** as of
> this README's revision, but has not yet been executed against a
> public LLM endpoint. The `results/` directory therefore contains no
> reference report. Readers may execute the commands above to obtain
> measured pass rates and failure clusters for a given model.

---

## 6. Implementation

### 6.1 Repository layout

```
spec2testbench/
├── README.md                            # this document
├── pyproject.toml                       # uv project; pydantic / anthropic / openai / pytest / ruff
├── uv.lock                              # reproducibility lockfile
│
├── examples/
│   └── 01_diff_pair_ota/                # Step 1 reference example
│       ├── trace.md                     # hand-executed walkthrough; 27-item punch list
│       ├── dut.cir                      # DUT netlist (5 MOSFETs, level-1 models)
│       ├── testbench.cir                # hand-written executable ngspice testbench
│       └── testbench.log                # actual simulation output
│
├── src/spec2testbench/
│   ├── ir.py                            # TestPlan IR schema and semantic equivalence
│   ├── extract.py                       # two parallel LLM extractors
│   ├── evaluate.py                      # extracted-IR vs gold-IR evaluator
│   └── benchmark/
│       ├── cases.py                     # 20-case registry
│       ├── runner.py                    # CLI runner
│       ├── README.md                    # benchmark usage notes
│       └── results/                     # report output directory (gitignored)
│
└── tests/
    ├── conftest.py                      # shared fixtures
    ├── test_ir_diff_pair_ota.py         # 9 tests: example round-trip + per-gap validation
    ├── test_ir_equivalence.py           # 14 tests: equivalence boundaries
    ├── test_ir_extended_primitives.py   # 49 tests: new primitives, analyses, stimuli, cross-refs
    ├── test_benchmark_cases.py          # 86 tests: registry guard
    └── test_extract_live.py             # 2 tests: live LLM extraction (API key-gated)
```

### 6.2 Quick start

Prerequisites:

- Python ≥ 3.13
- [uv](https://docs.astral.sh/uv/), the Python project and virtual-
  environment manager
- [ngspice](https://ngspice.sourceforge.io/) ≥ 45 (only required when
  reproducing the reference simulation)
- At least one LLM API key (only required for live extraction or
  benchmark execution)

Install:

```bash
git clone <fork-url>.git
cd spec2testbench
uv sync
```

Run the offline test suite:

```bash
uv run pytest
# expected: 157 passed, 2 skipped
```

Reproduce the reference example's ngspice simulation:

```bash
cd examples/01_diff_pair_ota
ngspice -b testbench.cir -o testbench.log
grep -E 'dc_gain_lin|ugb' testbench.log
# expected: dc_gain_lin = 2.019711e+03   (= 66.11 dB)
#           ugb         = 3.206502e+07   (= 32.07 MHz)
```

### 6.3 Testing and quality

| Test file | Tests | Coverage |
|---|---:|---|
| `tests/test_ir_diff_pair_ota.py` | 9 | reference IR round-trip; per-gap validation |
| `tests/test_ir_equivalence.py` | 14 | semantic-equivalence boundary cases |
| `tests/test_ir_extended_primitives.py` | 49 | 14 new primitives (positive and negative paths); new analyses; new stimuli; cross-refs |
| `tests/test_benchmark_cases.py` | 86 | gold-IR construction; primitive coverage; distributional consistency |
| `tests/test_extract_live.py` | 2 | live LLM extraction (gated) |
| **Total** | **160 (incl. 2 skipped by default)** | |

Linting passes under `uv run ruff check src/ tests/`.

---

## 7. Roadmap

| Step | Description | Status |
|---|---|---|
| 1 | Select a reference example; walk the pipeline end-to-end; produce trace | Complete (2026-05-13) |
| 2 | Lock the IR schema and semantic-equivalence definition | Complete (2026-05-13) |
| 3 | Implement Stage-1 extraction and automated evaluation | Complete (2026-05-14) |
| 4 | Extend the IR to 4 analyses / 16 primitives; construct 20-case benchmark; implement runner | Code-complete (2026-05-15); benchmark execution pending |
| 5 | Implement Stage-2 emitter (IR → ngspice netlist) and executability metric | Pending |
| 6 | End-to-end execution; comparison against direct-generation baseline | Pending |
| 7 | Cluster failure modes; plan the subsequent iteration | Pending |

---

## 8. Design Principles

The following principles inform engineering decisions throughout the
codebase:

1. **Cross-provider portability.** Multi-vendor LLM support is
   implemented by parallel `extract_with_<provider>` functions, not by
   a Provider abstraction layer or a mediator library.
2. **Strict schema, fail fast.** `extra="forbid"`, closed enums, and
   cross-field validators jointly ensure that malformed LLM output is
   rejected at the earliest boundary with field-localised error
   messages.
3. **Closed primitives over expression DSL.** v0 ships 16
   measurement primitives; new primitives are added only when surfaced
   by a new running example.
4. **No premature abstraction.** Abstraction is introduced after a
   pattern crystallises across multiple concrete implementations, not
   before.
5. **Trace before code.** Each new running example begins with a
   manual end-to-end walkthrough whose trace document records the
   constraints subsequent code must satisfy.
6. **Tests as schema-acceptance suites.** Tests verify that the schema
   can express each example and reject typical LLM errors, rather than
   testing code logic in isolation.

---

## 9. Frequently asked questions

**Q. Why does v0 stop at NL → IR rather than producing executable
testbenches directly?**
A. The reference trace surfaced 27 engineering gaps distributed across
four layers (IR schema / emitter / PDKContext / evaluator). Addressing
them simultaneously would result in superficial coverage of each
layer. Step 2 first stabilises the IR layer; the emitter (Step 5) is
then built on a settled foundation.

**Q. Why ngspice rather than Spectre as the default simulator?**
A. ngspice is open-source and supports hermetic reproduction. The
`MeasurementPrimitive` abstraction is intentionally simulator-
neutral, so a future emitter can target Spectre, HSPICE, or ngspice
through the same IR.

**Q. May I use this codebase with an OpenRouter or other third-party
OpenAI-compatible key?**
A. Yes. Invoke `extract_with_openai_compatible(...)` with the
`(api_key, base_url, model)` triple. The `OPENAI_COMPAT_*` environment
variables in `tests/test_extract_live.py` are designed for this case.

**Q. Why is LangChain not used?**
A. LangChain's abstractions obscure several signals central to this
research (prompt-cache hit rate, token usage, structured-output
error diagnostics). Two parallel 30-line functions provide finer
control with substantially less indirection.

**Q. How does one add a new measurement primitive?**
A. The procedure is:
1. Surface the primitive's need via a new running example and trace.
2. Add the new value to the `MeasurementPrimitive` enum.
3. Declare the primitive's required and disallowed fields in
   `_PRIMITIVE_PARAM_SPEC`, which drives the
   `Measurement._primitive_params` validator.
4. Document the primitive in `_SYSTEM_PROMPT`, including its
   semantics, required fields, and NL trigger phrases.
5. Add positive- and negative-path tests in
   `tests/test_ir_extended_primitives.py`.

---

[Back to top](#spec2testbench)
