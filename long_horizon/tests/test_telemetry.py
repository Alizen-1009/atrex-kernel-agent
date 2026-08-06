from __future__ import annotations

import unittest

from long_horizon.models import InvocationObservation
from long_horizon.telemetry import render_episode_brief, summarize_episode
from orchestrator.agent_runtime.model import (
    AgentRuntimeCapabilities,
    NormalizedAgentEvent,
    TokenUsage,
)


CAPABILITIES = AgentRuntimeCapabilities(
    terminal_usage=True,
    usage_delta=True,
    phase_marker_receipt=True,
    usage_delta_observed=True,
)


def usage(total: int) -> TokenUsage:
    return TokenUsage(total, 0, 0, 0, total, "exact")


def marker(sequence: int, phase: str, action: str) -> NormalizedAgentEvent:
    return NormalizedAgentEvent(
        sequence,
        "phase_marker",
        phase=phase,
        action=action,
        marker_id=f"{phase}-{action}-{sequence}",
    )


class EpisodeTelemetryTests(unittest.TestCase):
    def test_single_invocation_reuses_explicit_repeated_phase_accounting(self) -> None:
        observation = InvocationObservation(
            terminal_usage=usage(30),
            events=(
                NormalizedAgentEvent(0, "usage_delta", usage=usage(2)),
                marker(1, "research", "start"),
                NormalizedAgentEvent(2, "usage_delta", usage=usage(8)),
                marker(3, "research", "end"),
                marker(4, "implementation", "start"),
                NormalizedAgentEvent(5, "usage_delta", usage=usage(4)),
                marker(6, "implementation", "end"),
                marker(7, "implementation", "start"),
                NormalizedAgentEvent(8, "usage_delta", usage=usage(6)),
                marker(9, "implementation", "end"),
                NormalizedAgentEvent(10, "usage_delta", usage=usage(10)),
                NormalizedAgentEvent(11, "terminal_usage", usage=usage(30)),
            ),
            capabilities=CAPABILITIES,
        )

        summary = summarize_episode(
            episode=3,
            version=5,
            status="candidate_ready",
            accepted=True,
            control_tokens=30,
            resume_count=0,
            invocations=(observation,),
        )

        tokens = summary["phase_tokens"]
        self.assertEqual(tokens["terminal_usage"]["total_tokens"], 30)
        self.assertEqual(tokens["phases"]["research"]["usage"]["total_tokens"], 10)
        self.assertEqual(
            tokens["phases"]["implementation"]["usage"]["total_tokens"], 10
        )
        implementation = tokens["phases"]["implementation"]
        self.assertEqual(implementation["interval_count"], 2)
        implementation_intervals = summary["phase_intervals"]["implementation"]
        self.assertEqual(
            [value["usage"]["total_tokens"] for value in implementation_intervals],
            [4, 6],
        )
        self.assertEqual(
            [value["invocation"] for value in implementation_intervals],
            [1, 1],
        )
        self.assertEqual(tokens["orchestration"]["total_tokens"], 10)
        self.assertEqual(tokens["unattributed"]["total_tokens"], 0)
        self.assertEqual(tokens["accounted_coverage"], 1.0)
        self.assertEqual(summary["measurement"], "partial")
        self.assertEqual(summary["reason_codes"], [])
        self.assertNotIn("events", summary)

        brief = render_episode_brief(summary)
        self.assertIn("Episode 3", brief)
        self.assertIn("implementation", brief)
        self.assertIn("| implementation | 1 | 2 |", brief)
        self.assertIn("30", brief)

    def test_resumed_episode_is_attempt_first_and_marked_unqualified(self) -> None:
        first = InvocationObservation(
            terminal_usage=usage(10),
            events=(NormalizedAgentEvent(0, "usage_delta", usage=usage(10)),),
            capabilities=CAPABILITIES,
        )
        second = InvocationObservation(
            terminal_usage=usage(20),
            events=(NormalizedAgentEvent(0, "usage_delta", usage=usage(20)),),
            capabilities=CAPABILITIES,
        )

        summary = summarize_episode(
            episode=1,
            version=1,
            status="pivot",
            accepted=False,
            control_tokens=30,
            resume_count=1,
            invocations=(first, second),
        )

        self.assertEqual(summary["invocation_count"], 2)
        self.assertEqual(summary["phase_tokens"]["terminal_usage"]["total_tokens"], 30)
        self.assertEqual(summary["phase_tokens"]["accounted_coverage"], 1.0)
        self.assertEqual(summary["measurement"], "unavailable")
        self.assertIn(
            "same_session_resume_usage_semantics_unqualified",
            summary["reason_codes"],
        )

    def test_terminal_only_backend_keeps_episode_measurement_unavailable(self) -> None:
        terminal_only = AgentRuntimeCapabilities(
            terminal_usage=True,
            usage_delta=False,
            phase_marker_receipt=True,
            usage_delta_observed=False,
        )
        observation = InvocationObservation(
            terminal_usage=usage(500),
            events=(),
            capabilities=terminal_only,
        )

        summary = summarize_episode(
            episode=1,
            version=1,
            status="pivot",
            accepted=False,
            control_tokens=500,
            resume_count=0,
            invocations=(observation,),
        )

        self.assertEqual(summary["measurement"], "unavailable")
        self.assertEqual(summary["phase_tokens"]["measurement"], "unavailable")
        self.assertEqual(summary["phase_tokens"]["unattributed"]["total_tokens"], 500)
        self.assertIn("backend_has_no_usage_delta", summary["reason_codes"])

    def test_unavailable_interval_is_not_exposed_as_phase_detail(self) -> None:
        observation = InvocationObservation(
            terminal_usage=usage(10),
            events=(
                marker(0, "planning", "start"),
                NormalizedAgentEvent(1, "usage_delta", usage=TokenUsage.unavailable()),
                marker(2, "planning", "end"),
            ),
            capabilities=CAPABILITIES,
        )

        summary = summarize_episode(
            episode=1,
            version=1,
            status="pivot",
            accepted=False,
            control_tokens=10,
            resume_count=0,
            invocations=(observation,),
        )

        planning = summary["phase_tokens"]["phases"]["planning"]
        self.assertIsNone(planning["usage"])
        self.assertEqual(planning["interval_count"], 0)
        self.assertEqual(summary["phase_intervals"]["planning"], [])

    def test_missing_structured_observation_preserves_control_total_as_unattributed(self) -> None:
        summary = summarize_episode(
            episode=1,
            version=1,
            status="invalid_handoff",
            accepted=False,
            control_tokens=42,
            resume_count=0,
            invocations=(),
        )

        tokens = summary["phase_tokens"]
        self.assertEqual(tokens["terminal_usage"]["total_tokens"], 42)
        self.assertEqual(tokens["unattributed"]["total_tokens"], 42)
        self.assertEqual(tokens["accounted_coverage"], 0.0)
        self.assertEqual(summary["measurement"], "unavailable")
        self.assertIn("structured_usage_unavailable", summary["reason_codes"])


if __name__ == "__main__":
    unittest.main()
