from __future__ import annotations

import unittest
from pathlib import Path

from orchestrator.agent_runtime import (
    ClaudeAdapter,
    CodexAdapter,
    PiAdapter,
    QoderAdapter,
    TokenUsage,
    subtract_token_usage,
    token_usage_exceeds,
    token_usage_from_mapping,
)


FIXTURES = Path(__file__).parent / "fixtures" / "agent_runtime"


class TokenUsageNormalizationTest(unittest.TestCase):
    def test_missing_components_remain_unknown_instead_of_becoming_zero(self) -> None:
        usage = token_usage_from_mapping(
            {"input_tokens": 7, "output_tokens": 2}
        )

        self.assertEqual(
            usage,
            TokenUsage(
                input_tokens=7,
                output_tokens=2,
                cache_read_tokens=None,
                cache_write_tokens=None,
                total_tokens=9,
                measurement="exact",
            ),
        )

    def test_component_overflow_is_not_hidden_by_a_larger_total(self) -> None:
        observed = TokenUsage(12, 0, None, None, 12, "exact")
        terminal = TokenUsage(10, 5, None, None, 15, "exact")

        self.assertTrue(token_usage_exceeds(observed, terminal))
        with self.assertRaisesRegex(ValueError, "exceeds total"):
            subtract_token_usage(terminal, observed)

    def test_cache_creation_is_normalized_as_cache_write(self) -> None:
        usage = token_usage_from_mapping(
            {
                "inputTokens": 1,
                "outputTokens": 2,
                "cacheReadInputTokens": 3,
                "cacheCreationInputTokens": 4,
            }
        )

        self.assertEqual(usage.cache_read_tokens, 3)
        self.assertEqual(usage.cache_write_tokens, 4)
        self.assertEqual(usage.total_tokens, 10)


class BackendFixtureTest(unittest.TestCase):
    def test_claude_fixture_yields_deltas_receipts_and_terminal_usage(self) -> None:
        events, terminal = ClaudeAdapter(Path("missing")).normalize_stream(
            (FIXTURES / "claude_usage.jsonl").read_text()
        )

        self.assertEqual(
            [event.kind for event in events],
            [
                "usage_delta",
                "phase_marker",
                "usage_delta",
                "phase_marker",
                "terminal_usage",
            ],
        )
        self.assertEqual(events[1].phase, "research")
        self.assertEqual(events[1].action, "start")
        self.assertEqual(terminal.total_tokens, 214)

    def test_qoder_fixture_supports_camel_case_usage(self) -> None:
        events, terminal = QoderAdapter(Path("missing")).normalize_stream(
            (FIXTURES / "qoder_usage.jsonl").read_text()
        )

        self.assertEqual([event.kind for event in events].count("usage_delta"), 2)
        self.assertEqual([event.kind for event in events].count("phase_marker"), 2)
        self.assertEqual(terminal.total_tokens, 15)

    def test_pi_fixture_yields_deltas_receipts_and_derived_terminal_usage(self) -> None:
        adapter = PiAdapter(Path("missing"))
        events, terminal = adapter.normalize_stream(
            (FIXTURES / "pi_usage.jsonl").read_text()
        )

        self.assertEqual(
            [event.kind for event in events],
            [
                "usage_delta",
                "phase_marker",
                "usage_delta",
                "phase_marker",
                "usage_delta",
                "terminal_usage",
            ],
        )
        self.assertEqual(events[1].phase, "research")
        self.assertEqual(terminal.input_tokens, 12)
        self.assertEqual(terminal.output_tokens, 10)
        self.assertEqual(terminal.cache_read_tokens, 204)
        self.assertEqual(terminal.cache_write_tokens, 100)
        self.assertEqual(terminal.total_tokens, 326)

    def test_codex_fixture_exposes_terminal_only_capability(self) -> None:
        adapter = CodexAdapter(Path("missing"))
        events, terminal = adapter.normalize_stream(
            (FIXTURES / "codex_usage.jsonl").read_text()
        )

        self.assertFalse(adapter.capabilities.usage_delta)
        self.assertEqual(
            [event.kind for event in events],
            ["phase_marker", "phase_marker", "terminal_usage"],
        )
        self.assertEqual(terminal.cache_read_tokens, 8)
        self.assertEqual(terminal.total_tokens, 25)

    def test_marker_text_in_an_assistant_response_is_not_a_receipt(self) -> None:
        stdout = (
            '{"type":"assistant","message":{"content":[{"type":"text",'
            '"text":"ATREX_TRACE_EVENT={\\"kind\\":\\"phase_marker\\"}"}],'
            '"usage":{"input_tokens":1,"output_tokens":1}}}'
        )

        events, _ = ClaudeAdapter(Path("missing")).normalize_stream(stdout)

        self.assertEqual([event.kind for event in events], ["usage_delta"])


if __name__ == "__main__":
    unittest.main()
