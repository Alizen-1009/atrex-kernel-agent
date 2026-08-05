from __future__ import annotations

import json
import subprocess
import tempfile
import unittest
from dataclasses import replace
from pathlib import Path

from orchestrator import optimize
from orchestrator.agent_runtime import (
    AgentRuntimeCapabilities,
    NormalizedAgentEvent,
    PiAdapter,
    TokenUsage,
)
from orchestrator.telemetry import (
    IterationTelemetryRecorder,
    aggregate_attempt_tokens,
    changed_paths_since,
    observed_outcome,
    summarize_phase_tokens,
)
from orchestrator.telemetry import iteration as telemetry_iteration


class SequenceClock:
    def __init__(self, *values: float) -> None:
        self._values = iter(values)

    def __call__(self) -> float:
        return next(self._values)


def usage(total: int) -> TokenUsage:
    return TokenUsage(
        input_tokens=total - 1,
        output_tokens=1,
        cache_read_tokens=None,
        cache_write_tokens=None,
        total_tokens=total,
        measurement="exact",
    )


DELTA_CAPABILITIES = AgentRuntimeCapabilities(
    terminal_usage=True,
    usage_delta=True,
    phase_marker_receipt=True,
    usage_delta_observed=True,
)


class PhaseTokenSummaryTest(unittest.TestCase):
    def test_pi_usage_and_receipts_feed_the_common_phase_summary(self) -> None:
        fixture = (
            Path(__file__).parent
            / "fixtures"
            / "agent_runtime"
            / "pi_usage.jsonl"
        )
        adapter = PiAdapter(Path("missing"))
        events, terminal = adapter.normalize_stream(fixture.read_text())

        summary = summarize_phase_tokens(
            events=events,
            terminal_usage=terminal,
            capabilities=replace(
                adapter.capabilities, usage_delta_observed=True
            ),
            observation_errors=(),
        )

        self.assertEqual(
            summary["phases"]["research"]["usage"]["total_tokens"], 214
        )
        self.assertEqual(summary["terminal_usage"]["total_tokens"], 326)
        self.assertEqual(summary["orchestration"]["total_tokens"], 112)
        self.assertEqual(summary["unattributed"]["total_tokens"], 0)
        self.assertEqual(summary["accounted_coverage"], 1.0)
        self.assertEqual(summary["reconciliation_status"], "reconciled")

    def test_complete_non_overlapping_intervals_are_attributed_and_reconciled(self) -> None:
        events = (
            NormalizedAgentEvent(0, "usage_delta", usage=usage(2)),
            NormalizedAgentEvent(1, "phase_marker", phase="research", action="start", marker_id="m1"),
            NormalizedAgentEvent(2, "usage_delta", usage=usage(10)),
            NormalizedAgentEvent(3, "phase_marker", phase="research", action="end", marker_id="m2"),
            NormalizedAgentEvent(4, "phase_marker", phase="implementation", action="start", marker_id="m3"),
            NormalizedAgentEvent(5, "usage_delta", usage=usage(20)),
            NormalizedAgentEvent(6, "phase_marker", phase="implementation", action="end", marker_id="m4"),
        )

        summary = summarize_phase_tokens(
            events=events,
            terminal_usage=TokenUsage(37, 3, None, None, 40, "exact"),
            capabilities=DELTA_CAPABILITIES,
            observation_errors=(),
        )

        self.assertEqual(summary["phases"]["research"]["usage"]["total_tokens"], 12)
        self.assertEqual(summary["phases"]["implementation"]["usage"]["total_tokens"], 20)
        self.assertEqual(summary["phases"]["planning"]["measurement"], "unavailable")
        self.assertEqual(summary["orchestration"]["total_tokens"], 8)
        self.assertEqual(summary["unattributed"]["total_tokens"], 0)
        self.assertEqual(summary["coverage"], 0.8)
        self.assertEqual(summary["accounted_coverage"], 1.0)
        self.assertEqual(summary["reconciliation_status"], "reconciled")

    def test_repeated_phase_intervals_are_summed(self) -> None:
        events = (
            NormalizedAgentEvent(0, "phase_marker", phase="research", action="start", marker_id="m1"),
            NormalizedAgentEvent(1, "usage_delta", usage=usage(3)),
            NormalizedAgentEvent(2, "phase_marker", phase="research", action="end", marker_id="m2"),
            NormalizedAgentEvent(3, "phase_marker", phase="research", action="start", marker_id="m3"),
            NormalizedAgentEvent(4, "usage_delta", usage=usage(4)),
            NormalizedAgentEvent(5, "phase_marker", phase="research", action="end", marker_id="m4"),
        )

        summary = summarize_phase_tokens(
            events=events,
            terminal_usage=TokenUsage(5, 2, None, None, 7, "exact"),
            capabilities=DELTA_CAPABILITIES,
            observation_errors=(),
        )

        self.assertEqual(summary["phases"]["research"]["interval_count"], 2)
        self.assertEqual(summary["phases"]["research"]["usage"]["total_tokens"], 7)
        self.assertEqual(summary["coverage"], 1.0)

    def test_overlapping_and_unclosed_intervals_fail_closed(self) -> None:
        events = (
            NormalizedAgentEvent(0, "phase_marker", phase="research", action="start", marker_id="m1"),
            NormalizedAgentEvent(1, "usage_delta", usage=usage(3)),
            NormalizedAgentEvent(2, "phase_marker", phase="implementation", action="start", marker_id="m2"),
            NormalizedAgentEvent(3, "usage_delta", usage=usage(4)),
            NormalizedAgentEvent(4, "phase_marker", phase="implementation", action="end", marker_id="m3"),
            NormalizedAgentEvent(5, "phase_marker", phase="research", action="end", marker_id="m4"),
            NormalizedAgentEvent(6, "phase_marker", phase="benchmark", action="start", marker_id="m5"),
            NormalizedAgentEvent(7, "usage_delta", usage=usage(5)),
        )

        summary = summarize_phase_tokens(
            events=events,
            terminal_usage=TokenUsage(9, 3, None, None, 12, "exact"),
            capabilities=DELTA_CAPABILITIES,
            observation_errors=(),
        )

        self.assertIsNone(summary["phases"]["research"]["usage"])
        self.assertIsNone(summary["phases"]["benchmark"]["usage"])
        self.assertEqual(summary["orchestration"]["total_tokens"], 12)
        self.assertEqual(summary["unattributed"]["total_tokens"], 0)
        self.assertEqual(summary["accounted_coverage"], 1.0)
        self.assertIn("overlapping_phase", summary["reason_codes"])
        self.assertIn("unclosed_phase", summary["reason_codes"])

    def test_component_delta_above_terminal_is_inconsistent(self) -> None:
        events = (
            NormalizedAgentEvent(0, "phase_marker", phase="research", action="start", marker_id="m1"),
            NormalizedAgentEvent(
                1,
                "usage_delta",
                usage=TokenUsage(12, 0, None, None, 12, "exact"),
            ),
            NormalizedAgentEvent(2, "phase_marker", phase="research", action="end", marker_id="m2"),
        )

        summary = summarize_phase_tokens(
            events=events,
            terminal_usage=TokenUsage(10, 5, None, None, 15, "exact"),
            capabilities=DELTA_CAPABILITIES,
            observation_errors=(),
        )

        self.assertEqual(summary["reconciliation_status"], "inconsistent")
        self.assertIsNone(summary["unattributed"])
        self.assertIn("usage_delta_exceeds_terminal", summary["reason_codes"])

    def test_delta_total_above_terminal_is_reported_without_correction(self) -> None:
        events = (
            NormalizedAgentEvent(0, "usage_delta", usage=usage(2)),
            NormalizedAgentEvent(1, "phase_marker", phase="research", action="start", marker_id="m1"),
            NormalizedAgentEvent(2, "usage_delta", usage=usage(9)),
            NormalizedAgentEvent(3, "phase_marker", phase="research", action="end", marker_id="m2"),
        )

        summary = summarize_phase_tokens(
            events=events,
            terminal_usage=usage(10),
            capabilities=DELTA_CAPABILITIES,
            observation_errors=(),
        )

        self.assertEqual(summary["reconciliation_status"], "inconsistent")
        self.assertIsNone(summary["unattributed"])
        self.assertIsNone(summary["coverage"])
        self.assertIn("usage_delta_exceeds_terminal", summary["reason_codes"])

    def test_missing_attempt_terminal_does_not_distort_observed_coverage(self) -> None:
        missing_terminal = summarize_phase_tokens(
            events=(
                NormalizedAgentEvent(0, "phase_marker", phase="research", action="start", marker_id="m1"),
                NormalizedAgentEvent(1, "usage_delta", usage=usage(4)),
                NormalizedAgentEvent(2, "phase_marker", phase="research", action="end", marker_id="m2"),
            ),
            terminal_usage=TokenUsage.unavailable(),
            capabilities=DELTA_CAPABILITIES,
            observation_errors=(),
        )
        observed_terminal = summarize_phase_tokens(
            events=(
                NormalizedAgentEvent(0, "phase_marker", phase="implementation", action="start", marker_id="m3"),
                NormalizedAgentEvent(1, "usage_delta", usage=usage(10)),
                NormalizedAgentEvent(2, "phase_marker", phase="implementation", action="end", marker_id="m4"),
            ),
            terminal_usage=usage(20),
            capabilities=DELTA_CAPABILITIES,
            observation_errors=(),
        )

        aggregate = aggregate_attempt_tokens(
            [
                {"phase_tokens": missing_terminal},
                {"phase_tokens": observed_terminal},
            ]
        )

        self.assertEqual(aggregate["terminal_usage"]["total_tokens"], 20)
        self.assertEqual(aggregate["attempts_with_terminal_usage"], 1)
        self.assertEqual(aggregate["phases"]["research"]["usage"]["total_tokens"], 4)
        self.assertEqual(aggregate["coverage"], 0.5)
        self.assertEqual(aggregate["orchestration"]["total_tokens"], 10)
        self.assertEqual(aggregate["unattributed"]["total_tokens"], 0)
        self.assertEqual(aggregate["accounted_coverage"], 1.0)
        self.assertIn("attempt_terminal_usage_unavailable", aggregate["reason_codes"])

    def test_supported_but_unobserved_deltas_do_not_become_exact_zero(self) -> None:
        summary = summarize_phase_tokens(
            events=(
                NormalizedAgentEvent(0, "phase_marker", phase="research", action="start", marker_id="m1"),
                NormalizedAgentEvent(1, "phase_marker", phase="research", action="end", marker_id="m2"),
            ),
            terminal_usage=usage(9),
            capabilities=AgentRuntimeCapabilities(True, True, True, False),
            observation_errors=(),
        )

        self.assertIsNone(summary["phases"]["research"]["usage"])
        self.assertEqual(summary["unattributed"]["total_tokens"], 9)
        self.assertEqual(summary["coverage"], 0.0)
        self.assertIn("backend_usage_delta_unobserved", summary["reason_codes"])

    def test_backend_without_delta_capability_attributes_nothing(self) -> None:
        summary = summarize_phase_tokens(
            events=(),
            terminal_usage=usage(9),
            capabilities=AgentRuntimeCapabilities(True, False, True),
            observation_errors=(),
        )

        self.assertEqual(summary["unattributed"]["total_tokens"], 9)
        self.assertEqual(summary["coverage"], 0.0)
        self.assertEqual(summary["measurement"], "unavailable")
        self.assertIn("backend_has_no_usage_delta", summary["reason_codes"])


class IterationTelemetryTest(unittest.TestCase):
    def test_phase_timing_rejects_overlap_and_tracks_orchestration(self) -> None:
        events = [
            {"event": "phase_started", "phase": "research", "monotonic_seconds": 1.0},
            {"event": "phase_started", "phase": "implementation", "monotonic_seconds": 2.0},
            {"event": "phase_completed", "phase": "implementation", "monotonic_seconds": 3.0},
            {"event": "phase_completed", "phase": "research", "monotonic_seconds": 4.0},
        ]

        phases, _, _, timing = telemetry_iteration._summarize_events(events, 10.0)

        self.assertIsNone(phases["research"]["wall_seconds"])
        self.assertIsNone(phases["implementation"]["wall_seconds"])
        self.assertEqual(timing["attributed_seconds"], 0.0)
        self.assertEqual(timing["orchestration_seconds"], 10.0)
        self.assertEqual(timing["unattributed_seconds"], 0.0)
        self.assertEqual(timing["accounted_coverage"], 1.0)
        self.assertIn("overlapping_phase", timing["reason_codes"])

    def _recorder(
        self,
        workspace: Path,
        *,
        attempt_id: str,
        clock: SequenceClock,
    ) -> IterationTelemetryRecorder:
        return IterationTelemetryRecorder(
            workspace=workspace,
            campaign_id="demo",
            version=1,
            runtime_id="claude",
            base_head="",
            base_kernel_blob="",
            monotonic_clock=clock,
            utc_clock=lambda: "2026-08-04T00:00:00+00:00",
            attempt_id=attempt_id,
        )

    def test_recorder_persists_phase_tokens_and_renders_brief_table(self) -> None:
        events = (
            NormalizedAgentEvent(0, "phase_marker", phase="research", action="start", marker_id="m1"),
            NormalizedAgentEvent(1, "usage_delta", usage=usage(10)),
            NormalizedAgentEvent(2, "phase_marker", phase="research", action="end", marker_id="m2"),
            NormalizedAgentEvent(3, "terminal_usage", usage=usage(20)),
        )
        with tempfile.TemporaryDirectory(prefix="phase-token-recorder-") as temp_dir:
            recorder = self._recorder(
                Path(temp_dir),
                attempt_id="attempt-1",
                clock=SequenceClock(1.0, 2.0, 3.0, 4.0),
            )
            recorder.agent_started()
            recorder.agent_completed(
                session_id="session-1",
                exit_status=0,
                timed_out=False,
                terminal_usage=usage(20),
                events=events,
                capabilities=DELTA_CAPABILITIES,
            )
            summary_path = recorder.finalize(
                memory=None,
                post_head="",
                post_kernel_blob="",
                changed_paths=[],
            )
            summary = json.loads(summary_path.read_text())
            attempt = json.loads(recorder.attempt_summary_path.read_text())
            brief = (recorder.directory / "iteration.brief.md").read_text()
            trace = recorder.trace_path.read_text()

        self.assertEqual(
            attempt["phase_tokens"]["phases"]["research"]["usage"]["total_tokens"],
            10,
        )
        self.assertEqual(summary["phase_tokens"]["terminal_usage"]["total_tokens"], 20)
        self.assertEqual(summary["phase_tokens"]["orchestration"]["total_tokens"], 10)
        self.assertEqual(summary["phase_tokens"]["unattributed"]["total_tokens"], 0)
        self.assertEqual(summary["phase_tokens"]["accounted_coverage"], 1.0)
        self.assertIn("## Phase token usage", brief)
        self.assertIn("| research | 9 | 1 |", brief)
        self.assertIn("| orchestration | 10 | 0 |", brief)
        self.assertNotIn("raw parser", trace)

    def test_iteration_summary_aggregates_all_attempt_tokens(self) -> None:
        with tempfile.TemporaryDirectory(prefix="phase-token-attempts-") as temp_dir:
            workspace = Path(temp_dir)
            first = self._recorder(
                workspace,
                attempt_id="attempt-1",
                clock=SequenceClock(1.0, 2.0, 3.0),
            )
            first.agent_started()
            first.agent_completed(
                session_id="session-1",
                exit_status=1,
                timed_out=False,
                terminal_usage=usage(10),
                events=(
                    NormalizedAgentEvent(0, "phase_marker", phase="research", action="start", marker_id="m1"),
                    NormalizedAgentEvent(1, "usage_delta", usage=usage(4)),
                    NormalizedAgentEvent(2, "phase_marker", phase="research", action="end", marker_id="m2"),
                ),
                capabilities=DELTA_CAPABILITIES,
            )
            second = self._recorder(
                workspace,
                attempt_id="attempt-2",
                clock=SequenceClock(4.0, 5.0, 6.0, 7.0),
            )
            second.agent_started()
            second.agent_completed(
                session_id="session-2",
                exit_status=0,
                timed_out=False,
                terminal_usage=usage(20),
                events=(
                    NormalizedAgentEvent(0, "phase_marker", phase="implementation", action="start", marker_id="m3"),
                    NormalizedAgentEvent(1, "usage_delta", usage=usage(10)),
                    NormalizedAgentEvent(2, "phase_marker", phase="implementation", action="end", marker_id="m4"),
                ),
                capabilities=DELTA_CAPABILITIES,
            )
            summary_path = second.finalize(
                memory=None,
                post_head="",
                post_kernel_blob="",
                changed_paths=[],
            )
            summary = json.loads(summary_path.read_text())

        tokens = summary["phase_tokens"]
        self.assertEqual(tokens["attempt_count"], 2)
        self.assertEqual(tokens["attempts_with_terminal_usage"], 2)
        self.assertEqual(tokens["terminal_usage"]["total_tokens"], 30)
        self.assertEqual(tokens["phases"]["research"]["usage"]["total_tokens"], 4)
        self.assertEqual(tokens["phases"]["implementation"]["usage"]["total_tokens"], 10)
        self.assertEqual(tokens["orchestration"]["input_tokens"], 16)
        self.assertEqual(tokens["orchestration"]["output_tokens"], 0)
        self.assertEqual(tokens["orchestration"]["total_tokens"], 16)
        self.assertEqual(tokens["unattributed"]["total_tokens"], 0)
        self.assertEqual(tokens["accounted_coverage"], 1.0)

    def test_campaign_helpers_record_one_local_attempt(self) -> None:
        with tempfile.TemporaryDirectory(prefix="campaign-telemetry-") as temp_dir:
            campaign = optimize.Campaign(
                name="demo",
                kernel_demo="/tmp/reference.py",
                platform="H20",
                framework="Triton",
                work_dir=temp_dir,
                agent_cli="codex",
            )
            workspace = campaign.workspace
            (workspace / "memory").mkdir(parents=True)
            (workspace / "kernel.py").write_text("# incumbent\n", encoding="utf-8")
            (workspace / "memory" / "v1.json").write_text(
                json.dumps(
                    {
                        "quality_gate": {"result": "FAIL"},
                        "correctness": {"status": "PASS"},
                    }
                ),
                encoding="utf-8",
            )
            subprocess.run(["git", "init", "-q"], cwd=workspace, check=True)
            subprocess.run(
                ["git", "config", "user.email", "test@example.com"],
                cwd=workspace, check=True,
            )
            subprocess.run(
                ["git", "config", "user.name", "Test"], cwd=workspace, check=True,
            )
            subprocess.run(["git", "add", "-A"], cwd=workspace, check=True)
            subprocess.run(
                ["git", "commit", "-q", "-m", "baseline"], cwd=workspace, check=True,
            )
            head = optimize.git_head(workspace)
            recorder = campaign._begin_iteration_telemetry(1, head)
            self.assertIsNotNone(recorder)
            assert recorder is not None
            recorder.agent_completed(
                session_id="session-1", exit_status=0, timed_out=False
            )
            campaign._finish_iteration_telemetry(
                recorder, 1, optimize.read_memory(workspace, 1)
            )
            summary = json.loads(
                (
                    workspace
                    / ".atrex"
                    / "telemetry"
                    / "v1"
                    / "iteration.summary.json"
                ).read_text(encoding="utf-8")
            )

        self.assertEqual(summary["runtime"]["runtime_id"], "codex")
        self.assertNotIn("tokens", summary)
        self.assertEqual(summary["observed_outcome"], "performance_rejection")

    def test_changed_paths_include_committed_and_untracked_files(self) -> None:
        with tempfile.TemporaryDirectory(prefix="telemetry-paths-") as temp_dir:
            workspace = Path(temp_dir)
            (workspace / "kernel.py").write_text("# baseline\n", encoding="utf-8")
            subprocess.run(["git", "init", "-q"], cwd=workspace, check=True)
            subprocess.run(
                ["git", "config", "user.email", "test@example.com"],
                cwd=workspace,
                check=True,
            )
            subprocess.run(
                ["git", "config", "user.name", "Test"], cwd=workspace, check=True
            )
            subprocess.run(["git", "add", "kernel.py"], cwd=workspace, check=True)
            subprocess.run(
                ["git", "commit", "-q", "-m", "baseline"], cwd=workspace, check=True
            )
            head = subprocess.run(
                ["git", "rev-parse", "HEAD"], cwd=workspace, check=True,
                capture_output=True, text=True,
            ).stdout.strip()
            (workspace / "kernel.py").write_text("# candidate\n", encoding="utf-8")
            subprocess.run(["git", "add", "kernel.py"], cwd=workspace, check=True)
            subprocess.run(
                ["git", "commit", "-q", "-m", "candidate"], cwd=workspace, check=True
            )
            (workspace / "plans").mkdir()
            (workspace / "plans" / "v1_plan.md").write_text("plan\n", encoding="utf-8")

            changed = changed_paths_since(workspace, head)

        self.assertEqual(changed, ["kernel.py", "plans/v1_plan.md"])

    def test_observed_outcome_is_read_only_and_explicit_about_disagreement(self) -> None:
        self.assertEqual(
            observed_outcome(
                exit_status=0,
                timed_out=False,
                memory={"quality_gate": {"result": "PASS"}, "correctness": {"status": "PASS"}},
                kernel_changed=True,
            ),
            ("accepted", []),
        )
        self.assertEqual(
            observed_outcome(
                exit_status=0,
                timed_out=False,
                memory={"quality_gate": {"result": "FAIL"}, "correctness": {"status": "PASS"}},
                kernel_changed=False,
            ),
            ("performance_rejection", []),
        )
        self.assertEqual(
            observed_outcome(
                exit_status=0,
                timed_out=False,
                memory={"quality_gate": {"result": "FAIL"}, "correctness": {"status": "FAIL"}},
                kernel_changed=False,
            ),
            ("validation_failure", []),
        )
        self.assertEqual(
            observed_outcome(
                exit_status=0,
                timed_out=False,
                memory={"quality_gate": {"result": "PASS"}},
                kernel_changed=False,
            ),
            ("unknown", ["memory_git_disagreement"]),
        )
        self.assertEqual(
            observed_outcome(
                exit_status=1,
                timed_out=False,
                memory=None,
                kernel_changed=False,
            ),
            ("runtime_failure", []),
        )
        self.assertEqual(
            observed_outcome(
                exit_status=1,
                timed_out=False,
                memory={
                    "quality_gate": {"result": "FAIL"},
                    "correctness": {"status": "FAIL"},
                },
                kernel_changed=False,
            ),
            ("runtime_failure", []),
        )

    def test_recorder_writes_ignored_local_trace_and_summary(self) -> None:
        with tempfile.TemporaryDirectory(prefix="iteration-telemetry-") as temp_dir:
            workspace = Path(temp_dir)
            (workspace / "memory").mkdir()
            (workspace / "kernel.py").write_text("# incumbent\n", encoding="utf-8")
            (workspace / "memory" / "v1.json").write_text(
                json.dumps(
                    {
                        "quality_gate": {"result": "FAIL"},
                        "correctness": {"status": "PASS"},
                        "performance": {"latency_us": 12.5},
                    }
                ),
                encoding="utf-8",
            )
            subprocess.run(["git", "init", "-q"], cwd=workspace, check=True)
            subprocess.run(
                ["git", "config", "user.email", "test@example.com"],
                cwd=workspace,
                check=True,
            )
            subprocess.run(
                ["git", "config", "user.name", "Test"], cwd=workspace, check=True
            )
            subprocess.run(["git", "add", "-A"], cwd=workspace, check=True)
            subprocess.run(
                ["git", "commit", "-q", "-m", "baseline"], cwd=workspace, check=True
            )
            head = subprocess.run(
                ["git", "rev-parse", "HEAD"],
                cwd=workspace,
                check=True,
                capture_output=True,
                text=True,
            ).stdout.strip()

            recorder = IterationTelemetryRecorder(
                workspace=workspace,
                campaign_id="demo-triton",
                version=1,
                runtime_id="codex",
                base_head=head,
                base_kernel_blob="blob-before",
                monotonic_clock=SequenceClock(10.0, 12.0, 22.0, 25.0),
                utc_clock=lambda: "2026-08-04T00:00:00+00:00",
                attempt_id="attempt-1",
            )
            recorder.agent_started()
            recorder._append_event(
                "phase_started",
                measurement="explicit",
                fields={"phase": "research"},
                monotonic_seconds=13.0,
            )
            recorder._append_event(
                "source_read",
                measurement="explicit",
                fields={
                    "source_kind": "gpu_wiki",
                    "reference": "docs/kernel-opt/example.md",
                },
                monotonic_seconds=14.0,
            )
            recorder._append_event(
                "phase_completed",
                measurement="explicit",
                fields={"phase": "research"},
                monotonic_seconds=15.0,
            )
            recorder._append_event(
                "sandbox_operation_completed",
                measurement="exact",
                fields={
                    "operation_id": "sandbox-1",
                    "category": "profile",
                    "duration_seconds": 3.0,
                    "status": "succeeded",
                },
                monotonic_seconds=16.0,
            )
            recorder.agent_completed(
                session_id="session-1",
                exit_status=0,
                timed_out=False,
            )
            summary_path = recorder.finalize(
                memory=json.loads(
                    (workspace / "memory" / "v1.json").read_text(encoding="utf-8")
                ),
                post_head=head,
                post_kernel_blob="blob-before",
                changed_paths=[],
            )

            summary = json.loads(summary_path.read_text(encoding="utf-8"))
            attempt_summary = json.loads(
                (
                    workspace
                    / ".atrex"
                    / "telemetry"
                    / "v1"
                    / "attempt-attempt-1.summary.json"
                ).read_text(encoding="utf-8")
            )
            trace_path = (
                workspace
                / ".atrex"
                / "telemetry"
                / "v1"
                / "attempt-attempt-1.jsonl"
            )
            brief = (
                workspace / ".atrex" / "telemetry" / "v1" / "iteration.brief.md"
            ).read_text(encoding="utf-8")
            events = [json.loads(line) for line in trace_path.read_text().splitlines()]
            status = subprocess.run(
                ["git", "status", "--porcelain"],
                cwd=workspace,
                check=True,
                capture_output=True,
                text=True,
            ).stdout

        self.assertEqual(summary["observed_outcome"], "performance_rejection")
        self.assertEqual(summary["total_wall_seconds"], 15.0)
        self.assertEqual(summary["agent_wall_seconds"], 10.0)
        self.assertNotIn("tokens", summary)
        self.assertEqual(summary["phases"]["research"]["measurement"], "explicit")
        self.assertEqual(summary["phases"]["research"]["wall_seconds"], 2.0)
        self.assertEqual(summary["phase_timing"]["attributed_seconds"], 2.0)
        self.assertEqual(summary["orchestration_wall_seconds"], 13.0)
        self.assertEqual(summary["unattributed_wall_seconds"], 0.0)
        self.assertEqual(summary["phase_timing"]["accounted_coverage"], 1.0)
        self.assertEqual(summary["source_reads"]["coverage"], "explicit")
        self.assertEqual(summary["source_reads"]["unique_count"], 1)
        self.assertEqual(summary["sandbox_operations"]["coverage"], "exact")
        self.assertEqual(summary["sandbox_operations"]["total_seconds"], 3.0)
        self.assertEqual(attempt_summary["attempt_id"], "attempt-1")
        self.assertIn("outcome: `performance_rejection`", brief)
        self.assertIn("research=2.0s", brief)
        self.assertEqual(
            [event["event"] for event in events],
            [
                "iteration_started",
                "agent_session_started",
                "phase_started",
                "source_read",
                "phase_completed",
                "sandbox_operation_completed",
                "agent_session_completed",
                "outcome_observed",
                "iteration_completed",
            ],
        )
        self.assertEqual(status, "")


if __name__ == "__main__":
    unittest.main()
