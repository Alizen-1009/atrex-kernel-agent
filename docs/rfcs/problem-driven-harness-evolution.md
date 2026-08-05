# RFC: Problem-Driven Harness Evolution

[简体中文](problem-driven-harness-evolution.zh-CN.md)

- **Status:** Proposed
- **Date:** 2026-08-04
- **Scope:** Atrex Kernel Agent harness evolution
- **Near-term commitment:** characterize current behavior, extract the existing multi-runtime seam, and add local-only observability for ordinary iterations

## Summary

Atrex needs a clearer harness, but a larger harness is not automatically a better optimizer. Additional controllers, journals, receipts, policies, and state machines may improve reliability while also increasing GPU cost, token cost, latency, migration risk, and the number of failure modes. They do not make the Optimization Agent better at interpreting profiles or generating faster kernels.

This RFC adopts a problem-driven strategy:

> Observe a real failure or repeated change cost, reproduce it, measure it, introduce the smallest mechanism that owns it, and stop when the measured problem is solved.

The only architectural extraction currently justified by multiple real implementations is `AgentRuntime`: Atrex already supports Claude, Qoder, Codex, and Pi, while their command, environment, token, authentication, and skill-hydration behavior is mixed into `orchestrator/optimize.py`.

This is repository-level multi-runtime support, not multi-Agent orchestration. Each campaign selects exactly one runtime when its workspace is created. Setup, framework baseline, every optimization iteration, repair/salvage, conversion, and finalization for that campaign use the same recorded runtime. Runtime failure never causes an automatic backend switch; changing backend requires a new campaign/workspace.

Upstream `main` also contains the opt-in `long_horizon` entry point, with its own episode supervisor, journal, isolated worktrees, handoff recovery, and ABBA verification. This RFC treats that package as an existing adjacent execution mode and does not remove or redesign it. Restrictions below on adding a Controller, journal, or additional worktree lifecycle apply to the ordinary `orchestrator.optimize` loop covered by this proposal, not to the already-shipped `long_horizon` package.

A second near-term problem is now explicit: maintainers cannot reliably answer where one ordinary optimization iteration spent its time, which sources it read, which GPU operations dominated, or how backend-reported token usage was distributed across explicit workflow phases. v1 observability addresses that problem without changing candidate acceptance, Git, memory, token-budget accounting, or the optimization loop.

All broader mechanisms are conditional. Controller-owned acceptance, candidate write scopes, repair turns, durable step journals, two-phase Git writeback, a common `step()` interface, and bucket/layer migration are options with explicit activation conditions—not a predetermined PR sequence.

## Decision

1. Treat the current Agent-driven optimization loop as the behavioral baseline.
2. Collect evidence before changing its authority model.
3. Extract `AgentRuntime` behavior-preservingly because four real backends already justify that seam.
4. Bind each campaign workspace to one runtime for its full lifecycle; never switch backend automatically between iterations or recovery turns.
5. Add local-only, read-only observability for ordinary optimization iterations without changing Git or memory authority.
6. Add shadow decision observation only when acceptance uncertainty justifies it.
7. Introduce each deeper harness mechanism only when a concrete problem and acceptance metric justify it.
8. Do not commit to a full Campaign Controller implementation, event ledger, StepJournal, or bucket/layer rewrite in advance.
9. Preserve a north-star responsibility model so local fixes do not recreate cross-host or cross-path coupling.

## Why this replaces the earlier plan

The earlier draft specified a complete Campaign Controller and a PR 0–12 migration. That design answered “how could the harness be structured?” but did not first answer:

- How often does the current Agent accept an invalid candidate?
- How often do interrupted sessions fail to recover from Git and memory?
- How often do memory, Git, and stall state disagree materially?
- How much duplicate GPU work would independent validation add?
- Does a clean repair session outperform continuing in the original session?
- Are bucket callbacks or layer-specific loops causing observed defects?
- Is harness reliability, rather than Agent optimization quality, the current bottleneck?

Without this evidence, implementing the full design would be speculative. This RFC keeps the useful responsibility vocabulary but changes the roadmap from architecture-led to evidence-gated.

## Agent and harness

### Optimization Agent

The Optimization Agent is the model-driven actor running through Claude, Qoder, or Codex. It currently:

- reads prior memory and profile evidence;
- researches gpu-wiki and reference sources;
- chooses an optimization direction;
- writes plans and candidate code;
- invokes sandbox tools for profiling, correctness, and benchmarking;
- records results and Git state according to the current prompt contract.

Improving this actor means improving profile interpretation, search, planning, framework knowledge, candidate quality, debugging, and learning from prior attempts.

### Atrex harness

The harness surrounds the Agent. It includes:

- CLI and campaign orchestration;
- host-process launch and guards;
- sandbox/gateway transport;
- evaluator integration;
- workspace and Git conventions;
- memory artifacts;
- retries, budgets, bucketing, aggregation, and layer scheduling.

Harness changes can improve reliability, observability, recovery, consistency, and maintainability. They do not directly improve the Agent's GPU optimization intelligence.

## Principles

### 1. Evidence before mechanism

A large file, an attractive architecture, or a possible future dashboard is not enough to justify a new state owner. Each mechanism needs a concrete problem, evidence, and a measurable exit condition.

### 2. One problem, one owner, one change

Do not solve host variation, candidate acceptance, crash recovery, status projection, and bucket scheduling in one refactor. Assign each observed problem to the nearest owning module and land one coherent change.

### 3. Shadow before authority

When the problem concerns decisions—such as whether a candidate should be accepted—first compute a shadow decision without changing Git, memory, stall, or campaign flow. Compare decisions before transferring authority.

### 4. Prefer derived facts

Use existing Git, `memory/vN.json`, profile artifacts, and manifests before adding a new persistent state file. Add durable state only for facts that cannot be reconstructed safely and whose loss has caused a real problem.

### 5. Preserve the Agent feedback loop

Do not add an extra profile, evaluator run, clean repair session, or structured output requirement without measuring its effect on tokens, GPU time, wall time, and accepted improvements.

### 6. Stop after the problem is solved

A successful narrow fix is not a reason to build the rest of a north-star architecture. Every stage has an explicit stop gate.

### 7. Keep changes reversible

Behavior-preserving extraction and behavior-changing enforcement remain separate. Every new enforcement mechanism must have a rollback path.

## Current evidence

### Confirmed: four real Agent runtimes are coupled to one module

Atrex supports Claude, Qoder, Codex, and Pi. Host-specific behavior currently spans functions such as:

- `_session_command`;
- `_session_env`;
- `_tokens_from_stream`;
- `_agent_auth_hint`;
- `_agent_runtime_directive`;
- `_baseline_driver_directive`;
- `_plan_generator_directive`;
- host-specific parts of `link_runtime`;
- process and dependency guards used by `run_session`.

Tests patch these private functions directly. This is a real seam because multiple implementations already exist and change for different reasons.

### Confirmed: runtime token accounting is process-local

`Campaign.tokens_spent` is initialized in memory for each process. A restarted campaign may not preserve the exact prior token spend. This is a concrete code property, but it becomes a user-impacting problem only when token-budget continuity across restart matters in real campaigns. The fix should be scoped to that problem if and when confirmed.

### Confirmed: control-flow duplication exists

Ordinary `Campaign.run()`, framework baseline, conversion, workload buckets, and layer scheduling contain overlapping session/validation/writeback logic. This is a maintainability signal, not yet proof that a universal controller is the right solution. Before unifying paths, identify an actual behavior drift or repeated change that the shared seam would eliminate.

### Confirmed: ordinary iterations lack decision-ready telemetry

Current session accounting exposes aggregate exit, timeout, token-budget, and output-tail facts, while profile and evaluator artifacts are stored separately. It does not provide one correlated iteration timeline that answers phase duration, source-read metadata, sandbox-operation duration, or unattributed time. This is an explicit operating and Agent-improvement requirement, so a bounded observability slice is justified without waiting for an acceptance incident.

### Not yet established

The repository does not yet provide quantified evidence for:

- false acceptance rate;
- decision drift between Agent claims and mechanical observations;
- duplicate side effects after resume;
- unrecoverable phase-level interruptions;
- material memory/Git divergence;
- bucket callback race frequency;
- layer scheduling defects caused by the separate loop;
- the cost/benefit of Controller-owned mandatory profiling;
- the value of clean repair turns;
- the need for a full Campaign Controller API.

These remain hypotheses.

## Current loop is the baseline

The ordinary iteration remains behaviorally unchanged until evidence supports a narrower change:

```text
Agent reads current workspace and memory
→ profiles/reuses profile
→ researches and plans one lever
→ edits and debugs candidate
→ validates and benchmarks
→ records memory
→ commits a win or records a rejection
→ outer campaign decides whether to continue
```

The baseline must be characterized before extraction or enforcement changes it.

## Phase 0: establish a current-state baseline

### Objective

Determine which harness problems occur in practice and which costs dominate campaign outcomes.

### Inputs

Use existing public-safe artifacts where possible:

- Git history and kernel blobs;
- `memory/vN.json`;
- stall state;
- session result summaries already retained by the orchestrator;
- profile/evaluator compact outputs;
- bucket and aggregate manifests;
- existing tests and issue history.

Do not ingest raw private transcripts, credentials, or unbounded gateway logs into repository fixtures.

### Failure taxonomy

Classify observations without collapsing them into one “failure” bucket:

| Class | Meaning |
| --- | --- |
| `runtime_failure` | Claude/Qoder/Codex/Pi process did not complete its contract |
| `candidate_validation_failure` | Candidate compile, correctness, or admissibility check failed |
| `performance_rejection` | Candidate was valid but did not significantly improve the incumbent |
| `infrastructure_failure` | Gateway/provider could not produce a trustworthy observation |
| `state_recovery_failure` | Git/memory/worktree state could not be reconciled safely |
| `blocked` | External action is required before continuing |
| `accepted` | Candidate passed the current contract and became incumbent |

### Baseline metrics

Measure at least:

- Agent-declared win versus mechanically inspectable outcome disagreement;
- accepted candidates later found incorrect or non-compliant;
- session interruption frequency;
- successful versus manual recovery after interruption;
- repeated experiment frequency after incomplete rounds;
- memory/Git/stall inconsistencies that affect the next action;
- tokens per accepted improvement;
- GPU minutes per accepted improvement where observable;
- wall time to first accepted improvement;
- campaign completion rate;
- backend-specific failure distribution.

If a metric cannot be reconstructed from current compact artifacts, record it as unknown. Do not add broad instrumentation until the missing metric is tied to a decision.

### Deliverable

A compact, public-safe diagnosis should answer:

1. Which harness failures are real and recurring?
2. Which are rare but severe?
3. Which are only architectural concerns with no observed impact?
4. Which failure class consumes the most human, token, GPU, or wall-clock cost?
5. What is the smallest next change that can be evaluated?

### Stop gate

If no material harness problem is found, stop after the justified `AgentRuntime` extraction. Redirect effort toward Optimization Agent quality instead of manufacturing a Controller project.

## Phase 1: behavior-preserving AgentRuntime extraction

### Why this stage is justified now

There are already four real backends. One adapter means a hypothetical seam; four adapters make host variation real.

### Minimal interface

```python
class AgentRuntime(Protocol):
    @property
    def id(self) -> str: ...

    def run(self, request: AgentRunRequest) -> AgentRunResult: ...
```

The first request should remain close to current behavior. Do not make it a complete future semantic-turn protocol before a caller needs that shape.

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

Concrete adapters own only host variation that exists today:

- command construction;
- provider settings;
- authentication environment differences;
- token/output parsing;
- session identity;
- host-specific skill/plugin hydration;
- host-specific diagnostic classification.

Shared process supervision and dependency guards may live in a runtime-internal module. Campaign state persists one immutable runtime id; the session boundary resolves that id through the runtime registry and does not branch on host names. Runtime-instance injection is not required for v1.

### Implementations

- `ClaudeRuntime`;
- `QoderRuntime`;
- `CodexRuntime`.

Do not add Cursor/OpenCode placeholders, plugin discovery, runtime manifests, or third-party adapter installation.

### Campaign-level runtime binding

Repository support for several runtimes does not permit one campaign to mix them. The composition root selects one adapter when the campaign is created and injects that instance into every campaign path.

Persist the selected runtime additively with existing ignored workspace policy state, for example:

```json
{
  "mode": "production",
  "framework": "Triton",
  "agent_runtime": "codex"
}
```

Invariants:

- setup, framework baseline, ordinary iterations, conversion, repair, salvage, bucket children, and layer-boundary work inherit the recorded runtime;
- resume fails closed when the requested runtime differs from the recorded runtime;
- runtime failure is classified and retried or surfaced, never handled by switching backend;
- changing runtime requires a fresh campaign/workspace;
- backend comparisons use separate campaigns with the same starting inputs and budgets.

Legacy workspaces without `agent_runtime` are `legacy_unbound`. The first post-upgrade run records the explicitly requested runtime before launching a session and reports that adoption. After adoption, the binding is immutable.

### Compatibility requirements

Preserve exactly:

- CLI flags;
- commands and settings precedence;
- environment behavior;
- dependency/process guards;
- token accounting semantics;
- fresh-session behavior;
- skill hydration;
- one-runtime-per-campaign binding, including backward-compatible adoption for legacy workspaces;
- current prompts;
- setup, framework baseline, ordinary iteration, conversion, bucket, and layer behavior.

### Tests

Characterize current commands, environment, token parsing, failures, hydration, and process termination first. Then move those tests to the `AgentRuntime` interface and remove superseded private-function tests.

### Success criteria

- `Campaign` no longer branches on Claude/Qoder/Codex/Pi for session execution;
- all four adapters pass a shared contract suite;
- every path in one campaign uses its recorded runtime, and mismatched resume fails before launching an Agent session;
- existing CLI and campaign tests remain green;
- no token/GPU/wall-time change is expected beyond test noise;
- no new runtime dependency or user-facing state is introduced.

### Stop gate

After extraction, proceed only to the already-approved ordinary-iteration observability slice. Do not automatically proceed to Controller, journal, acceptance enforcement, or broader prompt redesign.

## Phase 2: ordinary-iteration observability

### Scope

v1 covers ordinary optimization versions `vN` only. It does not instrument setup baseline, framework baseline, conversion, decomposition, recombination, aggregate validation, or `long_horizon` episodes. Those paths may be added later only after the ordinary-iteration trace proves useful. It also attributes backend-reported token usage to explicit ordinary-iteration phases on a best-effort basis; a backend without message-level usage degrades to `unavailable` without changing the Agent session.

Observability is read-only. It does not decide candidate acceptance, repair state, Git commits, memory contents, stall, or the next campaign action.

### Questions v1 must answer

For one ordinary iteration:

1. What was the total wall time?
2. How long did each Agent attempt run?
3. How much time was spent in profile, research, planning, implementation, correctness, and benchmark phases?
4. How many input, output, cache-read, cache-write, and total tokens were reported inside complete explicit intervals for those phases?
5. Which gpu-wiki, reference-project, workspace, or public-web sources were read?
6. How long did source-read tools and sandbox operations take?
7. Which files changed during the attempt?
8. What runtime, memory, and Git outcomes were observed?
9. Which portions of the trace are exact, explicit, inferred, partial, unavailable, or unattributed?

### Local-only storage

Detailed traces and summaries live in ignored local state:

```text
.atrex/telemetry/
└── v12/
    ├── attempt-<session-id>.jsonl
    ├── attempt-<session-id>.summary.json
    ├── iteration.summary.json
    └── iteration.brief.md
```

v1 does not write telemetry into `memory/vN.json`, does not add a metadata commit, and does not alter the candidate worktree. A later proposal may promote a compact summary into durable memory only when a real consumer requires it.

### Correlation and event schema

Every event carries bounded correlation fields:

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

Representative events:

- `iteration_started` / `iteration_completed`;
- `agent_session_started` / `agent_session_completed`;
- `phase_started` / `phase_completed`;
- normalized `agent_usage_delta`, `agent_terminal_usage`, and successful `agent_phase_marker` receipt events;
- `source_read_started` / `source_read_completed`;
- `sandbox_operation_started` / `sandbox_operation_completed`;
- `file_changed`;
- `runtime_failure_observed`;
- `outcome_observed`.

Use monotonic time for duration and wall-clock UTC only for correlation/readability.

### Phase attribution

Use a mixed model:

1. **Exact harness boundaries:** iteration, Agent process, and sandbox operation start/end.
2. **Explicit Agent markers:** the ordinary iteration prompt asks the Agent to mark `research`, `planning`, and `implementation` phase boundaries through a small local tracing helper.
3. **Inferred fallback:** backend tool events and commands may infer missing phases—for example wiki reads as research, plan writes as planning, kernel edits as implementation, and evaluator commands as validation.
4. **Unattributed:** time that cannot be assigned without guessing remains unattributed.

Every phase value includes one of:

```text
exact | explicit | inferred | partial | unavailable
```

Top-level phase spans must not be double-counted. Nested tool duration is reported separately from phase wall time.

### Phase token attribution

Token attribution uses a stricter model than time attribution:

- the fixed phases are `profile`, `research`, `planning`, `implementation`, `correctness`, and `benchmark`;
- phase intervals may repeat but may not overlap;
- only complete, matching, non-overlapping explicit marker pairs are eligible;
- the marker helper emits a machine-readable receipt only after the trace write succeeds;
- backend adapters normalize message usage as `usage_delta` and final session usage as `terminal_usage`; parser capability alone is not evidence, so a session with no observed delta remains unavailable;
- missing, malformed, nested, or unclosed intervals are never inferred from tool behavior;
- unassigned token usage remains in an explicit `unattributed` bucket;
- input token usage belongs to the phase in which the model processed it, even when the context originated in an earlier phase;
- input, output, cache-read, cache-write, and total counters remain separate; missing backend fields are `null`, not zero;
- if usage deltas exceed terminal usage in total or in any mutually available component, retain the conflict as `inconsistent` rather than clamping or scaling phase values;
- if a backend exposes only terminal usage, phase values are `unavailable`, coverage is zero, and the full total is unattributed.

Phase attribution is attempt-first and iteration-second. Failed and timed-out attempts remain in the aggregate. This telemetry is diagnostic only and does not replace the existing `result.tokens` compatibility total used by campaign token budgets.

### Sandbox operation timing

Instrument local `tools/sandbox.py` boundaries without launching extra GPU work. Record:

- requested and resolved kind (`profile`, `run`, `dev`);
- sanitized command category, not an unrestricted raw command;
- operation id and status;
- queue/wait/execute/total duration when available;
- profile versus correctness versus benchmark classification;
- retry/fallback information;
- bounded artifact references.

A sandbox call may be nested inside a phase. Its duration does not add a second copy to total iteration wall time.

### Metadata-only source trace

v1 source metadata is an explicit best-effort Agent marker, not an automatic reconstruction of backend tool traffic. Classify marked sources as:

```text
gpu_wiki | reference_projects | workspace | public_web | unknown
```

Record only source kind plus a workspace-relative path or sanitized public reference. Summaries may derive count, unique count, and repeats from those markers. Public references retain only credential-free HTTP(S) scheme, authority, and path; query parameters, fragments, and URL userinfo are discarded or rejected.

Do not record file contents, raw tool output, full transcripts, unrestricted shell commands, credentials, user absolute paths, or private URL parameters. The absence of a marker yields `source_read_coverage=unavailable`; v1 does not claim that every read was observed.

### Read-only outcome normalization

Telemetry records current facts separately:

- runtime exit/timeout/failure category;
- memory quality/correctness/performance fields when present;
- Git HEAD and kernel-blob effects;
- normalized observed outcome.

Observed outcomes are:

```text
accepted | performance_rejection | validation_failure |
runtime_failure | infrastructure_failure | interrupted | unknown
```

This normalization is diagnostic only. If memory and Git disagree, emit `unknown` with reason codes such as `memory_git_disagreement`; do not repair, reset, reject, or change campaign authority.

### Summary

`iteration.summary.json` includes:

- total and per-attempt wall time;
- phase wall-time and percentage breakdown;
- sandbox operation breakdown;
- source-read counts, unique refs, repeats, and tool time;
- per-attempt and aggregate structured phase token usage, unattributed usage, coverage, and reconciliation status;
- changed-file metadata;
- observed outcome and reason codes;
- coverage/measurement quality for every section;
- unattributed duration.

Percentages use non-overlapping top-level phase wall time divided by total iteration wall time. They must not be forced to 100% by inventing attribution.

### Tests

Use fake clocks and fixture streams to cover:

- complete explicit markers;
- missing/out-of-order markers;
- inferred and unattributed phase timing;
- complete, repeated, missing, nested, overlapping, and unclosed token marker intervals;
- normalized usage deltas, terminal usage, unavailable backend capability, and inconsistent reconciliation;
- multiple attempts under one `vN`, including attempts without terminal usage;
- Claude/Qoder/Codex/Pi source and token-event normalization;
- sandbox retry/fallback duration without double counting;
- source-path sanitization and private-data rejection;
- memory/Git disagreement producing `unknown`;
- proof that telemetry writes only below ignored `.atrex/telemetry/`.

### Success criteria

- one ordinary iteration produces a bounded local trace and summary;
- total wall time, Agent time, and sandbox duration reconcile within defined tolerances;
- phase, source, and token coverage explicitly report uncertainty;
- attributed token usage plus unattributed usage reconciles to observed terminal usage, or reports an explicit inconsistency;
- no extra Agent or GPU call is introduced;
- no Git, memory, stall, or acceptance behavior changes;
- overhead is measured and remains small enough for default-on local telemetry, otherwise tracing becomes opt-in or sampled.

### Stop gate

After v1 telemetry, inspect real iteration summaries before adding more fields, other campaign phases, remote telemetry, dashboards, or a Supervisor Agent. Add only signals that answer an observed operating or Agent-quality question.

## Phase 3: shadow observation, only when justified

### Activation condition

Start this phase only if at least one of the following is true:

- a candidate was accepted and later shown incorrect or policy-invalid;
- Agent and orchestrator records disagree often enough to affect later iterations;
- unattended campaigns require independently inspectable acceptance evidence;
- a planned behavior change needs current decision-disagreement data;
- maintainers cannot determine why a candidate was accepted from compact artifacts.

### Shadow rule

Shadow logic observes but does not mutate:

```text
Agent completes the current iteration
→ shadow reader inspects existing Git/memory/evaluator artifacts
→ shadow logic emits accept/reject/unknown plus reasons
→ current Git, memory, stall, and campaign flow remain authoritative
```

### No duplicate GPU work by default

The first shadow pass reuses existing compact results. Missing evidence yields `unknown`; it does not automatically launch another profile or evaluator run. A separate experiment may measure the value and cost of independent reruns.

### Suggested output

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

### Metrics

- decision disagreement rate;
- `unknown` rate caused by missing evidence;
- confirmed false accept rate;
- confirmed false reject rate;
- per-backend differences;
- additional processing, token, GPU, and wall-time cost.

### Go/no-go gate

Transfer acceptance authority only when shadow evidence demonstrates a material reliability problem and a proposed gate has acceptable cost. A single severe false accept may justify action; low-severity disagreement requires repeated evidence.

If shadow decisions consistently agree and recovery is adequate, stop. Keep the shadow tool diagnostic or remove it; do not build a Controller for architectural symmetry.

## Conditional mechanism catalog

The following mechanisms are not a roadmap. Each is activated by a specific observed problem.

### Independent final acceptance gate

**Activate when:** accepted candidates are later found incorrect, policy-invalid, or unsupported by the recorded evaluator result.

**Smallest change:** add one post-session acceptance check around the current ordinary iteration. Reuse the existing immutable evaluator and current Git/memory artifacts. Do not introduce a general Campaign Controller first.

**Measure:** false accepts prevented, false rejects introduced, extra GPU time, wall time, and accepted improvements per token.

**Stop when:** the acceptance discrepancy is eliminated at acceptable cost.

### Candidate write-scope enforcement

**Activate when:** Agent modifications to evaluator, ground truth, historical state, hidden helper files, or Git authority make validation untrustworthy.

**Smallest change:** snapshot protected paths before a session, inspect actual changed paths afterward, mechanically restore harmless protected-file changes, and reject only candidate-affecting or unrecoverable violations.

**Measure:** scope violations found, valid candidates preserved after mechanical restore, false rejections, and repair cost.

**Do not prebuild:** a generic workspace transaction engine unless repeated call sites justify it.

### Bounded candidate repair

**Activate when:** an independent acceptance gate rejects candidates that could often be fixed without changing the optimization hypothesis, and pushing every rejection to a new version wastes material work.

**Smallest change:** one typed repair request using the existing AgentRuntime and current candidate. Start with one repair, not an assumed maximum of two.

**Measure:** repair success rate, added token/GPU cost, wall time, and whether clean repair sessions lose useful context.

**Stop or revert when:** repair rarely succeeds or costs more than a new iteration.

### Durable token accounting

**Activate when:** campaigns with token budgets are restarted and the reset spend materially violates budget expectations.

**Smallest change:** persist only accumulated tokens in the existing ignored orchestrator state, with backward-compatible reads.

**Do not add:** campaign phases, stop ledgers, status snapshots, or event history unless separately justified.

### Operation receipt

**Activate when:** provider retry or process restart repeats an expensive profile/evaluator operation.

**Smallest change:** add an idempotency key and compact receipt around that operation only.

**Do not add:** a full StepJournal while only one effect needs deduplication.

### Writeback journal

**Activate when:** real or fault-injected interruptions between validated candidate, Git commit, and memory writeback cannot be reconciled reliably from existing facts.

**Smallest change:** journal only the writeback transaction and its effect ids.

**Escalate to a broader StepJournal only when:** multiple earlier phases also require durable resume and their independent receipts are insufficient.

### Two-phase C1/C2 Git writeback

**Activate when:** accepted-kernel provenance must be recorded inside committed memory and the current single-commit convention has caused an actual audit or recovery problem.

**Smallest change:** C1 commits accepted source; C2 records memory referencing C1.

**Measure:** recovery correctness, history clarity, added commits, and effects on stall/incumbent logic.

**Do not adopt solely because:** self-referential commit hashes are theoretically awkward if no consumer needs exact provenance.

### Read-only CampaignSnapshot

**Activate when:** a real caller—operator tool, monitor, bucket scheduler, dashboard, or external integration—needs stable status and currently reimplements inconsistent reads.

**Smallest change:** derive a read model from existing Git, memory, stall, and manifests. Do not create a writable status file.

### Common `step()` interface

**Activate when:** ordinary campaign, bucket, or layer paths exhibit a concrete duplicated bug or repeated feature change that a shared step result would eliminate.

**Smallest change:** extract the proven shared transition, not every phase of every path.

**Do not change:** bucket parallelism, immediate aggregation, or layer ROI scheduling in the same PR unless the observed problem requires it.

### Full Campaign Controller

**Activate when:** several independently justified mechanisms now share one transaction owner and callers are reconstructing the same ordering themselves.

A full Controller is an emergent consolidation after real seams exist, not the first abstraction.

## Problem-to-change protocol

Every harness change beyond behavior-preserving extraction should start with a short issue or design note containing:

```text
Problem:
  What observable behavior is wrong or costly?

Evidence:
  Reproduction, incident, fixture, or measured frequency.

Current authority:
  Which module/session owns the behavior today?

Smallest mechanism:
  What is the least new state/interface needed?

Success metric:
  What must improve, and what must not regress?

Rollback:
  How is the mechanism disabled or reverted?

Stop condition:
  What will not be built if this mechanism solves the problem?
```

“`optimize.py` is large” is not a complete problem statement. The issue must identify a change-locality, correctness, recovery, or operating cost.

## Testing strategy

### Behavior-preserving extraction

Use characterization tests before moves and interface-level tests afterward. Delete tests that only pin old private placement once public-seam parity exists.

### Behavior-changing mechanisms

Prefer a failing test or replay fixture that demonstrates the observed problem before implementation. Tests should target the new mechanism's narrow contract.

Examples:

- false accept → evaluator/policy mismatch fixture;
- token reset → restart fixture;
- duplicate validation → same idempotency key replay;
- partial writeback → interruption after each recorded effect;
- scope violation → protected-path mutation fixture;
- bucket drift → same accepted bucket result through both paths.

### Real GPU qualification

Unit tests use compact observations and temporary Git repositories. Real GPU tests are focused qualification for the exact mechanism being activated. Do not add full campaign GPU runs to every structural PR.

## Metrics and non-regression

A harness mechanism is successful only when its target reliability metric improves without unacceptable optimization-efficiency regression.

### Reliability metrics

- invalid accepted candidate rate;
- duplicate side-effect rate;
- restart recovery success rate;
- memory/Git consistency rate;
- manual recovery time;
- backend-specific runtime failure rate.

### Optimization-efficiency metrics

- accepted improvements per 100k Agent tokens;
- GPU minutes per accepted improvement;
- median iterations to first improvement;
- candidate repair success rate;
- campaign completion rate;
- wall time per logical iteration.

### Interpretation

Code organization alone is not proof of product improvement. If reliability improves but accepted improvements per token or GPU minute regress materially, reassess the mechanism and its default activation.

## Near-term delivery plan

### PR 0 — revised RFC

Add this problem-driven plan in English and Chinese. No runtime behavior changes.

### PR 1 — runtime characterization

- pin current Claude/Qoder/Codex/Pi command, environment, token, failure, process-guard, and hydration behavior;
- identify which existing private tests are authoritative and which are incidental;
- record current backend failure categories using public-safe evidence;
- do not change campaign authority or prompts.

### PR 2 — AgentRuntime extraction

- introduce the minimal `AgentRuntime.run()` seam;
- move concrete host variation behind Claude/Qoder/Codex/Pi adapters;
- inject one selected runtime into all paths of a campaign;
- persist and enforce the campaign-level runtime binding with legacy adoption;
- keep all current behavior and artifacts;
- migrate tests to the seam and remove superseded private-placement tests.

### PR 3 — ordinary-iteration observability

- add the ignored local trace and summary format;
- timestamp Agent, phase-marker, source-read, sandbox, file-change, and observed-outcome events;
- normalize backend usage deltas, terminal usage, and successful marker receipts through the selected AgentRuntime adapter;
- attribute structured token usage only to complete explicit phase intervals and retain an unattributed bucket;
- instrument sandbox timing without adding GPU calls;
- add a read-only summary renderer;
- prove that Git, memory, stall, and acceptance behavior are unchanged.

### Decision gate after PR 3

Review Phase 0 evidence, the runtime extraction, and real ordinary-iteration summaries.

Possible outcomes:

1. **Stop harness work:** host coupling was the only justified problem; invest in Optimization Agent quality.
2. **Improve the Optimization Agent:** telemetry identifies a costly phase, repeated source use, or backend-specific inefficiency.
3. **Run a shadow diagnostic:** acceptance or evidence quality is uncertain.
4. **Fix one narrow observed problem:** token persistence, acceptance, write scope, retry deduplication, or another concrete issue.
5. **Revisit architecture:** only if several proven mechanisms now require common ownership.

There is no pre-approved PR 3–12 sequence.

## Deferred north-star responsibility model

If evidence eventually justifies deeper control, preserve this responsibility direction:

- **Optimization Agent:** evidence interpretation, research, planning, candidate generation, debugging;
- **AgentRuntime:** Claude/Qoder/Codex/Pi host adaptation;
- **GPU transport/evaluator:** bounded profile and evaluation observations;
- **domain policy:** typed decisions from observations;
- **workspace/Git module:** candidate and commit mechanics;
- **controller, if needed:** ordering and authoritative transitions.

This model prevents the Agent from becoming the sole mechanical authority, but it does not require creating every module now.

## Non-goals

This plan does not pre-authorize:

- a full Campaign Controller;
- a StepJournal;
- C1/C2 writeback;
- Controller-owned mandatory profiling;
- Agent-authored receipt schemas;
- bounded repair turns;
- a generic GPU Provider abstraction;
- a generic CampaignWorkspace abstraction;
- a common bucket/layer `step()` API;
- a dashboard/server;
- LoopX integration;
- an event ledger;
- a runtime plugin system;
- dynamic runtime switching or cross-runtime handoff inside one campaign;
- tracked raw telemetry, remote telemetry infrastructure, or dashboards;
- inferred or time-proportional phase token estimates, token-price/currency accounting, or phase token attribution for non-ordinary sessions; existing campaign token-budget accounting remains unchanged;
- automatic recovery of every source read from backend tool streams;
- observability for non-ordinary phases before v1 is evaluated;
- per-step worktrees.

Any of these may be proposed later with evidence and an activation condition.

## Alternatives considered

### Implement the complete Controller roadmap now

Rejected. It adds many state owners and failure modes before their benefits are measured.

### Make no architectural changes until a production incident

Rejected. The existing three-backend runtime seam is already demonstrated, and characterization is cheaper before a high-cost incident.

### Refactor only because `optimize.py` is large

Rejected. File size is a signal. Extraction should follow distinct change reasons and tested seams, not target line count.

### Let every issue produce an ad hoc patch

Rejected. Problem-driven development still uses stable responsibility vocabulary, characterization, explicit ownership, and stop conditions. It is not patch-driven development.

### Use LoopX as the controller immediately

Rejected. LoopX may govern a coarse Atrex campaign later, but it does not provide evidence that Atrex's inner loop needs a new authority model now.

## Risks and mitigations

| Risk | Mitigation |
| --- | --- |
| Evidence collection becomes a new broad observability project | Collect only metrics tied to an imminent decision; record unknowns |
| AgentRuntime extraction becomes a speculative plugin framework | Implement only the four current adapters and one minimal interface |
| Shadow checks silently become authority | Keep outputs diagnostic and prohibit Git/memory/stall writes |
| Problem-driven work degenerates into local patches | Require owner, reproduction, success metric, rollback, and stop condition |
| Severe but rare failures are ignored by frequency metrics | Treat one high-severity false accept or unrecoverable corruption as sufficient evidence |
| Harness work continues by inertia | Enforce the decision gate after every mechanism and allow “stop” as the expected outcome |
| Agent-quality work is displaced | Track optimization-efficiency metrics and compare opportunity cost |

## Open questions

These questions are intentionally deferred until evidence exists:

- Is false acceptance a meaningful current problem?
- Which accepted-result facts are missing from compact artifacts?
- Does independent rerun cost less than manual recovery?
- Is one repair turn worthwhile, and should it reuse or replace the original session?
- Does token-budget continuity matter in deployed campaign usage?
- Which duplicated campaign path has produced the first real behavior drift?
- Does any external caller currently need a stable status projection?

## Final decision

Adopt problem-driven, evidence-gated harness evolution. Implement runtime characterization, immutable campaign-level runtime binding, the minimal AgentRuntime extraction, and local-only ordinary-iteration observability first. Treat every deeper control-plane mechanism as deferred until a concrete problem, measurable success condition, and rollback plan justify it.
