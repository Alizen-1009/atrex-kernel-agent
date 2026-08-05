# RFC：问题驱动的 Harness 演进

[English](problem-driven-harness-evolution.md)

- **状态：** Proposed
- **日期：** 2026-08-04
- **范围：** Atrex Kernel Agent harness 演进
- **近期承诺：** characterization 当前行为，抽取已经存在的 multi-runtime seam，并为普通 iteration 增加 local-only observability

## 摘要

Atrex 需要更清晰的 harness，但更大的 harness 不会自动带来更好的优化效果。新增 controller、journal、receipt、policy 和 state machine 可能提升可靠性，也可能增加 GPU 成本、token 成本、执行延迟、迁移风险和新的故障模式。它们不会让 Optimization Agent 更擅长解释 profile 或生成更快的 kernel。

本 RFC 采用问题驱动策略：

> 先观察真实故障或重复变更成本，复现并测量它，再引入能够拥有该问题的最小机制；当问题被解决后停止继续扩张。

目前唯一已经被多个真实实现证明合理的架构抽取是 `AgentRuntime`：Atrex 已支持 Claude、Qoder 和 Codex，但它们的 command、environment、token、authentication 与 skill-hydration 行为混杂在 `orchestrator/optimize.py` 中。

这里指仓库级 multi-runtime support，而不是 multi-Agent orchestration。每个 campaign 在创建 workspace 时选择且只选择一个 runtime。该 campaign 的 setup、framework baseline、所有 optimization iteration、repair/salvage、conversion 和 finalization 始终使用同一个已记录 runtime。Runtime failure 绝不会触发自动 backend 切换；更换 backend 必须创建新的 campaign/workspace。

第二个近期问题也已经明确：maintainer 目前无法可靠回答一个普通 optimization iteration 的时间花在哪里、读取了哪些 source、哪些 GPU operation 占主要耗时，以及 backend 报告的 token usage 如何分布在 explicit workflow phase 中。v1 observability 解决该问题，但不改变 candidate acceptance、Git、memory、现有 token-budget accounting 或 optimization loop。

更广泛的机制全部是条件性的。Controller-owned acceptance、candidate write scope、repair turn、durable step journal、two-phase Git writeback、通用 `step()` interface，以及 bucket/layer 迁移，都必须有明确 activation condition，而不是预先确定的 PR 路线。

## 决策

1. 将当前 Agent-driven optimization loop 作为行为基线。
2. 在改变 authority model 之前先收集证据。
3. 行为保持地抽取 `AgentRuntime`，因为三个真实 backend 已经证明该 seam 成立。
4. 每个 campaign workspace 在完整生命周期内绑定一个 runtime；iteration 或 recovery turn 之间绝不自动切换 backend。
5. 为普通 optimization iteration 增加 local-only、read-only observability，不改变 Git 或 memory authority。
6. 只有 acceptance uncertainty 能证明需要时，才增加 shadow decision observation。
7. 只有具体问题和 acceptance metric 能证明价值时，才引入更深的 harness mechanism。
8. 不预先承诺完整 Campaign Controller、event ledger、StepJournal 或 bucket/layer 重写。
9. 保留 north-star responsibility model，避免局部修复重新制造跨 host 或跨 path 耦合。

## 为什么替换之前的计划

之前的草案规定了完整 Campaign Controller 和 PR 0–12 迁移路线。该设计回答了“Harness 可以怎样组织”，却没有先回答：

- 当前 Agent 多久会错误接受一次 invalid candidate？
- Interrupted session 多久会无法从 Git 和 memory 恢复？
- Memory、Git 与 stall state 多久会发生实质性不一致？
- Independent validation 会增加多少重复 GPU 工作？
- Clean repair session 是否真的优于继续原始 session？
- Bucket callback 或 layer 独立循环是否已经造成真实 defect？
- 当前主要瓶颈究竟是 harness reliability，还是 Agent optimization quality？

没有这些证据，完整实现该设计属于推测性建设。本 RFC 保留有用的职责语言，但把 roadmap 从 architecture-led 改成 evidence-gated。

## Agent 与 Harness

### Optimization Agent

Optimization Agent 是通过 Claude、Qoder 或 Codex 运行的 model-driven actor。它目前负责：

- 读取历史 memory 和 profile evidence；
- 查询 gpu-wiki 与 reference source；
- 选择 optimization direction；
- 写 plan 与 candidate code；
- 调用 sandbox tool 完成 profile、correctness 和 benchmark；
- 按当前 prompt contract 记录结果与 Git state。

提升该 Agent，意味着提升 profile interpretation、search、planning、framework knowledge、candidate quality、debugging，以及从历史 attempt 学习的能力。

### Atrex Harness

Harness 包围并驱动 Agent，包括：

- CLI 与 campaign orchestration；
- host process launch 与 guard；
- sandbox/gateway transport；
- evaluator integration；
- workspace 和 Git convention；
- memory artifact；
- retry、budget、bucketing、aggregation 与 layer scheduling。

Harness 变更可以提升 reliability、observability、recovery、consistency 与 maintainability，但不会直接提升 Agent 的 GPU optimization intelligence。

## 原则

### 1. Evidence before mechanism

文件很大、架构看起来更漂亮或未来可能需要 dashboard，都不足以证明应该增加新的 state owner。每个 mechanism 都需要具体问题、证据和可测量 exit condition。

### 2. 一个问题、一个 owner、一次变更

不要在同一个 refactor 中同时解决 host variation、candidate acceptance、crash recovery、status projection 和 bucket scheduling。把每个观察到的问题交给最近的 owning module，并提交一个完整一致的变更。

### 3. Shadow before authority

当问题涉及决策，例如 candidate 是否应该被接受，先计算 shadow decision，不修改 Git、memory、stall 或 campaign flow。先比较 decision，再考虑转移 authority。

### 4. 优先使用派生事实

增加新的持久 state file 前，优先使用现有 Git、`memory/vN.json`、profile artifact 和 manifest。只有某项事实无法安全重建，并且它的丢失已经造成真实问题时，才增加 durable state。

### 5. 保持 Agent feedback loop

在没有测量 token、GPU time、wall time 和 accepted improvement 影响之前，不增加额外 profile、evaluator run、clean repair session 或 structured output requirement。

### 6. 问题解决后停止

成功完成一个窄修复，不意味着应该继续建设 north-star architecture 的其余部分。每个 stage 都必须有明确 stop gate。

### 7. 保持可回滚

行为保持的 extraction 与行为变更的 enforcement 必须分开。每个新 enforcement mechanism 都要有 rollback path。

## 当前证据

### 已确认：三个真实 Agent runtime 耦合在同一模块

Atrex 支持 Claude、Qoder 和 Codex。Host-specific 行为当前分布在：

- `_session_command`；
- `_session_env`；
- `_tokens_from_stream`；
- `_agent_auth_hint`；
- `_agent_runtime_directive`；
- `_baseline_driver_directive`；
- `_plan_generator_directive`；
- `link_runtime` 中的 host-specific 部分；
- `run_session` 使用的 process 与 dependency guard。

测试直接 patch 这些私有函数。这是一个真实 seam，因为已经存在多个 implementation，并且它们因不同原因变化。

### 已确认：runtime token accounting 是 process-local

每次 process 启动时，`Campaign.tokens_spent` 在内存中初始化。Campaign 重启后可能无法保留之前精确的 token spend。这是一个确定的代码属性，但只有当真实 campaign 要求 token budget 跨重启连续时，它才是用户影响问题。如果该问题被确认，应只针对它做窄修复。

### 已确认：存在 control-flow duplication

普通 `Campaign.run()`、framework baseline、conversion、workload bucket 和 layer scheduling 包含重叠的 session/validation/writeback logic。这是 maintainability signal，但还不是“通用 Controller 一定正确”的证据。统一之前，应先找到 shared seam 能消除的实际 behavior drift 或重复变更成本。

### 已确认：普通 iteration 缺少 decision-ready telemetry

当前 session accounting 能提供 aggregate exit、timeout、token-budget 和 output-tail fact，profile 与 evaluator artifact 则分散存放。系统没有一条关联完整的 iteration timeline，无法回答 phase duration、source-read metadata、sandbox-operation duration 或 unattributed time。这是明确的运维和 Agent-improvement 需求，因此无需等待 acceptance incident，就可以建设一个有界 observability slice。

### 尚未建立证据

仓库目前还没有量化证明以下问题：

- false acceptance rate；
- Agent claim 与 mechanical observation 的 decision drift；
- resume 后的 duplicate side effect；
- 无法恢复的 phase-level interruption；
- 有实质影响的 memory/Git divergence；
- bucket callback race frequency；
- layer 独立 loop 导致的 scheduling defect；
- Controller-owned mandatory profiling 的收益；
- clean repair turn 的价值；
- 完整 Campaign Controller API 的必要性。

这些仍然只是 hypothesis。

## 当前 loop 是行为基线

在证据支持更窄的变更前，普通 iteration 保持行为不变：

```text
Agent 读取当前 workspace 与 memory
→ profile 或复用 profile
→ 研究并规划一个 lever
→ 编辑并调试 candidate
→ validate 与 benchmark
→ 记录 memory
→ commit win 或记录 rejection
→ outer campaign 决定是否继续
```

Extraction 或 enforcement 改变它之前，必须先完成 baseline characterization。

## Phase 0：建立 current-state baseline

### 目标

判断哪些 harness 问题真实发生，以及哪些成本主导 campaign outcome。

### 输入

尽可能使用现有 public-safe artifact：

- Git history 与 kernel blob；
- `memory/vN.json`；
- stall state；
- orchestrator 已保留的 session result summary；
- compact profile/evaluator output；
- bucket 与 aggregate manifest；
- 现有 test 和 issue history。

不得把 raw private transcript、credential 或无界 gateway log 放入 repository fixture。

### Failure taxonomy

对 observation 分类，禁止全部折叠成“失败”：

| Class | 含义 |
| --- | --- |
| `runtime_failure` | Claude/Qoder/Codex process 未完成 contract |
| `candidate_validation_failure` | Candidate compile、correctness 或 admissibility check 失败 |
| `performance_rejection` | Candidate 合法，但没有显著提升 incumbent |
| `infrastructure_failure` | Gateway/provider 无法产生可信 observation |
| `state_recovery_failure` | Git/memory/worktree state 无法安全 reconciliation |
| `blocked` | 继续前需要外部动作 |
| `accepted` | Candidate 通过当前 contract 并成为 incumbent |

### Baseline metric

至少测量：

- Agent-declared win 与可机械检查 outcome 的 disagreement；
- accepted candidate 后续被发现 incorrect 或 non-compliant 的次数；
- session interruption frequency；
- interruption 后自动恢复与人工恢复比例；
- incomplete round 后重复实验的频率；
- 会影响下一步动作的 memory/Git/stall inconsistency；
- 每个 accepted improvement 的 token 消耗；
- 可观测时，每个 accepted improvement 的 GPU minute；
- 到第一次 accepted improvement 的 wall time；
- campaign completion rate；
- backend-specific failure distribution。

如果当前 compact artifact 无法重建某项 metric，就标记 unknown。除非缺失 metric 直接影响一个决策，否则不要扩张成宽泛 instrumentation 项目。

### 产物

一份紧凑、public-safe 的 diagnosis 应回答：

1. 哪些 harness failure 真实且重复发生？
2. 哪些很少发生但严重？
3. 哪些只是没有观察影响的架构担忧？
4. 哪类 failure 消耗最多 human、token、GPU 或 wall-clock 成本？
5. 下一项可以评估的最小变更是什么？

### Stop gate

如果没有发现实质性 harness 问题，则在已经合理的 `AgentRuntime` extraction 后停止。把精力转向 Optimization Agent quality，而不是制造 Controller 项目。

## Phase 1：行为保持的 AgentRuntime extraction

### 为什么现在已经合理

已经存在三个真实 backend。一个 adapter 只是 hypothetical seam；三个 adapter 证明 host variation 真实存在。

### 最小 interface

```python
class AgentRuntime(Protocol):
    @property
    def id(self) -> str: ...

    def run(self, request: AgentRunRequest) -> AgentRunResult: ...
```

第一版 request 应接近现有行为。不能在 caller 尚未需要时，提前把它做成完整 future semantic-turn protocol。

```python
@dataclass(frozen=True)
class AgentRunRequest:
    workspace: Path
    prompt: str
    timeout_s: int
    reasoning_effort: str
    sandbox_environment: Mapping[str, str]
```

```python
@dataclass(frozen=True)
class AgentRunResult:
    runtime_id: str
    exit_status: int
    timed_out: bool
    tokens: int
    session_id: str | None
    stdout_tail: str
    stderr_tail: str
    failure_category: str | None
```

### Adapter ownership

Concrete adapter 只负责今天真实存在的 host variation：

- command construction；
- provider setting；
- authentication environment 差异；
- token/output parsing；
- session identity；
- host-specific skill/plugin hydration；
- host-specific diagnostic classification。

Shared process supervision 和 dependency guard 可以放在 runtime internal module。Campaign logic 只接收 `AgentRuntime`，不根据 host name 分支。

### Implementation

- `ClaudeRuntime`；
- `QoderRuntime`；
- `CodexRuntime`。

不增加 Cursor/OpenCode placeholder、plugin discovery、runtime manifest 或 third-party adapter installation。

### Campaign-level runtime binding

仓库支持多个 runtime，不代表一个 campaign 可以混用它们。Composition root 在 campaign 创建时选择一个 adapter，并把该 instance 注入所有 campaign path。

将 selected runtime additively 持久化到现有 ignored workspace policy state，例如：

```json
{
  "mode": "production",
  "framework": "Triton",
  "agent_runtime": "codex"
}
```

不变量：

- setup、framework baseline、ordinary iteration、conversion、repair、salvage、bucket child 和 layer-boundary work 都继承已记录 runtime；
- resume 时 requested runtime 与 recorded runtime 不一致则 fail closed；
- runtime failure 只会被分类、重试或上报，绝不通过切换 backend 处理；
- 更换 runtime 必须创建全新的 campaign/workspace；
- backend 对比使用具有相同初始输入和预算的独立 campaign。

缺少 `agent_runtime` 的旧 workspace 标记为 `legacy_unbound`。升级后的第一次运行会在启动 session 前记录显式 requested runtime，并报告该 adoption。完成 adoption 后，binding 不可变。

### Compatibility requirement

精确保留：

- CLI flag；
- command 与 setting precedence；
- environment behavior；
- dependency/process guard；
- token accounting 语义；
- fresh-session behavior；
- skill hydration；
- one-runtime-per-campaign binding，包括 legacy workspace 的 backward-compatible adoption；
- 当前 prompt；
- setup、framework baseline、ordinary iteration、conversion、bucket 与 layer 行为。

### 测试

先 characterization 当前 command、environment、token parsing、failure、hydration 和 process termination。随后把测试迁移到 `AgentRuntime` interface，并删除被替代的 private-function test。

### Success criteria

- `Campaign` 不再为 session execution 根据 Claude/Qoder/Codex 分支；
- 三个 adapter 通过共享 contract suite；
- 同一个 campaign 的所有 path 使用其 recorded runtime，mismatched resume 在启动 Agent session 前失败；
- 现有 CLI 与 campaign test 保持通过；
- 除测试噪声外，不预期 token/GPU/wall-time 变化；
- 不增加 runtime dependency 或 user-facing state。

### Stop gate

Extraction 完成后，只继续到已经批准的 ordinary-iteration observability slice。不得自动继续到 Controller、journal、acceptance enforcement 或更广泛 prompt redesign。

## Phase 2：普通 iteration observability

### 范围

v1 只覆盖普通 optimization version `vN`，不覆盖 setup baseline、framework baseline、conversion、decomposition、recombination 或 aggregate validation。只有 ordinary-iteration trace 证明有价值后，才考虑扩展其他 path。它还以 best-effort 方式，把 backend 报告的 token usage 归因到普通 iteration 的 explicit phase；缺少 message-level usage 的 backend 降级为 `unavailable`，且不改变 Agent session。

Observability 是 read-only 的。它不决定 candidate acceptance、repair state、Git commit、memory content、stall 或下一步 campaign action。

### v1 必须回答的问题

针对一个普通 iteration：

1. 总 wall time 是多少？
2. 每个 Agent attempt 运行了多久？
3. Profile、research、planning、implementation、correctness 和 benchmark phase 各花了多久？
4. 这些 phase 的完整 explicit interval 内报告了多少 input、output、cache-read、cache-write 和 total token？
5. 读取了哪些 gpu-wiki、reference-project、workspace 或 public-web source？
6. Source-read tool 和 sandbox operation 各花了多久？
7. Attempt 中修改了哪些文件？
8. 观察到什么 runtime、memory 与 Git outcome？
9. Trace 中哪些部分是 exact、explicit、inferred、partial、unavailable 或 unattributed？

### Local-only storage

详细 trace 与 summary 保存在 ignored local state：

```text
.atrex/telemetry/
└── v12/
    ├── attempt-<session-id>.jsonl
    ├── attempt-<session-id>.summary.json
    ├── iteration.summary.json
    └── iteration.brief.md
```

v1 不把 telemetry 写入 `memory/vN.json`，不增加 metadata commit，也不修改 candidate worktree。只有未来出现真实 consumer 时，才通过单独提案将 compact summary 提升为 durable memory。

### Correlation 与 event schema

每条 event 携带有界 correlation field：

```json
{
  "schema_version": "atrex_iteration_event_v1",
  "campaign_id": "attention-h20-triton",
  "iteration_id": "v12",
  "attempt_id": "<session-id>",
  "event": "phase_started",
  "phase": "research",
  "timestamp": "2026-08-04T12:00:00Z",
  "monotonic_ns": 123456789,
  "source": "agent_runtime",
  "measurement": "explicit"
}
```

代表性 event：

- `iteration_started` / `iteration_completed`；
- `agent_session_started` / `agent_session_completed`；
- `phase_started` / `phase_completed`；
- normalized `agent_usage_delta`、`agent_terminal_usage` 和成功执行后的 `agent_phase_marker` receipt event；
- `source_read_started` / `source_read_completed`；
- `sandbox_operation_started` / `sandbox_operation_completed`；
- `file_changed`；
- `runtime_failure_observed`；
- `outcome_observed`。

Duration 使用 monotonic time；wall-clock UTC 只用于 correlation 和可读性。

### Phase attribution

使用混合模型：

1. **Exact harness boundary：** iteration、Agent process 和 sandbox operation start/end。
2. **Explicit Agent marker：** ordinary iteration prompt 要求 Agent 通过一个小型 local tracing helper 标记 `research`、`planning` 和 `implementation` phase boundary。
3. **Inferred fallback：** backend tool event 与 command 可以推断缺失 phase，例如 wiki read 归入 research、plan write 归入 planning、kernel edit 归入 implementation、evaluator command 归入 validation。
4. **Unattributed：** 无法在不猜测情况下归类的时间保留为 unattributed。

每个 phase value 都带有以下 measurement：

```text
exact | explicit | inferred | partial | unavailable
```

Top-level phase span 不得重复计数。Nested tool duration 与 phase wall time 分开报告。

### Phase token attribution

Token attribution 使用比时间归因更严格的模型：

- 固定 phase 为 `profile`、`research`、`planning`、`implementation`、`correctness` 和 `benchmark`；
- phase interval 可以重复，但不能重叠；
- 只有完整匹配、非重叠的 explicit marker pair 才允许归因；
- marker helper 只有在 trace 写入成功后才输出 machine-readable receipt；
- backend adapter 将 message usage 归一化为 `usage_delta`，将 session 最终 usage 归一化为 `terminal_usage`；
- 缺失、非法、嵌套或未闭合的 interval 不根据 tool behavior 推断；
- 无法归因的 token 保留在显式 `unattributed` bucket；
- input token 归属于模型实际处理它的 phase，即使 context 来自更早的 phase；
- input、output、cache-read、cache-write 和 total counter 分开保留；backend 缺失字段为 `null`，不是零；
- usage delta 超过 terminal usage 时保留 `inconsistent` 冲突，不按比例缩放 phase value；
- backend 只有 terminal usage 时，phase value 为 `unavailable`、coverage 为零，全部 total 进入 unattributed。

Phase attribution 先按 attempt 计算，再在 iteration 层聚合。失败和超时 attempt 仍保留在 aggregate 中。该 telemetry 只用于 diagnostic，不替换现有 campaign token budget 使用的 `result.tokens` compatibility total。

### Sandbox operation timing

在不增加 GPU work 的前提下 instrument 本地 `tools/sandbox.py` boundary。记录：

- requested 和 resolved kind（`profile`、`run`、`dev`）；
- sanitized command category，而不是不受限制的 raw command；
- operation id 与 status；
- 可获取时的 queue/wait/execute/total duration；
- profile、correctness、benchmark classification；
- retry/fallback information；
- bounded artifact reference。

Sandbox call 可以嵌套在 phase 内，其 duration 不会再次加到 total iteration wall time。

### Metadata-only source trace

当 backend stream 暴露 tool call 时，由各 AgentRuntime adapter 归一化 source-read event。Source 分类为：

```text
gpu_wiki | reference_projects | workspace | public_web | unknown
```

只记录：

- source kind；
- workspace-relative path 或 sanitized public reference；
- allowlisted query tool 和 sanitized search keyword；
- operation duration 与 status；
- repeated-read count。

不记录 file content、raw tool output、full transcript、不受限制的 shell command、credential、用户绝对路径或 private URL parameter。若某 backend 无法暴露完整 read，标记 `source_read_coverage=partial`。

Tool duration 测量 I/O/tool execution，不代表 model comprehension。更宽的 research phase 包含 model reasoning time。

### Read-only outcome normalization

Telemetry 分开记录当前 fact：

- runtime exit/timeout/failure category；
- 存在时的 memory quality/correctness/performance field；
- Git HEAD 与 kernel-blob effect；
- normalized observed outcome。

Observed outcome：

```text
accepted | performance_rejection | validation_failure |
runtime_failure | infrastructure_failure | interrupted | unknown
```

该 normalization 只用于 diagnostic。如果 memory 与 Git 不一致，输出 `unknown` 和 `memory_git_disagreement` 等 reason code；不得 repair、reset、reject 或改变 campaign authority。

### Summary

`iteration.summary.json` 包含：

- total 与 per-attempt wall time；
- phase wall-time 和 percentage breakdown；
- sandbox operation breakdown；
- source-read count、unique ref、repeat 与 tool time；
- per-attempt 与 aggregate structured phase token usage、unattributed usage、coverage 和 reconciliation status；
- changed-file metadata；
- observed outcome 与 reason code；
- 每部分 coverage/measurement quality；
- unattributed duration。

Percentage 使用 non-overlapping top-level phase wall time 除以 total iteration wall time。禁止通过虚构 attribution 强行凑到 100%。

### 测试

使用 fake clock 和 fixture stream 覆盖：

- 完整 explicit marker；
- marker 缺失或乱序；
- inferred 和 unattributed phase timing；
- 完整、重复、缺失、嵌套、重叠和未闭合的 token marker interval；
- normalized usage delta、terminal usage、backend capability unavailable 和 inconsistent reconciliation；
- 一个 `vN` 下多个 attempt，包括缺少 terminal usage 的 attempt；
- Claude/Qoder/Codex source 与 token-event normalization；
- sandbox retry/fallback duration 且不重复计数；
- source-path sanitization 与 private-data rejection；
- memory/Git disagreement 输出 `unknown`；
- 证明 telemetry 只写 ignored `.atrex/telemetry/`。

### Success criteria

- 一个普通 iteration 产生有界 local trace 和 summary；
- total wall time、Agent time 和 sandbox duration 在定义 tolerance 内 reconciliation；
- phase、source 与 token coverage 显式报告 uncertainty；
- attributed token usage 加 unattributed usage 与 observed terminal usage reconciliation，或报告显式 inconsistency；
- 不增加额外 Agent 或 GPU call；
- Git、memory、stall 与 acceptance 行为不变；
- 测量并限制 overhead；如果不足以 default-on，则 trace 改为 opt-in 或 sampled。

### Stop gate

v1 telemetry 完成后，先检查真实 iteration summary，再增加其他 field、其他 campaign phase、remote telemetry、dashboard 或 Supervisor Agent。只增加能够回答真实运维或 Agent-quality 问题的 signal。

## Phase 3：仅在有依据时进行 shadow observation

### Activation condition

至少满足以下一项时才启动：

- candidate 被接受后又证明 incorrect 或 policy-invalid；
- Agent 与 orchestrator record 的 disagreement 已足以影响后续 iteration；
- unattended campaign 需要独立可检查的 acceptance evidence；
- 某个计划中的行为变更需要当前 decision-disagreement 数据；
- maintainer 无法从 compact artifact 判断 candidate 为什么被接受。

### Shadow rule

Shadow logic 只观察，不修改：

```text
Agent 按当前方式完成 iteration
→ shadow reader 检查现有 Git/memory/evaluator artifact
→ shadow logic 输出 accept/reject/unknown 与 reason
→ 当前 Git、memory、stall 和 campaign flow 继续保持权威
```

### 默认不重复 GPU 工作

第一轮 shadow 复用已有 compact result。证据缺失时输出 `unknown`，不会自动启动额外 profile 或 evaluator run。可以用独立实验测量 independent rerun 的价值和成本。

### 建议 output

```python
@dataclass(frozen=True)
class ShadowIterationAssessment:
    version: int
    agent_outcome: str
    shadow_outcome: Literal["accept", "reject", "unknown"]
    reason_codes: tuple[str, ...]
    evidence_refs: tuple[str, ...]
    decision_matches: bool | None
```

### Metric

- decision disagreement rate；
- 因证据缺失导致的 `unknown` rate；
- confirmed false accept rate；
- confirmed false reject rate；
- per-backend 差异；
- 额外 processing、token、GPU 与 wall-time 成本。

### Go/no-go gate

只有 shadow evidence 证明存在实质性 reliability 问题，而且拟议 gate 成本可接受时，才转移 acceptance authority。一个严重 false accept 可以成为充分依据；低严重度 disagreement 需要重复证据。

如果 shadow decision 持续一致，且当前 recovery 已足够，则停止。Shadow tool 可以保留为 diagnostic，也可以删除；不得为了架构对称而继续建设 Controller。

## 条件性机制目录

以下机制不是 roadmap。每项机制都由具体观察问题激活。

### Independent final acceptance gate

**何时激活：** accepted candidate 后续被发现 incorrect、policy-invalid，或记录的 evaluator result 无法支持 acceptance。

**最小变更：** 在当前 ordinary iteration 后增加一个 post-session acceptance check，复用现有 immutable evaluator 和 Git/memory artifact。不要先创建通用 Campaign Controller。

**测量：** 阻止的 false accept、引入的 false reject、额外 GPU time、wall time，以及每 token 的 accepted improvement。

**停止条件：** 以可接受成本消除 acceptance discrepancy。

### Candidate write-scope enforcement

**何时激活：** Agent 对 evaluator、ground truth、historical state、hidden helper file 或 Git authority 的修改导致 validation 不可信。

**最小变更：** session 前 snapshot protected path，session 后检查实际 changed path；机械恢复无害 protected-file change，只 reject 会影响 candidate 或无法恢复的 violation。

**测量：** 发现的 scope violation、机械恢复后保留的 valid candidate、false rejection 与 repair 成本。

**不要预建：** 通用 workspace transaction engine，除非多个真实 call site 证明其必要。

### Bounded candidate repair

**何时激活：** independent acceptance gate 经常拒绝本可在不改变 optimization hypothesis 的情况下修复的 candidate，而把每次 rejection 推到新 version 会浪费实质成果。

**最小变更：** 使用现有 AgentRuntime 和当前 candidate 发起一次 typed repair request。先从一次 repair 开始，不预设两次。

**测量：** repair success rate、额外 token/GPU 成本、wall time，以及 clean repair session 是否丢失有用上下文。

**停止或回滚条件：** repair 很少成功，或成本高于新 iteration。

### Durable token accounting

**何时激活：** 使用 token budget 的 campaign 被重启，而重置后的 spend 对预算预期造成实质影响。

**最小变更：** 只在现有 ignored orchestrator state 中持久化 accumulated token，并保持 backward-compatible read。

**不要增加：** campaign phase、stop ledger、status snapshot 或 event history，除非分别获得依据。

### Operation receipt

**何时激活：** provider retry 或 process restart 会重复昂贵的 profile/evaluator operation。

**最小变更：** 只围绕该 operation 增加 idempotency key 和 compact receipt。

**不要增加：** 如果只有一个 effect 需要去重，不建设完整 StepJournal。

### Writeback journal

**何时激活：** 真实或 fault-injected interruption 发生在 validated candidate、Git commit 与 memory writeback 之间，且现有事实无法可靠 reconciliation。

**最小变更：** 只 journal writeback transaction 与其 effect id。

**何时升级为更广 StepJournal：** 多个更早 phase 也需要 durable resume，而且各自 operation receipt 不足以恢复。

### Two-phase C1/C2 Git writeback

**何时激活：** accepted-kernel provenance 必须记录进 committed memory，而且当前 single-commit convention 已造成真实 audit 或 recovery 问题。

**最小变更：** C1 commit accepted source；C2 record 引用 C1 的 memory。

**测量：** recovery correctness、history clarity、额外 commit，以及对 stall/incumbent logic 的影响。

**不能仅因以下理由采用：** 如果没有 consumer 需要精确 provenance，理论上的 self-referential commit hash 不足以证明值得增加两阶段 commit。

### Read-only CampaignSnapshot

**何时激活：** 真实 caller——operator tool、monitor、bucket scheduler、dashboard 或 external integration——需要稳定 status，而当前各自重复实现不一致读取。

**最小变更：** 从现有 Git、memory、stall 和 manifest 派生 read model，不创建 writable status file。

### Common `step()` interface

**何时激活：** ordinary campaign、bucket 或 layer path 出现具体重复 bug，或某项重复 feature change 可以由 shared step result 消除。

**最小变更：** 只抽取已经证明共享的 transition，不一次抽象所有 path 的全部 phase。

**同一 PR 不改变：** bucket parallelism、immediate aggregation 或 layer ROI scheduling，除非观察到的问题明确要求。

### Full Campaign Controller

**何时激活：** 多个已经独立证明合理的 mechanism 现在共享同一个 transaction owner，而且 caller 正在重复构造同一 ordering。

完整 Controller 应是多个真实 seam 形成后的 consolidation，而不是第一个 abstraction。

## Problem-to-change protocol

每个超出行为保持 extraction 的 harness change，都应从一份短 issue 或 design note 开始：

```text
Problem:
  哪个可观察行为错误或成本过高？

Evidence:
  Reproduction、incident、fixture 或频率测量。

Current authority:
  当前哪个 module/session 拥有该行为？

Smallest mechanism:
  最少需要增加什么 state/interface？

Success metric:
  哪项指标必须提升，哪些不能退化？

Rollback:
  如何禁用或回滚该机制？

Stop condition:
  如果该机制解决问题，明确不继续建设什么？
```

“`optimize.py` 很大”不是完整问题描述。Issue 必须指出 change-locality、correctness、recovery 或 operating cost。

## 测试策略

### 行为保持 extraction

移动前使用 characterization test，移动后使用 interface-level test。Public seam parity 建立后，删除只固定旧 private placement 的测试。

### 行为变更 mechanism

实现前优先提供能够展示观察问题的 failing test 或 replay fixture。测试应覆盖新 mechanism 的窄 contract。

示例：

- false accept → evaluator/policy mismatch fixture；
- token reset → restart fixture；
- duplicate validation → 相同 idempotency key replay；
- partial writeback → 每个已记录 effect 后注入 interruption；
- scope violation → protected-path mutation fixture；
- bucket drift → 同一个 accepted bucket result 走两条 path。

### Real GPU qualification

Unit test 使用 compact observation 和 temporary Git repository。Real GPU test 只用于当前 activated mechanism 的 focused qualification。不能给每个结构性 PR 都增加完整 campaign GPU run。

## Metric 与 non-regression

Harness mechanism 只有在目标 reliability metric 提升、同时 optimization-efficiency 没有不可接受退化时，才算成功。

### Reliability metric

- invalid accepted candidate rate；
- duplicate side-effect rate；
- restart recovery success rate；
- memory/Git consistency rate；
- manual recovery time；
- backend-specific runtime failure rate。

### Optimization-efficiency metric

- 每 100k Agent token 的 accepted improvement；
- 每个 accepted improvement 的 GPU minute；
- 到第一次 improvement 的 median iteration 数；
- candidate repair success rate；
- campaign completion rate；
- 每个 logical iteration 的 wall time。

### 解释原则

代码组织更清晰本身不是 product improvement 的证据。如果 reliability 提升，但每 token 或每 GPU minute 的 accepted improvement 实质退化，应重新评估 mechanism 和默认 activation。

## 近期交付计划

### PR 0 — 修订 RFC

增加本问题驱动计划的中英文版本，不改变 runtime 行为。

### PR 1 — runtime characterization

- 固定当前 Claude/Qoder/Codex 的 command、environment、token、failure、process-guard 与 hydration 行为；
- 识别现有 private test 中哪些是权威语义，哪些只是偶然实现；
- 使用 public-safe evidence 记录当前 backend failure category；
- 不改变 campaign authority 或 prompt。

### PR 2 — AgentRuntime extraction

- 引入最小 `AgentRuntime.run()` seam；
- 将真实 host variation 移到 Claude/Qoder/Codex adapter 后；
- 将一个 selected runtime 注入 campaign 的所有 path；
- 持久化并强制 campaign-level runtime binding，同时兼容 legacy adoption；
- 保持所有当前行为和 artifact；
- 将测试迁移到 seam，并删除被替代的 private-placement test。

### PR 3 — ordinary-iteration observability

- 增加 ignored local trace 与 summary format；
- 为 Agent、phase marker、source read、sandbox、file change 和 observed outcome event 打时间戳；
- 通过 selected AgentRuntime adapter 归一化 backend usage delta、terminal usage 和成功 marker receipt；
- 只把 structured token usage 归因到完整 explicit phase interval，并保留 unattributed bucket；
- instrument sandbox timing，但不增加 GPU call；
- 增加 read-only summary renderer；
- 证明 Git、memory、stall 和 acceptance 行为不变。

### PR 3 后的决策门

审阅 Phase 0 evidence、runtime extraction 和真实 ordinary-iteration summary。

可能结果：

1. **停止 harness 工作：** host coupling 是唯一被证明的问题；投入 Optimization Agent quality。
2. **改进 Optimization Agent：** telemetry 发现高成本 phase、重复 source 使用或 backend-specific inefficiency。
3. **运行 shadow diagnostic：** acceptance 或 evidence quality 仍不确定。
4. **修复一个窄问题：** token persistence、acceptance、write scope、retry deduplication 或其他具体问题。
5. **重新评估架构：** 只有多个已证明 mechanism 开始需要共同 ownership 时才进行。

不存在预批准的 PR 3–12 路线。

## Deferred north-star responsibility model

如果证据最终支持更深控制，保持以下 responsibility direction：

- **Optimization Agent：** evidence interpretation、research、planning、candidate generation、debugging；
- **AgentRuntime：** Claude/Qoder/Codex host adaptation；
- **GPU transport/evaluator：** bounded profile 与 evaluation observation；
- **domain policy：** 根据 observation 产生 typed decision；
- **workspace/Git module：** candidate 与 commit mechanics；
- **controller（如果需要）：** ordering 与 authoritative transition。

该模型可以防止 Agent 成为唯一机械权威，但不要求现在创建所有 module。

## 非目标

本计划不预先授权：

- 完整 Campaign Controller；
- StepJournal；
- C1/C2 writeback；
- Controller-owned mandatory profiling；
- Agent-authored receipt schema；
- bounded repair turn；
- generic GPU Provider abstraction；
- generic CampaignWorkspace abstraction；
- common bucket/layer `step()` API；
- dashboard/server；
- LoopX integration；
- event ledger；
- runtime plugin system；
- 单个 campaign 内的 dynamic runtime switching 或 cross-runtime handoff；
- tracked raw telemetry、remote telemetry infrastructure 或 dashboard；
- inferred 或按时间比例计算的 phase token estimate、token-price/currency accounting，或 non-ordinary session 的 phase token attribution；现有 campaign token-budget accounting 保持不变；
- 在评估 v1 之前覆盖 non-ordinary phase 的 observability；
- per-step worktree。

这些机制未来都可以在具有 evidence 和 activation condition 时单独提出。

## 考虑过的替代方案

### 立即实现完整 Controller roadmap

拒绝。它会在收益被测量前增加多个 state owner 和 failure mode。

### 在 production incident 前完全不做架构变更

拒绝。现有三个 backend 已经证明 runtime seam 存在，而且提前 characterization 的成本低于高代价 incident。

### 只因为 `optimize.py` 很大就重构

拒绝。文件大小只是 signal。Extraction 应遵循不同 change reason 和 tested seam，而不是以行数为目标。

### 每个问题都做 ad hoc patch

拒绝。Problem-driven development 仍然使用稳定 responsibility vocabulary、characterization、显式 ownership 和 stop condition。它不是 patch-driven development。

### 立即使用 LoopX 作为 controller

拒绝。LoopX 未来可以管理粗粒度 Atrex campaign，但它不能证明 Atrex inner loop 现在就需要新的 authority model。

## 风险与缓解措施

| 风险 | 缓解措施 |
| --- | --- |
| Evidence collection 变成新的大型 observability 项目 | 只收集与近期决策绑定的 metric；无法获取时记录 unknown |
| AgentRuntime extraction 变成推测性 plugin framework | 只实现三个当前 adapter 和一个最小 interface |
| Shadow check 静默变成 authority | 保持 diagnostic output，禁止 Git/memory/stall write |
| Problem-driven work 退化成局部 patch | 强制 owner、reproduction、success metric、rollback 和 stop condition |
| 低频但严重 failure 被 frequency metric 忽略 | 一次高严重度 false accept 或不可恢复 corruption 即可成为充分证据 |
| Harness 工作因惯性继续 | 每个 mechanism 后强制 decision gate，并把“停止”作为正常结果 |
| Agent-quality 工作被挤占 | 跟踪 optimization-efficiency metric 和 opportunity cost |

## Open question

以下问题有意推迟到存在 evidence 时再回答：

- False acceptance 是否是当前有实质影响的问题？
- Compact artifact 缺少哪些 accepted-result fact？
- Independent rerun 的成本是否低于 manual recovery？
- 一次 repair turn 是否值得，它应复用还是替换原 session？
- Token-budget continuity 对实际部署的 campaign 是否重要？
- 哪一条 duplicated campaign path 首先产生了真实 behavior drift？
- 当前是否有 external caller 需要稳定 status projection？

## 最终决策

采用问题驱动、证据门控的 harness 演进。先完成 runtime characterization、不可变 campaign-level runtime binding、最小 AgentRuntime extraction，以及 local-only ordinary-iteration observability。所有更深的 control-plane mechanism 都保持 deferred，直到具体问题、可测量 success condition 与 rollback plan 能够证明其必要性。
