# Hidden-Teacher Distillation Campaign — Task Checklist

Implementation must follow TDD: add a failing behavior test before each production change.

## Phase 1 — Contracts and isolation artifacts

- [x] **Task 1:** Define Teacher campaign schemas and immutable domain models.
  - Verified: `python3 -m unittest teacher_distill.tests.test_models -v`
- [ ] **Task 2:** Validate and fingerprint self-contained Teacher bundles.
  - Depends on: Task 1
  - Verify: `python -m unittest teacher_distill.tests.test_bundle -v`
- [ ] **Task 3:** Build a physical, content-addressed sanitized gpu-wiki view.
  - Depends on: Tasks 1–2
  - Verify: `python -m unittest teacher_distill.tests.test_knowledge_view -v`

### Checkpoint A

- [ ] Tasks 1–3 tests pass.
- [ ] Valid/invalid bundle fixtures behave fail-closed.
- [ ] Sanitized view is deterministic, queryable, self-contained, and contains no original-wiki symlinks.
- [ ] Review the generated inclusion/exclusion report.

## Phase 2 — Backward-compatible orchestration seams

- [ ] **Task 4:** Add optional `teacher_progress` memory/schema/summary support.
  - Depends on: Task 1
  - Verify: `python -m unittest tests.test_teacher_memory -v`
- [ ] **Task 5:** Add `StopPolicy`/`DefaultStopPolicy` without changing standard campaigns.
  - Depends on: Task 1
  - Verify: `python -m unittest tests.test_stop_policy tests.test_optimize_dispatch tests.test_framework_baseline -v`
- [ ] **Task 6:** Add Teacher-mode CLI validation and lazy dispatch.
  - Depends on: Tasks 1–2, 5
  - Verify: `python -m unittest tests.test_teacher_cli tests.test_optimize_dispatch tests.test_agent_cli -v`

### Checkpoint B

- [ ] Tasks 4–6 tests pass.
- [ ] Existing `tests/` suite is green.
- [ ] Standard CLI defaults and auto-dispatch behavior are unchanged.
- [ ] Unsupported Teacher-mode combinations fail before workspace/GPU/Agent work.

## Phase 3 — Hidden-audited execution and Teacher measurement

- [ ] **Task 7:** Enforce sanitized runtime links, search restrictions, forbidden-access audit, and no-public-web policy.
  - Depends on: Tasks 3, 6
  - Verify: `python -m unittest tests.test_teacher_session_policy tests.test_agent_runtime_characterization tests.test_campaign_runtime_binding -v`
- [ ] **Task 8:** Materialize and validate the private Teacher benchmark workspace.
  - Depends on: Tasks 1–2, 6
  - Verify: `python -m unittest teacher_distill.tests.test_teacher_benchmark -v`
- [ ] **Task 9:** Generalize same-allocation ABBA for private Teacher vs Git Candidate.
  - Depends on: Task 8
  - Verify: `python -m unittest teacher_distill.tests.test_abba long_horizon.tests.test_verifier -v`
- [ ] **Task 10:** Implement `TeacherStopPolicy` and Teacher-progress recording.
  - Depends on: Tasks 4–5, 9
  - Verify: `python -m unittest teacher_distill.tests.test_stop_policy -v`

### Checkpoint C

- [ ] Tasks 7–10 pass with fake sandbox responses.
- [ ] All four Agent backends receive the same Teacher policy environment.
- [ ] Teacher source/path never appears in public workspace or prompts.
- [ ] Mocked provisional PASS → ABBA FAIL → continue → ABBA PASS behavior works.

## Phase 4 — Complete Teacher campaign and bounded exploration

- [ ] **Task 11:** Implement `TeacherDistillCampaign` setup, resume locks, loop wiring, and terminal statuses.
  - Depends on: Tasks 6–10
  - Verify: `python -m unittest teacher_distill.tests.test_campaign -v`
- [ ] **Task 12:** Add one bounded long-horizon episode after stalls and one partial restart.
  - Depends on: Task 11
  - Verify: `python -m unittest teacher_distill.tests.test_escalation long_horizon.tests.test_campaign long_horizon.tests.test_git_episode -v`

### Checkpoint D

- [ ] SUCCESS, PLATEAU, BUDGET_EXHAUSTED, INFRA_ERROR, leakage, and resume mismatch are covered.
- [ ] Candidate HEAD remains monotonically best.
- [ ] Resume restores escalation/restart counters and deterministic next action.
- [ ] Existing long-horizon tests remain green.

## Phase 5 — Evidence-backed distillation

- [ ] **Task 13:** Build deterministic evidence and performance-trajectory manifests.
  - Depends on: Tasks 11–12
  - Verify: `python -m unittest teacher_distill.tests.test_evidence -v`
- [ ] **Task 14:** Generate hypothesis-only Teacher gap analysis and evidence-cited drafts.
  - Depends on: Task 13
  - Verify: `python -m unittest teacher_distill.tests.test_distillation -v`
- [ ] **Task 15:** Validate drafts and prohibit automatic canonical wiki promotion.
  - Depends on: Task 14
  - Verify: `python -m unittest teacher_distill.tests.test_draft_validator -v`

## Phase 6 — Documentation and release gate

- [ ] **Task 16:** Add docs, CLI examples, minimal fixtures, and mocked end-to-end test.
  - Depends on: Tasks 1–15
- [ ] Run: `python -m unittest discover -s tests -v`
- [ ] Run: `python -m unittest discover -s long_horizon/tests -v`
- [ ] Run: `python -m unittest discover -s teacher_distill/tests -v`
- [ ] Run: `python -m unittest gpu-wiki/scripts/test_query.py -v`
- [ ] Run: `python -m unittest gpu-wiki/scripts/test_check_self_contained.py -v`
- [ ] Run: `git diff --check`

### Checkpoint E — Release readiness

- [ ] Standard campaign behavior remains unchanged.
- [ ] Mocked Teacher campaign passes end to end.
- [ ] One internal real-GPU smoke campaign passes the checklist in `tasks/plan.md`.
- [ ] Campaign execution leaves canonical `gpu-wiki/` untouched.
- [ ] Threat-model wording clearly says `hidden-audited`, not security-grade isolation.
- [ ] Human review approves the implementation and generated draft format.
