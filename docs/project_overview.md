# spec2testbench — 项目目标 vs 当前代码库实现

> 本文档对照 `image1.png`（动机）和 `image2.png`（技术路线）两张图，
> 系统说明：(1) 你想做的是什么，(2) 目前代码库做到了哪些目标，(3) 还差什么。
>
> 日期：2026-05-14

---

## Part 1 — 图 1 解释（项目动机）

图 1 是四条聊天消息，连起来读：

> 1. 「昨天我们思考了一下 觉得 spec2testbench 是非常重要的**缓解**」
> 2. 「**环节**」
> 3. 「现在 autoresearch 很牛 但前提是我们有一个**好的 testbench 能够 guide agent evolving**」
> 4. 「然后也和张托肯交流了一下 他发现 **claude 的 testbench 的能力就比 mimo v2.5 pro 强很多**」

### 核心论点（图 1 想说的）

| # | 论点 | 含义 |
|---|---|---|
| 1 | spec2testbench 是个**重要的"缓解环节"** | 这个项目不是炫技，是补一个真实的瓶颈 |
| 2 | "autoresearch" 已经很强大 | LLM agent 自驱设计电路这条路是可行的 |
| 3 | 但前提是有"**好的 testbench 能 guide agent evolving**" | agent 要进化必须有反馈环；testbench 就是反馈环的载体 |
| 4 | **Claude 在 testbench 这件事上比同类 LLM（mimo v2.5 pro）强很多** | 这就是为什么要专门基于 Claude 来建这个项目 |

### 用一句话浓缩图 1

> **"autoresearch 这条路成不成，关键看 agent 有没有好 testbench 可以反馈迭代。
> 当前生成 testbench 的瓶颈在人——所以做 spec2testbench 把这条链路自动化。
> Claude 是当下做这件事最强的引擎。"**

### 这给后续工作什么约束

- **质量优先于规模**：testbench 错一处，agent 就走错方向；schema strictness、deterministic output、hermetic test 是核心
- **要 close-loop**：不能只生成 testbench 就完事，必须能跑通、能给出 pass/fail 反馈
- **要基于 Claude 设计**：prompt、IR schema、错误反馈格式都应该 Claude-friendly

---

## Part 2 — 图 2 解释（技术路线 / pipeline）

图 2 描述了"研究 LLM Agent 能不能写好 testbench"这个问题应该如何展开。结构上是 **2 个连续阶段** + **3 条技术要求**。

### 整体目标

> **重点是研究 LLM Agent 能不能写好 Testbench**

### 阶段 1 — NL spec → 结构化 test plan（spec extraction）

> 应该有一个过程：
> Natural language spec → (spec extraction) → 结构化的 test plan

图里给的 test plan 示例 JSON：

```json
{
  "spec_item":   "DC_gain > 60dB",
  "analysis_item": "AC",
  "stimulus":    {"type": "AC", "magnitude": 1, "port": "inp-inn (differential)"},
  "loading":     {"CL": "1pF", "connection": "out to gnd"},
  "measurement": {"metric": "max(vdb(out))", "condition": "f=1Hz to 10Hz"},
  "corners":     ["tt_27", "ss_85", "ff_-40"],
  "pass_criterion": "value > 60"
}
```

**这个 JSON 就是后来我们叫它 "IR" / "TestPlan" 的东西。** 它是人话和可执行 netlist 之间的"中间表示"。

### 阶段 2 — 结构化 test plan → 可执行 netlist

> 然后再到一个 executable 的 spice netlist
>
> 输入 **NL spec + DUT netlist + PDK context**，输出可执行的 **commercial simulator testbench**（首选 **Spectre**，次选 **HSPICE**），并且建议以一个结构内可以表示从 pipeline 更可视化，evaluation 更细颗粒，**ngspice 作为 open-source fallback**。

把这段拆开看：

| 子要求 | 含义 |
|---|---|
| 输入有三个：NL spec、DUT netlist、PDK context | IR 只是 NL 的提取；要真生成 testbench 还要 DUT 接口 + 工艺/PDK 上下文 |
| 输出：**可执行的** commercial simulator testbench | 不是写出来"看着像 SPICE"，而是 ngspice/Spectre 真能跑 |
| 优先级：**Spectre > HSPICE > ngspice** | Spectre/HSPICE 是工业标准；ngspice 是 open-source fallback（可在没商用 license 时跑） |
| pipeline 要"结构内可表示" | 不要黑盒——每个阶段的输入输出可看、可 dump |
| evaluation 要"更细颗粒" | 不只输出 pass/fail，要能分阶段看：extract 对不对、emit 对不对、sim 对不对、verdict 对不对 |

### 用一张图总结图 2

```
┌──────────────┐
│   NL spec    │   人类工程师写的需求段
└──────┬───────┘
       │  (Stage 1: spec extraction)        ← 由 LLM 完成
       ▼
┌──────────────┐
│   IR /       │   结构化 JSON / pydantic 对象
│  TestPlan    │   解决了"人话 → 机器可读"
└──────┬───────┘
       │
       │  + DUT netlist (上游给)
       │  + PDK context (PDK 提供)
       │
       │  (Stage 2: testbench emission)     ← 由 emitter 完成
       ▼
┌──────────────────┐
│ Executable spice │   .cir / .spice 文件
│   testbench      │   Spectre > HSPICE > ngspice
└──────┬───────────┘
       │  (Stage 3: simulation)
       ▼
┌──────────────────┐
│  Sim results +   │
│   pass/fail      │   evaluation 细颗粒：分阶段都能 inspect
└──────────────────┘
```

---

## Part 3 — 当前代码库做了什么（vs 图 2 目标）

到 2026-05-14 为止，代码库经过两个大 step。

### Step 1：用一个具体例子手工跑通整条链路

**目标：** 不是要写代码，是要**用一个真实电路把图 2 那条链路从头到尾手工走一遍**，把所有"图上说得通、实际做时会卡"的地方记下来。这是后面所有工作的需求文档。

**选的例子：** 5-管差分对 OTA（教科书电路），两条 spec：DC 增益 > 60 dB，UGB ≥ 10 MHz @ 1 pF 负载。

**产出物（5 个文件）：**

| 文件 | 角色 | 对应图 2 哪个位置 |
|---|---|---|
| `examples/01_diff_pair_ota/trace.md` | **手工 walkthrough 文档**（380+ 行） | 把整条 pipeline 当作 trace 记录下来 |
| 同上 §1 (NL spec) | 手写的自然语言需求 | 图 2 最上面那个"Natural language spec" |
| 同上 §2 (Gold IR JSON) | 手写的结构化 test plan | 图 2 中间那个"结构化 test plan" |
| `examples/01_diff_pair_ota/dut.cir` | 被测电路 netlist | 图 2 阶段 2 的"DUT netlist"输入 |
| `examples/01_diff_pair_ota/testbench.cir` | 手写的完整 testbench | 图 2 最右边的"executable spice netlist" |
| `examples/01_diff_pair_ota/testbench.log` | ngspice 实际跑出的结果 | 图 2 最右边的"sim results"（ngspice fallback 跑通了） |

**关键产出：27 条 punch list**

trace 真正的价值不是 testbench 本身，是手工跑的过程暴露了**27 个 NL 没说但 pipeline 必须处理的问题**。这 27 条分成 3 类：

| 类 | 数量 | 含义 |
|---|---|---|
| Schema gaps (A–K) | 11 条 | IR schema 当前不够用的地方 |
| Emitter knowledge debts (K1–K14) | 14 条 | IR → netlist 翻译时 emitter 要靠外部知识补的地方 |
| Evaluator knowledge debts (E1–E2) | 2 条 | 评估阶段必须做的单位换算 |

详细见 `trace.md` 末尾的 Summary 段。

---

### Step 2：把 IR schema 固化下来（解决 11 条 schema gaps）

**目标：** 把"图 2 中间那个 JSON 长什么样"用 pydantic 写死。一份 schema，能：
- 完整表达 Step 1 那个 example
- 在 NL 模糊的地方强制结构化
- 在错误数据进来时给精确报错（LLM-friendly）
- 顺带定义"两份 IR 何时算同一份测试"（semantic equivalence）

**产出物：**

```
src/spec2testbench/
└── ir.py                 (~440 行：11 model + 8 enum + 5 validator + semantic_equivalent)

tests/
├── test_ir_diff_pair_ota.py    (9 tests：把 Step 1 的 gold IR 用新 schema 表达 + round-trip)
└── test_ir_equivalence.py      (14 tests：semantic equivalence 各种情形)

pyproject.toml + uv.lock        (uv 项目骨架，pydantic 2.13 / pytest 9 / ruff 0.15)
```

**结果：23/23 tests passed，ruff lint 全清。**

11 条 schema gap 中：
- **10 条已经在 IR 里解决**（A、B、C、D、E、F、G、H、J、K）
- **1 条（I：VDD/ibias/Vcm）显式推到未来的 PDKContext** —— 因为这些不属于"测什么"而属于"电路上下文"

---

## Part 4 — 对照表：图 2 的每个要求 vs 当前实现状态

这是最重要的一张表。

| 图 2 要求 | 当前状态 | 在哪 | 评论 |
|---|---|---|---|
| **Stage 1: NL spec → IR**（自动化） | ❌ 未实现 | — | NL → IR 目前是**手写**的（trace.md §1 → §2）；LLM extractor 没写 |
| **Stage 1 的 IR schema** | ✅ 已实现 | `src/spec2testbench/ir.py` | pydantic v2，11 个 gap 中 10 个就地解决，1 个显式推迟 |
| **Stage 2: IR + DUT + PDK → testbench**（自动化） | ❌ 未实现 | — | IR → netlist 目前是**手写**的（trace.md §2/3 → §4 的 testbench.cir）；emitter 没写 |
| **DUT netlist 输入路径** | 🟡 部分 | IR 里有 `dut.netlist_path` 字段 | 字段就位但没有"读取并 include"的实现 |
| **PDK context 输入** | ❌ 未实现 | — | Step 2 显式推迟；K3/K4/K5/K8（11 条 emitter 债中 4 条）依赖它 |
| **可执行 netlist（首选 Spectre）** | ❌ 未实现 | — | Spectre emitter 没写 |
| **可执行 netlist（次选 HSPICE）** | ❌ 未实现 | — | HSPICE emitter 没写 |
| **可执行 netlist（ngspice fallback）** | 🟡 一个例子手工跑通 | `examples/01_diff_pair_ota/testbench.cir` + `.log` | 是手写不是 emit；但是 ngspice 上确实跑通了，DC gain=66.1dB / UGB=32MHz |
| **Stage 3: 实际跑仿真** | 🟡 一个例子手工跑过 | `testbench.log` | 跑通了但没有自动化的 sim runner |
| **Stage 4: 评估（fine-grained）** | 🟡 思想已沉淀，未实现 | trace.md §6/§7 + 27 条 punch list 的分类 | 知道要分阶段评（extract / emit / sim / verdict）；具体 evaluator 代码没写 |
| **pipeline 结构化 / 可视化** | 🟡 schema 部分 | `ir.py` | 数据形态结构化了；可视化层（让用户看 pipeline）没做 |

**图例：**
- ✅ 已实现（且有自动化测试覆盖）
- 🟡 部分实现 / 一个例子手工做过，但没自动化
- ❌ 未实现

---

## Part 5 — 已实现部分的具体细节（你的代码现在能干什么）

### 当前代码能做的事

1. **构造一个完整的 TestPlan IR**（用 Python 代码）

   ```python
   from spec2testbench.ir import TestPlan, Meta, Dut, ...
   plan = TestPlan(
       meta=Meta(id="my_test", nl_spec="..."),
       dut=Dut(name="ota", subckt_ports=[...]),
       analyses=[AcAnalysis(id="ac1", f_start=1.0, f_stop=1e9)],
       measurements=[...],
       pass_criteria=[...],
       corners=[...],
   )
   ```

2. **JSON 序列化 / 反序列化（且会验证）**

   ```python
   raw = plan.model_dump_json()       # → JSON 字符串
   plan2 = TestPlan.model_validate_json(raw)  # 字符串 → 实例（不合法直接 raise）
   ```

3. **判断两个 IR 是否描述同一个测试**

   ```python
   from spec2testbench.ir import semantic_equivalent
   semantic_equivalent(plan_a, plan_b)  # → True/False
   ```

4. **挡住所有典型的非法 IR**（详见 23 个 pytest）
   - 漏字段、字段错类型 → pydantic 拒绝
   - measurement 引用不存在的 analysis → validator 拒绝
   - crossing 原语没带方向 → validator 拒绝（Gap G）
   - 端口角色重复 → validator 拒绝
   - stimulus 引用 DUT 不存在的角色 → validator 拒绝

### 当前代码不能做的事

1. ❌ 不能从 NL spec 文字自动提取 IR（这是 Step 3 的目标）
2. ❌ 不能从 IR 生成 netlist（Step 5）
3. ❌ 不能自动跑仿真器
4. ❌ 不能自动给 pass/fail（Step 4 的目标）
5. ❌ 没有 Spectre/HSPICE 支持
6. ❌ 没有 PDKContext 模型

---

## Part 6 — 你站在 roadmap 上的哪里

回顾我们最初定的 7 步路线图：

| Step | 描述 | 状态 |
|---|---|---|
| 1 | 选定 1 个 running example，手工跑通端到端 → 出 trace | **✅ 已完成** |
| 2 | 基于 trace 固化 IR schema 和评估准则（语义等价定义） | **✅ 已完成** |
| 3 | 实装 Stage 1 (LLM extract) + 自动评估 | ⏳ 下一步 |
| 4 | 扩 10–20 个种子 case，跑 Stage 1 benchmark | ⏳ 之后 |
| 5 | 实装 Stage 2 emit (ngspice) + executability 指标 | ⏳ 之后 |
| 6 | 端到端跑，对比 IR-路径 vs 直接生成路径 | ⏳ 之后 |
| 7 | 根据失败模式聚类，决定下一轮迭代重点 | ⏳ 之后 |

**所以你现在在 Step 2 和 Step 3 之间的衔接点。**

**已经具备的"基础设施"** ：
- 一份手工 trace，知道整条 pipeline 的每个失败模式（27 条）
- 一份**真实可用**的 IR schema（不是空架子，能表达完整 example、能 round-trip、能验证）
- 一份语义等价定义，未来 benchmark 时用来判断"LLM 抽出来的 IR 跟标准答案是不是同一个测试"

**下一步（Step 3）要做什么** ：
- 写一个 `extract(nl_spec: str) -> TestPlan` 函数，调用 Claude API
- 系统提示词包含：IR schema 描述、闭集原语清单、若干 few-shot 例子
- 用语义等价（已经写好）做自动评估：抽出来的 IR vs 我们手写的 gold IR，equivalent 就算对
- 在 `examples/01_diff_pair_ota` 这个唯一例子上先跑通

Step 3 完成后，第一次有"机器自动做事 + 机器自动判断对错"的闭环。

---

## Part 7 — 图 2 vs 当前代码 一张图

```
图 2 期望：
═══════════
NL spec ──[Stage 1 spec extract]──► IR ──[Stage 2 emit]──► testbench ──► sim ──► verdict
                                            ▲          ▲
                                    DUT netlist     PDK context
                                                                        Spectre / HSPICE / ngspice

当前代码实现：
═══════════
NL spec ──[手工写]──► IR ──[手工写]──► testbench ──[手 ngspice]──► [手 grep+计算]──► verdict
            │              │                                                    │
            │              │                                                    │
            └──── 仅这一段有 schema + 测试覆盖 ──────┘                       │
                  (src/spec2testbench/ir.py)                                  │
                                                                              │
            +27 条 punch list 已系统记录在 examples/01_diff_pair_ota/trace.md
            +1 个完整 running example 跑通（ngspice，gain=66.1dB / UGB=32MHz）
```

---

## Part 8 — 文件清单（给你一个能 ls 进去看的导览）

```
spec2testbench/
├── docs/
│   └── project_overview.md          ← 你正在看的这个文件
│
├── examples/
│   └── 01_diff_pair_ota/
│       ├── trace.md                 ← 端到端 walkthrough + 27 条 punch list
│       ├── dut.cir                  ← 5-tran OTA 电路 netlist
│       ├── testbench.cir            ← 手写完整 testbench（ngspice 能跑）
│       └── testbench.log            ← 实际仿真输出
│
├── src/spec2testbench/
│   ├── __init__.py
│   └── ir.py                        ← IR schema 全部定义在这里（pydantic + validators + 等价）
│
├── tests/
│   ├── test_ir_diff_pair_ota.py     ← Step 2.3：gold IR round-trip 测试
│   └── test_ir_equivalence.py       ← Step 2.4：语义等价测试
│
├── image1.png                       ← 项目动机
├── image2.png                       ← 技术路线
├── pyproject.toml                   ← uv 项目配置
└── uv.lock                          ← 依赖锁文件
```

---

## Part 9 — 一句话总结

> **你想做的（图 1 + 图 2）**：让 LLM 自动把人话需求变成可跑的 testbench，让 autoresearch agent 有反馈环——优先 Spectre，HSPICE 次选，ngspice 兜底。
>
> **当前代码做到的**：(1) 用一个差分对 OTA 例子，把整条链路**手工**走通了一遍，并且把 27 个"图上没说但实际必须解决"的问题系统记录下来；(2) 把图 2 中间那个 JSON（IR）的形态彻底想清楚并用 pydantic 锁住，附带 23 个自动化测试。
>
> **还没做的**：自动化的 spec extractor、自动化的 emitter、自动化的 evaluator、Spectre/HSPICE 支持、PDKContext 设计、benchmark 数据集——这些是 Step 3 及以后的工作。
>
> **比喻**：图 2 是"想造的工厂"；我们目前**手工**用车间做了一个完整的样品（Step 1），并把其中一台核心设备的图纸定型（Step 2 的 IR schema）。**还没有 production line**——这就是 Step 3 开始要做的事。
