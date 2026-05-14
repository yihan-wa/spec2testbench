<div align="center">

# spec2testbench

**让 LLM 把自然语言模拟电路测试需求，自动翻译成可执行的 testbench。**
**Turn natural-language analog test specs into executable testbenches, automatically, via LLMs.**

🇨🇳 **中文（本部分）** &nbsp;·&nbsp; [🇬🇧 English version below ↓](#english-version)

</div>

---

## 目录

- [一句话介绍](#一句话介绍)
- [为什么做这个项目](#为什么做这个项目)
- [完整愿景：图 2 的 pipeline](#完整愿景图-2-的-pipeline)
- [当前实际做到了什么（v0）](#当前实际做到了什么v0)
- [快速上手](#快速上手)
- [代码库结构](#代码库结构)
- [核心组件详解](#核心组件详解)
  - [IR：项目的心脏](#ir项目的心脏)
  - [闭集测量原语](#闭集测量原语)
  - [跨厂商 LLM extractor](#跨厂商-llm-extractor)
  - [自动评估器](#自动评估器)
  - [Running example：5-管差分对 OTA](#running-example5-管差分对-ota)
- [设计原则](#设计原则)
- [路线图](#路线图)
- [测试与质量](#测试与质量)
- [常见问题](#常见问题)
- [鸣谢与参考](#鸣谢与参考)

---

## 一句话介绍

**`spec2testbench` 是一个研究项目，目的是回答：LLM 能不能把"DC gain > 60 dB"这种人话需求，自动翻译成 ngspice/Spectre/HSPICE 能直接运行的 testbench 文件？**

如果答案是 **能**，那么 LLM-driven 的模拟电路自动化设计（autoresearch）这条路就能跑通——因为 agent 终于有了能"自我迭代验证"的反馈环。
如果答案是 **不能**，至少这个仓库提供了一套**可量化诊断**框架，告诉你 **LLM 到底卡在哪一步**——是看不懂 spec？编不出原语？接错端口？还是搞不定方言？

---

## 为什么做这个项目

电路设计中的"autoresearch"愿景：

```
            ┌────────────┐
            │  LLM agent │  "我设计一版电路"
            └─────┬──────┘
                  │
                  ▼ (这里需要 testbench)
            ┌─────────────┐
            │  仿真反馈   │  "DC gain 实测 43 dB, UGB 12 MHz"
            └─────┬───────┘
                  │
                  ▼
            ┌─────────────┐
            │  Agent 学习  │  "gain 不够，改大 W/L 再试"
            └─────┬───────┘
                  │   (回到上一步)
                  └─────►
```

**这个循环里，testbench 是评判员。Agent 改 DUT，testbench 跑测试给反馈，agent 据此迭代。**

但有一个瓶颈：**写一份"对的" testbench 需要专家手工做几小时**——要写对差分激励、要选对 corner、要避开仿真器方言陷阱、要……。Agent 一晚迭代 500 次，人写 testbench 一周写 5 份，**人是 autoresearch 的瓶颈**。

`spec2testbench` 就是把"人话需求 → testbench"这一步**自动化**。

为什么基于 Claude / LLM？因为这一步本质是**带专家知识的结构化翻译**——LLM 是目前唯一能在这个语义层稳定操作的工具。

---

## 完整愿景：图 2 的 pipeline

```
┌──────────────┐
│   NL spec    │   "DC gain should exceed 60 dB, UGB ≥ 10 MHz with 1pF load..."
└──────┬───────┘
       │  Stage 1: spec extraction        ← 本仓库 v0 实现的部分
       ▼
┌──────────────┐
│  TestPlan IR │   结构化 JSON，pydantic 验证
└──────┬───────┘
       │
       │  + DUT netlist (上游给)
       │  + PDK context (PDK 提供)
       │
       │  Stage 2: testbench emission      ← v0 未实现，留给 Step 5
       ▼
┌──────────────────┐
│ Executable spice │   .cir / .spice 文件
│   testbench      │   首选 Spectre，次选 HSPICE，ngspice 兜底
└──────┬───────────┘
       │  Stage 3: simulate
       ▼
┌──────────────────┐
│  pass / fail     │   分阶段评估：extract 对吗？emit 对吗？sim 对吗？verdict 对吗？
└──────────────────┘
```

完整愿景的具体描述见仓库根目录的 `image1.png` 和 `image2.png`，以及 `docs/project_overview.md`。

---

## 当前实际做到了什么（v0）

**v0 范围：spec → IR 这一条**（图 2 上半段）。下半段（IR → 可执行 netlist → 仿真 → verdict）作为后续步骤分阶段实装。

具体落地的三件事：

### ① 一份手工 walkthrough（Step 1）

用一个真实例子（5-管差分对 OTA + DC gain & UGB 两条 spec），**手工**把整条 pipeline 从 NL 走到 ngspice 仿真结果，过程中每撞到一个"图上没说但实际必须解决"的问题就**系统记录下来**。

撞到 **27 条 punch list**：
- 11 条 **schema gaps**（IR 该长什么样）
- 14 条 **emitter knowledge debts**（IR → netlist 翻译要从哪补充信息）
- 2 条 **evaluator knowledge debts**（单位换算 / 操作符语义）

详见：`examples/01_diff_pair_ota/trace.md`（380+ 行）

### ② 严格的 IR schema（Step 2）

用 pydantic v2 把"图 2 中间那个 JSON"的形态彻底锁死，**11 条 schema gap 中 10 条就地解决**，剩 1 条（VDD/bias/Vcm）显式推到未来的 PDKContext。

代码：`src/spec2testbench/ir.py`（约 440 行）
- 11 个 pydantic models
- 8 个 enum（约束闭集值）
- 5 个 cross-field validator（强制跨表引用合法）
- `semantic_equivalent(a, b)` —— 自动判断两个 IR 是否"语义等价"，用于 benchmark

### ③ 跨厂商 LLM extractor + 自动评估（Step 3）

**两个并列的 extractor**，同一份系统提示词、同一份 schema，只是接的 SDK 不同：

| 函数 | 接什么 |
|---|---|
| `extract_with_anthropic` | Anthropic 直连 (api.anthropic.com)，带 prompt caching |
| `extract_with_openai_compatible` | OpenAI 协议端点：OpenAI 直连 / OpenRouter / Xiaomi MiMo / Alibaba DashScope / 本地 vLLM——任何说 OpenAI 协议的服务 |

代码：`src/spec2testbench/extract.py` + `src/spec2testbench/evaluate.py`

---

## 快速上手

### 环境要求

- Python 3.13+
- [uv](https://docs.astral.sh/uv/)（包/虚拟环境管理）
- [ngspice](https://ngspice.sourceforge.io/) 45+（如果要跑 running example 仿真）
- 至少一个 LLM API key（Anthropic / OpenRouter / 其他 OpenAI 兼容平台），如果要跑 live extraction

### 安装

```bash
git clone <your-fork-url>.git
cd spec2testbench
uv sync             # 安装所有依赖到 .venv/
```

### 跑基础测试（不需要 API key）

```bash
uv run pytest -v
# 期望: 23 passed, 2 skipped (live tests 没 key 会自动 skip)
```

`23 passed` 表示：IR schema 能完整表达 running example、JSON round-trip 守恒、所有 gap 验证规则生效、semantic equivalence 满足所有边界。

### 跑 live extraction（需要 API key）

**Anthropic 直连：**
```bash
export ANTHROPIC_API_KEY=sk-ant-...
uv run pytest tests/test_extract_live.py::test_extract_with_anthropic -v -s
```

**OpenRouter（任意模型，含 Claude）：**
```bash
export OPENAI_COMPAT_API_KEY=sk-or-...
export OPENAI_COMPAT_BASE_URL=https://openrouter.ai/api/v1
export OPENAI_COMPAT_MODEL=anthropic/claude-sonnet-4.6
uv run pytest tests/test_extract_live.py::test_extract_with_openai_compatible -v -s
```

**Xiaomi MiMo（假设 OpenAI 兼容）：**
```bash
export OPENAI_COMPAT_API_KEY=...
export OPENAI_COMPAT_BASE_URL=https://<xiaomi-endpoint>/v1
export OPENAI_COMPAT_MODEL=<xiaomi-model-id>
uv run pytest tests/test_extract_live.py::test_extract_with_openai_compatible -v -s
```

### 手工跑一遍 running example 的 ngspice 仿真

```bash
cd examples/01_diff_pair_ota
ngspice -b testbench.cir -o testbench.log
grep -E 'dc_gain_lin|ugb' testbench.log
# 期望: dc_gain_lin = 2.019711e+03   (= 66.11 dB)
#       ugb         = 3.206502e+07   (= 32.07 MHz)
```

---

## 代码库结构

```
spec2testbench/
├── README.md                          ← 你正在读的这份
│
├── image1.png  image2.png             ← 项目动机 + 技术路线图（原始）
│
├── pyproject.toml                     ← uv 项目配置；deps: pydantic / anthropic / openai / pytest / ruff
├── uv.lock                            ← 依赖锁文件（committed for reproducibility）
├── .python-version                    ← 锁定 3.13
│
├── docs/
│   └── project_overview.md            ← 当前项目状态 vs 图 1/2 目标的完整对照
│
├── examples/
│   └── 01_diff_pair_ota/              ← Running example：5-管差分对 OTA
│       ├── trace.md                   ← 端到端 walkthrough + 27 条 punch list
│       ├── dut.cir                    ← DUT netlist（5 个 MOSFET，level-1 模型）
│       ├── testbench.cir              ← 手写完整 testbench（ngspice 可跑）
│       └── testbench.log              ← 实际仿真输出（DC gain ≈ 66 dB, UGB ≈ 32 MHz）
│
├── src/spec2testbench/
│   ├── __init__.py
│   ├── ir.py                          ← 核心：TestPlan IR schema + semantic equivalence
│   ├── extract.py                     ← 两个并列 LLM extractor
│   └── evaluate.py                    ← 自动评估：抽出的 IR vs gold IR
│
└── tests/
    ├── conftest.py                    ← 共享 fixtures（gold IR、NL、DUT metadata）
    ├── test_ir_diff_pair_ota.py       ← 9 个：gold IR round-trip + 各 gap 验证
    ├── test_ir_equivalence.py         ← 14 个：semantic equivalence 各边界
    └── test_extract_live.py           ← 2 个：live LLM extraction（gated 在 API key）
```

---

## 核心组件详解

### IR：项目的心脏

`TestPlan` IR 是图 2 中间那个结构化 JSON 的严格类型化形式。它把"测什么、怎么测、怎么判断通过"用 **7 个顶层 section** 表达出来：

```python
class TestPlan(BaseModel):
    meta:           Meta            # 元数据 + 原始 NL spec
    dut:            Dut             # 被测电路 + 端口签名
    analyses:       list[AcAnalysis]  # 仿真定义
    stimulus:       list[Stimulus]    # 激励
    loading:        list[Loading]     # 负载
    measurements:   list[Measurement] # 测量（用闭集原语）
    pass_criteria:  list[PassCriterion]  # 判定
    corners:        list[Corner]      # 工艺角
```

**为什么是 7 个 section 而不是图 2 那种扁平 JSON？** 因为只要有 ≥ 2 个测量共享一次仿真（典型情况：DC gain 和 UGB 都来自同一次 AC 扫频），扁平形态就崩了。详见 `examples/01_diff_pair_ota/trace.md` §2 中的 Gap-B 分析。

**强约束：**
- `extra="forbid"` —— 多写一个 schema 没定义的字段直接拒绝
- 5 个 cross-field validator —— 例如 measurement 引用的 analysis id 必须存在
- 闭集枚举 —— stimulus kind、measurement primitive、comparison op 都是限定取值

这些约束的目的是：**LLM 抽错时给出精确、可反馈的错误信息**，便于未来在 agent loop 里自动修正。

### 闭集测量原语

最重要的一个设计决策。"DC gain"、"UGB" 在物理上不是字段，是**取数方式**。直接用自然字符串会让 emitter / evaluator 之间约定崩塌。所以引入 **closed primitive vocabulary**：

```python
class MeasurementPrimitive(str, Enum):
    AC_LOW_FREQ_ASYMPTOTE       = "ac_low_freq_asymptote"        # → DC gain
    AC_FREQ_AT_MAGNITUDE_CROSSING = "ac_freq_at_magnitude_crossing"  # → UGB / -3dB
```

每个原语：
- 语义闭合（精确定义"如何从仿真曲线取数"）
- 单位明确（output_unit 是 IR 字段，必填）
- 参数必填（crossing 类必须带 direction，杜绝 Gap-G 的方向丢失）

v0 只内置 2 个原语——只为当前 running example 服务。后续 example 出现新测量需求时再扩展（YAGNI）。

### 跨厂商 LLM extractor

**严格遵循 [`prefer-cross-provider-portability`](./.claude/projects/-Users-eulerone-Documents-spec2testbench/memory/prefer_cross_provider_portability.md) 原则**：**不**做厂商抽象层，**不**用 LangChain/LiteLLM 这种胶水库；写两个并列函数，签名完全一致：

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

**两者共享：**
- 同一份系统提示词（`_SYSTEM_PROMPT`）——所有"如何抽取"的知识都在这里
- 同一份 schema：`TestPlan.model_json_schema()`——两边的 structured-output 机制都吃它
- 同一份 pydantic 验证：`TestPlan.model_validate(...)`

**两者唯一区别**：调 SDK 那约 30 行代码不同（Anthropic 用 `tools[].input_schema` + `tool_choice`，OpenAI 兼容用 `tools[].function.parameters` + `tool_choice`）。

**未来加新厂商** = 新增一个 `extract_with_<provider>`，30 行，签名一致。

### 自动评估器

`evaluate.py` 提供：

```python
def evaluate_extraction(extracted: TestPlan, gold: TestPlan) -> EvaluationReport:
    """返回 (equivalent: bool, differences: tuple[str, ...])。"""
```

底层用 `ir.semantic_equivalent()`，等价规则：
- 忽略 `meta.id` 和 `meta.nl_spec`（标签，不是内容）
- 6 个顶层列表当**无序集合**比较（analyses / stimulus / loading / measurements / pass_criteria / corners）
- `dut.subckt_ports` 当**有序序列**比较（SPICE 调用顺序敏感）
- 隐式默认值 ≡ 显式默认值（pydantic 自动填）

不等价时，`EvaluationReport.differences` 会列出**字段级**的 diff，例如：

```
- measurements[1].direction: extracted='rising' gold='falling'
- pass_criteria[0].value: extracted=70.0 gold=60.0
```

这让 benchmark 失败时**能立刻看出 LLM 抽错在哪一步**，不止"对/错"的二元判断。

### Running example：5-管差分对 OTA

整个项目的"地基"。一个教科书电路 + 两条简单 spec，**手工**跑通图 2 整条 pipeline。产出的 `trace.md` 是 380+ 行的"工程日志"，记录每个阶段的：

- 输入是什么
- 我手工写了什么
- 过程中卡在哪里
- 当前怎么 hack 过去
- 这暗示 schema 需要什么

**正是这份 trace 决定了 IR schema 长什么样**——没有 trace，schema 只能凭空猜。

| 文件 | 干啥的 |
|---|---|
| `trace.md` | 端到端 walkthrough，27 条 punch list |
| `dut.cir` | 被测电路（5 个 MOSFET + level-1 模型） |
| `testbench.cir` | 完整 testbench（ngspice 可执行） |
| `testbench.log` | 实际仿真输出 |

实测结果：**DC gain = 66.11 dB（spec > 60 dB → PASS），UGB = 32.07 MHz（spec ≥ 10 MHz → PASS）**。

---

## 设计原则

> 这些原则**贯穿全部代码**，不是装饰。

1. **跨厂商优先（No vendor lock-in）**
   永远写并列函数 `extract_with_<provider>`，不写厂商抽象层。详见 `memory/prefer_cross_provider_portability.md`。

2. **schema 严格（Strict schema, fail fast）**
   pydantic `extra="forbid"` + 闭集枚举 + cross-field validator。LLM 抽错时**在最早的边界**拒绝，并给出精确错误信息。

3. **闭集原语，不用 DSL（Closed primitives, not expression DSL）**
   v0 只内置 2 个测量原语，比"自由表达式 DSL"更可控。新原语随新 example 出现按需扩展。

4. **不做过早抽象（No premature abstraction）**
   `extract_with_anthropic` 和 `extract_with_openai_compatible` 完全并列、有重复，但**没有**`Provider` 接口、**没有**`LLMClient` 工厂、**没有**LangChain。第 N 个 provider 真出现时再考虑抽象。

5. **trace 优先于代码（Trace before code）**
   每个新 example 都先手工跑通 + 写 trace；新代码必须**有 trace 撞出的需求作为依据**。

6. **测试是 schema 的 acceptance test（Tests are schema-acceptance tests）**
   不是测代码逻辑——是验证"schema 能不能完整表达这个 example、能不能挡住典型的 LLM 错误"。

---

## 路线图

跟随原始 7 步路线推进：

| Step | 描述 | 状态 |
|---|---|---|
| 1 | 选定 1 个 running example，手工跑通端到端 → 出 trace | ✅ 完成 |
| 2 | 基于 trace 固化 IR schema 和评估准则（语义等价定义） | ✅ 完成 |
| 3 | 实装 Stage 1 (LLM extract) + 自动评估 | ✅ 完成 |
| 4 | 扩 10–20 个种子 case，跑 Stage 1 benchmark | ⏳ 下一步 |
| 5 | 实装 Stage 2 emit (ngspice) + executability 指标 | ⏳ |
| 6 | 端到端跑，对比 IR-路径 vs 直接生成路径 | ⏳ |
| 7 | 根据失败模式聚类，决定下一轮迭代重点 | ⏳ |

---

## 测试与质量

| 文件 | 测试数 | 测什么 |
|---|---|---|
| `tests/test_ir_diff_pair_ota.py` | 9 | gold IR 能用 schema 表达、JSON round-trip 守恒、每个 gap 的验证规则生效 |
| `tests/test_ir_equivalence.py` | 14 | semantic equivalence 各种边界（顺序无关、metadata 忽略、顺序敏感性、值差异） |
| `tests/test_extract_live.py` | 2 | 实际 LLM 抽取 + 与 gold IR 对比；gated 在 API key |

**Lint**：`ruff` 配置在 pyproject.toml；`uv run ruff check src/ tests/` 通过。

跑测试：
```bash
uv run pytest -v          # 全跑（live 测试 skip 如果没 key）
uv run ruff check         # lint
```

---

## 常见问题

**Q: 为什么 v0 只做到 spec → IR？图 2 不是要直接出 testbench 吗？**
A: trace 撞出来 27 条问题分布在 4 个不同 layer（IR schema / emitter / PDKContext / evaluator）。一次性都做意味着每一层都做不深。Step 2 先把 IR 这层做扎实，下一步（Step 5）才有稳定底座做 emitter。

**Q: 为什么用 ngspice 而不是 Spectre？**
A: ngspice 是 open-source fallback，hermetic 测试方便。v0 用 ngspice 不代表项目长期是 ngspice-only——`MeasurementPrimitive` 抽象就是为了让未来 emitter 同时支持 Spectre / HSPICE / ngspice。

**Q: 我没有 Anthropic key，只有 OpenRouter / Xiaomi MiMo key，能用吗？**
A: 能。用 `extract_with_openai_compatible` 函数，传你的 `(api_key, base_url, model)` 三元组。`tests/test_extract_live.py` 里的 OPENAI_COMPAT_* 环境变量就是为这个准备的。

**Q: 为什么不用 LangChain？**
A: LangChain 抽象层太厚，对一个**研究**项目（要精细比较不同 LLM 在 testbench 生成上的表现）反而是负担：prompt caching 信号、错误诊断、token usage 都被屏蔽了。两个 30 行的并列函数比 LangChain 简单 10 倍且更可控。

**Q: 我能加新的测量原语吗？**
A: 能但请慎重——闭集原语是 schema 的核心约束。流程：
1. 写一个新 running example 把新原语撞出来
2. 在 `MeasurementPrimitive` enum 加新值
3. 在 `Measurement._primitive_params` validator 加参数验证
4. 在 `_SYSTEM_PROMPT` 加新原语描述 + 用法
5. 加对应的 test 验证 schema 正确性

---

## 鸣谢与参考

- 项目动机来自一段微信聊天（见 `image1.png`）
- 技术路线来自后续讨论（见 `image2.png`）
- 「Claude testbench 能力强于 mimo v2.5 pro」这一观察来自 **张托肯** 与作者的交流（参见 `image1.png`）

---

<div align="center">

[⬆ 回到顶部](#spec2testbench)  &nbsp;·&nbsp;  [English version ↓](#english-version)

</div>

---

# English Version

<div align="center">

**Turn natural-language analog test specs into executable testbenches, automatically, via LLMs.**

[🇨🇳 中文版 ↑](#spec2testbench) &nbsp;·&nbsp; 🇬🇧 **English (this section)**

</div>

---

## Contents

- [One-line pitch](#one-line-pitch)
- [Why this project exists](#why-this-project-exists)
- [The full vision (image 2)](#the-full-vision-image-2)
- [What v0 actually delivers](#what-v0-actually-delivers)
- [Quick start](#quick-start)
- [Repository layout](#repository-layout)
- [Component deep dives](#component-deep-dives)
  - [The IR — heart of the project](#the-ir--heart-of-the-project)
  - [Closed measurement primitives](#closed-measurement-primitives)
  - [Cross-provider LLM extractor](#cross-provider-llm-extractor)
  - [Automated evaluator](#automated-evaluator)
  - [Running example: 5-T differential-pair OTA](#running-example-5-t-differential-pair-ota)
- [Design principles](#design-principles)
- [Roadmap](#roadmap)
- [Testing & quality](#testing--quality)
- [FAQ](#faq)
- [Acknowledgements](#acknowledgements)

---

## One-line pitch

**`spec2testbench` is a research codebase asking: can an LLM reliably turn an
informal sentence like "DC gain > 60 dB, UGB ≥ 10 MHz with 1 pF load" into
a ngspice/Spectre/HSPICE testbench file that actually runs?**

If yes, the autoresearch loop for analog IC design closes — agents finally
have a self-iterating feedback loop. If no, this repo at least gives you
a **quantitative diagnostic framework** showing exactly *where* the LLM
breaks: misunderstanding the spec, picking the wrong primitive, mis-wiring
ports, or tripping over a dialect quirk.

---

## Why this project exists

The autoresearch vision in analog design:

```
            ┌────────────┐
            │  LLM agent │  "Let me try this sizing."
            └─────┬──────┘
                  │
                  ▼ (this is where you need a testbench)
            ┌─────────────┐
            │ sim feedback│  "DC gain = 43 dB, UGB = 12 MHz."
            └─────┬───────┘
                  │
                  ▼
            ┌─────────────┐
            │ agent learns│  "Gain too low — increase W/L, try again."
            └─────┬───────┘
                  │
                  └─────► back to the top
```

**Inside this loop the testbench is the judge.** The agent changes the DUT,
the testbench measures, the agent learns from the numbers.

The bottleneck: **writing a correct testbench takes an expert several hours
per spec** — getting differential stimulus right, picking corners, avoiding
simulator-dialect traps, and so on. Agents iterate hundreds of times per
night; a human writes ~5 testbenches per week. **The human is the bottleneck
of autoresearch.**

`spec2testbench` is about automating that one step — natural-language spec
→ correct testbench — so the agent loop can actually close.

Why LLMs? Because this step is fundamentally **structured translation with
embedded expert knowledge** — and LLMs are currently the only tool that
operates reliably at this semantic level.

---

## The full vision (image 2)

```
┌──────────────┐
│   NL spec    │   "DC gain should exceed 60 dB, UGB ≥ 10 MHz with 1pF load..."
└──────┬───────┘
       │  Stage 1: spec extraction         ← what v0 implements
       ▼
┌──────────────┐
│  TestPlan IR │   strict JSON, pydantic-validated
└──────┬───────┘
       │
       │  + DUT netlist (upstream input)
       │  + PDK context (from the PDK)
       │
       │  Stage 2: testbench emission       ← not yet implemented (Step 5)
       ▼
┌──────────────────┐
│ Executable spice │   .cir / .spice file
│   testbench      │   Spectre first, HSPICE next, ngspice fallback
└──────┬───────────┘
       │  Stage 3: simulate
       ▼
┌──────────────────┐
│  pass / fail     │   fine-grained eval: was extract right? emit? sim? verdict?
└──────────────────┘
```

The original screenshots driving this vision are committed at the repo root
as `image1.png` and `image2.png`, with a full mapping to current code in
`docs/project_overview.md`.

---

## What v0 actually delivers

**v0 scope: spec → IR only** (top half of the pipeline). The bottom half
(IR → executable netlist → simulator → verdict) is deferred to later steps.

Concretely:

### ① A hand-run end-to-end example (Step 1)

We took one realistic case — a 5-transistor differential-pair OTA with two
specs (DC gain & UGB) — and **walked the entire pipeline by hand**: write
the NL, write the IR, write the DUT, write the testbench, run ngspice,
extract measurements, judge pass/fail. Every time something on the figure
turned out to be silently missing, we wrote it down.

The walkthrough produced a **27-item punch list**:
- 11 **schema gaps** (what the IR needs to express)
- 14 **emitter knowledge debts** (info IR → netlist translation needs from elsewhere)
- 2 **evaluator knowledge debts** (unit conversion, operator semantics)

See `examples/01_diff_pair_ota/trace.md` (380+ lines).

### ② A strict IR schema (Step 2)

A pydantic v2 schema that locks down the "structured TestPlan" shape from
image 2. **10 of the 11 schema gaps are resolved in-IR**; the last one
(VDD / bias / Vin_cm) is explicitly deferred to a future `PDKContext` data
structure.

Code: `src/spec2testbench/ir.py` (~440 lines)
- 11 pydantic models
- 8 enums (closed-value constraints)
- 5 cross-field validators (enforce inter-table reference integrity)
- `semantic_equivalent(a, b)` for benchmark-time IR comparison

### ③ Cross-provider LLM extractor + auto-evaluator (Step 3)

**Two parallel extractors** sharing the same system prompt, the same schema,
the same validation. Only the SDK wrapper differs:

| Function | Endpoint |
|---|---|
| `extract_with_anthropic` | Anthropic direct (api.anthropic.com), with prompt caching |
| `extract_with_openai_compatible` | Any OpenAI-protocol endpoint: OpenAI direct / OpenRouter / Xiaomi MiMo / Alibaba DashScope / local vLLM / etc. |

Code: `src/spec2testbench/extract.py` + `src/spec2testbench/evaluate.py`.

---

## Quick start

### Requirements

- Python 3.13+
- [uv](https://docs.astral.sh/uv/) (Python project / venv manager)
- [ngspice](https://ngspice.sourceforge.io/) 45+ (only to rerun the running-example simulation)
- At least one LLM API key (Anthropic / OpenRouter / any OpenAI-compatible) — only to run live extraction tests

### Install

```bash
git clone <your-fork-url>.git
cd spec2testbench
uv sync
```

### Run the offline tests (no API key needed)

```bash
uv run pytest -v
# expect: 23 passed, 2 skipped
```

The 23 passes mean: the IR schema can express the running example, JSON
round-trips lossless, every gap validation fires correctly, and semantic
equivalence holds across all boundary cases.

### Run live extraction (API key required)

**Anthropic direct:**
```bash
export ANTHROPIC_API_KEY=sk-ant-...
uv run pytest tests/test_extract_live.py::test_extract_with_anthropic -v -s
```

**OpenRouter (any model, e.g. Claude via the gateway):**
```bash
export OPENAI_COMPAT_API_KEY=sk-or-...
export OPENAI_COMPAT_BASE_URL=https://openrouter.ai/api/v1
export OPENAI_COMPAT_MODEL=anthropic/claude-sonnet-4.6
uv run pytest tests/test_extract_live.py::test_extract_with_openai_compatible -v -s
```

**Xiaomi MiMo or any other OpenAI-compatible platform:**
```bash
export OPENAI_COMPAT_API_KEY=...
export OPENAI_COMPAT_BASE_URL=https://<their-endpoint>/v1
export OPENAI_COMPAT_MODEL=<their-model-id>
uv run pytest tests/test_extract_live.py::test_extract_with_openai_compatible -v -s
```

### Rerun the running-example ngspice simulation

```bash
cd examples/01_diff_pair_ota
ngspice -b testbench.cir -o testbench.log
grep -E 'dc_gain_lin|ugb' testbench.log
# expected: dc_gain_lin = 2.019711e+03   (= 66.11 dB)
#           ugb         = 3.206502e+07   (= 32.07 MHz)
```

---

## Repository layout

```
spec2testbench/
├── README.md                          ← you are here
│
├── image1.png  image2.png             ← original motivation + pipeline screenshots
│
├── pyproject.toml                     ← uv project; deps: pydantic / anthropic / openai / pytest / ruff
├── uv.lock                            ← committed for reproducibility
├── .python-version                    ← pinned to 3.13
│
├── docs/
│   └── project_overview.md            ← detailed mapping: image-2 targets vs current code
│
├── examples/
│   └── 01_diff_pair_ota/              ← Running example: 5-T diff-pair OTA
│       ├── trace.md                   ← end-to-end walkthrough + 27 punch-list items
│       ├── dut.cir                    ← DUT netlist (5 MOSFETs, level-1 models)
│       ├── testbench.cir              ← hand-written full testbench (runs in ngspice)
│       └── testbench.log              ← actual sim output (DC gain ≈ 66 dB, UGB ≈ 32 MHz)
│
├── src/spec2testbench/
│   ├── __init__.py
│   ├── ir.py                          ← TestPlan IR schema + semantic equivalence
│   ├── extract.py                     ← two parallel LLM extractors
│   └── evaluate.py                    ← extracted-IR vs gold-IR auto evaluator
│
└── tests/
    ├── conftest.py                    ← shared fixtures (gold IR, NL, DUT metadata)
    ├── test_ir_diff_pair_ota.py       ← 9 tests: gold IR round-trip + per-gap validation
    ├── test_ir_equivalence.py         ← 14 tests: equivalence boundaries
    └── test_extract_live.py           ← 2 tests: live LLM extraction (gated on API keys)
```

---

## Component deep dives

### The IR — heart of the project

`TestPlan` is the strictly-typed form of the structured JSON in image 2.
Seven top-level sections cover *what to test, how to test it, and how to
judge*:

```python
class TestPlan(BaseModel):
    meta:          Meta              # metadata + original NL spec text
    dut:           Dut               # DUT identity + port signature
    analyses:      list[AcAnalysis]  # simulations to run
    stimulus:      list[Stimulus]    # signal sources
    loading:       list[Loading]     # passive loads
    measurements:  list[Measurement] # scalars derived from analyses
    pass_criteria: list[PassCriterion]  # verdict rules
    corners:       list[Corner]      # PVT corners
```

**Why 7 sections instead of image-2's flat JSON?** As soon as two
measurements share one analysis (e.g. DC gain and UGB both from one AC
sweep), the flat form breaks. See Gap-B in `examples/01_diff_pair_ota/trace.md` §2.

**Strict by design:**
- `extra="forbid"` — extra fields are rejected outright
- 5 cross-field validators — e.g. `measurement.from_analysis` must
  reference an existing analysis id
- Closed enums — stimulus kind, measurement primitive, comparison op are
  all enum-constrained

The purpose is to **fail fast at the boundary with precise, machine-
reusable error messages** the agent loop can act on.

### Closed measurement primitives

The single most important design decision. "DC gain" and "UGB" are not
fields — they are *recipes for extracting numbers from a simulation
curve*. Using free-form strings would silently break the emitter /
evaluator contract. So we introduce a **closed primitive vocabulary**:

```python
class MeasurementPrimitive(str, Enum):
    AC_LOW_FREQ_ASYMPTOTE         = "ac_low_freq_asymptote"         # → DC gain
    AC_FREQ_AT_MAGNITUDE_CROSSING = "ac_freq_at_magnitude_crossing" # → UGB / -3dB
```

Each primitive:
- Has closed semantics (precisely defines *how* to extract from the curve)
- Has an explicit `output_unit` (required IR field)
- Has required parameters validated by pydantic (crossing primitives must
  carry `direction` — closing Gap-G's silent-bug class)

v0 ships exactly **2** primitives — enough for the current running example.
New primitives get added deliberately, when a new running example surfaces
them (YAGNI).

### Cross-provider LLM extractor

Per the memory rule `prefer-cross-provider-portability`: **no provider
abstraction layer, no LangChain/LiteLLM glue.** Two parallel functions
with identical signatures:

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

**They share:**
- The same `_SYSTEM_PROMPT` — all "how to extract" knowledge lives there
- The same schema: `TestPlan.model_json_schema()` — fed to both providers'
  structured-output mechanism
- The same `TestPlan.model_validate(...)` for the response

**The only difference is the ~30 lines of SDK-specific code:**
Anthropic uses native `tools[].input_schema` + `tool_choice`; OpenAI-
compatible uses `tools[].function.parameters` + `tool_choice`.

**Adding a new provider** = add another `extract_with_<name>` function,
30 lines, same signature. No factory dispatch, no shared base class.

### Automated evaluator

`evaluate.py` provides:

```python
def evaluate_extraction(extracted: TestPlan, gold: TestPlan) -> EvaluationReport:
    """Returns (equivalent: bool, differences: tuple[str, ...])."""
```

Under the hood, `ir.semantic_equivalent()` enforces these rules:
- Ignores `meta.id` and `meta.nl_spec` (labels, not content)
- Treats the 6 top-level lists as **set-like** (analyses / stimulus /
  loading / measurements / pass_criteria / corners — order ignored)
- Treats `dut.subckt_ports` as **sequence-like** (SPICE call order)
- Implicit defaults ≡ explicit defaults (pydantic auto-fills both)

When the two IRs differ, the report lists **field-level diffs** like:

```
- measurements[1].direction: extracted='rising' gold='falling'
- pass_criteria[0].value: extracted=70.0 gold=60.0
```

That makes benchmark failures **diagnosable in seconds** — not just a
binary "wrong".

### Running example: 5-T differential-pair OTA

The foundation of the entire project. A textbook circuit + two simple
specs, walked end-to-end by hand through every stage of image 2. The
artifact `trace.md` is a 380+ line **engineering log** that records, per
stage:

- What the inputs were
- What was hand-written, and why
- Where the friction was
- What hack got us past it
- What that implies the schema needs

**That trace is what determined the IR schema's shape** — without it,
the schema would be guess-driven.

| File | Role |
|---|---|
| `trace.md` | end-to-end walkthrough; 27 punch-list items |
| `dut.cir` | DUT netlist (5 MOSFETs + level-1 models) |
| `testbench.cir` | full testbench (executable in ngspice) |
| `testbench.log` | actual ngspice output |

Measured results: **DC gain = 66.11 dB (spec > 60 dB → PASS), UGB = 32.07 MHz
(spec ≥ 10 MHz → PASS).**

---

## Design principles

> These thread through *every* part of the code, not decorative.

1. **Cross-provider first.** Parallel `extract_with_<provider>` functions,
   never a unified `Provider` interface. See
   `memory/prefer_cross_provider_portability.md`.

2. **Strict schema, fail fast.** `extra="forbid"`, closed enums, cross-
   field validators. Reject malformed input *at the earliest boundary*
   with precise error messages.

3. **Closed primitives over expression DSL.** v0 ships 2 measurement
   primitives — adding new ones is deliberate, driven by new running
   examples.

4. **No premature abstraction.** `extract_with_anthropic` and
   `extract_with_openai_compatible` are explicitly duplicated. No
   `Provider` base class. No LangChain. The Nth provider is when we
   consider abstraction — not the 2nd.

5. **Trace before code.** Every new running example starts with a manual
   walkthrough and a trace.md. New code must be backed by a trace-surfaced
   need.

6. **Tests are schema-acceptance tests.** Not testing code logic — testing
   that the schema can express each example and reject every typical LLM
   mistake.

---

## Roadmap

Following the original 7-step plan:

| Step | Description | Status |
|---|---|---|
| 1 | Pick one running example, walk it end-to-end → trace | ✅ done |
| 2 | Lock the IR schema + define semantic equivalence | ✅ done |
| 3 | Implement Stage-1 LLM extract + auto-evaluation | ✅ done |
| 4 | Grow to 10–20 seed cases, run a real Stage-1 benchmark | ⏳ next |
| 5 | Implement Stage-2 emit (ngspice) + executability metric | ⏳ |
| 6 | End-to-end run; compare IR-path vs direct generation | ⏳ |
| 7 | Cluster failure modes; decide the next iteration's focus | ⏳ |

---

## Testing & quality

| File | Tests | What it tests |
|---|---|---|
| `tests/test_ir_diff_pair_ota.py` | 9 | gold IR expressible in schema; JSON round-trip; per-gap validators |
| `tests/test_ir_equivalence.py` | 14 | semantic equivalence boundaries (order, metadata, sequence sensitivity, value diffs) |
| `tests/test_extract_live.py` | 2 | live LLM extraction vs gold IR; gated on API key env vars |

Lint: `ruff` configured in `pyproject.toml`; `uv run ruff check src/ tests/` is clean.

```bash
uv run pytest -v          # full suite (live tests skip if no key)
uv run ruff check         # lint
```

---

## FAQ

**Q: Why does v0 stop at spec → IR? Image 2 wants executable testbench end-to-end.**
A: The 27 trace items split across 4 distinct layers (IR schema / emitter
/ PDKContext / evaluator). Trying to do all of them at once means none of
them go deep. Step 2 nails the IR layer; Step 5 will build the emitter on
top of a stable foundation.

**Q: Why ngspice, not Spectre?**
A: ngspice is an open-source fallback and lets the project remain
hermetic for testing. v0 using ngspice doesn't mean ngspice-forever —
the `MeasurementPrimitive` abstraction is precisely what lets a future
emitter target Spectre, HSPICE, *or* ngspice.

**Q: I don't have an Anthropic key, only an OpenRouter / Xiaomi MiMo key. Can I use this?**
A: Yes. Use `extract_with_openai_compatible(...)` and pass your
`(api_key, base_url, model)` triple. The `OPENAI_COMPAT_*` env vars in
`tests/test_extract_live.py` are designed exactly for this.

**Q: Why not LangChain?**
A: LangChain's abstractions are too thick for a research project that
needs to compare LLMs at the structured-extraction level — prompt-caching
signals, error diagnostics, token usage are all obscured. Two 30-line
parallel functions are ~10× simpler and far more controllable.

**Q: Can I add a new measurement primitive?**
A: Yes but deliberately — the closed-primitive set is a core schema
invariant. The recipe:
1. Surface the new primitive via a fresh running example + trace
2. Add the value to the `MeasurementPrimitive` enum
3. Extend `Measurement._primitive_params` to validate its parameters
4. Document the primitive's semantics + use cases in `_SYSTEM_PROMPT`
5. Add tests asserting the validator behaviour

---

## Acknowledgements

- Project motivation comes from a WeChat conversation (`image1.png`)
- The pipeline sketch comes from the same thread (`image2.png`)
- The observation that "Claude's testbench ability is much stronger than
  mimo v2.5 pro" is from a conversation with **张托肯 (Zhang Tuoken)**,
  visible in `image1.png`.

---

<div align="center">

[⬆ Back to top](#spec2testbench) &nbsp;·&nbsp; [🇨🇳 中文版 ↑](#spec2testbench)

</div>
