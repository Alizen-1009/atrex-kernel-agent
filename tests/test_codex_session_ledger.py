from __future__ import annotations

import json
import tempfile
import unittest
from pathlib import Path
from unittest import mock

from orchestrator.agent_runtime.codex_ledger import (
    CodexLedgerError,
    CodexSessionLedgerObserver,
    CodexTemporaryHome,
    codex_thread_id_from_stream,
    observe_codex_usage,
)
from orchestrator.agent_runtime.model import AgentRuntimeCapabilities, TokenUsage
from orchestrator.telemetry.phase_tokens import summarize_phase_tokens


THREAD_ID = "019fd5dd-84f3-7471-8934-eb19657dfd56"


def token_count(last_total: int, cumulative_total: int) -> dict:
    return {
        "timestamp": "2026-08-06T00:00:00Z",
        "type": "event_msg",
        "payload": {
            "type": "token_count",
            "info": {
                "last_token_usage": {
                    "input_tokens": last_total,
                    "output_tokens": 0,
                    "cached_input_tokens": 0,
                    "cache_write_input_tokens": 0,
                    "total_tokens": last_total,
                },
                "total_token_usage": {
                    "input_tokens": cumulative_total,
                    "output_tokens": 0,
                    "cached_input_tokens": 0,
                    "cache_write_input_tokens": 0,
                    "total_tokens": cumulative_total,
                },
            },
        },
    }


def marker(action: str, phase: str, marker_id: str) -> dict:
    receipt = {
        "schema": "atrex.iteration_trace.v1",
        "kind": "phase_marker",
        "action": action,
        "phase": phase,
        "marker_id": marker_id,
    }
    return {
        "timestamp": "2026-08-06T00:00:00Z",
        "type": "response_item",
        "payload": {
            "type": "custom_tool_call_output",
            "output": [
                {
                    "type": "input_text",
                    "text": "ATREX_TRACE_EVENT=" + json.dumps(receipt),
                }
            ],
        },
    }


class CodexSessionLedgerTests(unittest.TestCase):
    def _ledger(self, home: Path, lines: list[dict]) -> Path:
        directory = home / "sessions" / "2026" / "08" / "06"
        directory.mkdir(parents=True)
        path = directory / f"rollout-test-{THREAD_ID}.jsonl"
        path.write_text("".join(json.dumps(value) + "\n" for value in lines))
        return path

    def test_thread_id_is_read_from_codex_stdout(self) -> None:
        stdout = "\n".join(
            [
                json.dumps({"type": "thread.started", "thread_id": THREAD_ID}),
                json.dumps({"type": "turn.completed", "usage": {"input_tokens": 8, "output_tokens": 2}}),
            ]
        )

        self.assertEqual(codex_thread_id_from_stream(stdout), THREAD_ID)

    def test_observer_recovers_usage_deltas_before_marker_receipts(self) -> None:
        with tempfile.TemporaryDirectory() as temp:
            home = Path(temp)
            self._ledger(
                home,
                [
                    marker("start", "research", "start-1"),
                    token_count(10, 10),
                    token_count(20, 30),
                    marker("end", "research", "end-1"),
                    token_count(5, 35),
                ],
            )
            observer = CodexSessionLedgerObserver(home)

            observed = observer.observe(THREAD_ID)

            self.assertEqual(
                [event.kind for event in observed.events],
                ["usage_delta", "phase_marker", "usage_delta", "usage_delta", "phase_marker", "terminal_usage"],
            )
            self.assertEqual(observed.events[0].usage.total_tokens, 10)
            self.assertEqual(observed.events[1].phase, "research")
            self.assertEqual(observed.terminal_usage.total_tokens, 35)
            self.assertEqual(observed.session_usage.total_tokens, 35)
            summary = summarize_phase_tokens(
                events=observed.events,
                terminal_usage=observed.terminal_usage,
                capabilities=AgentRuntimeCapabilities(True, True, True, True),
                observation_errors=(),
            )
            self.assertEqual(
                summary["phases"]["research"]["usage"]["total_tokens"], 35
            )
            self.assertEqual(summary["accounted_coverage"], 1.0)
            self.assertEqual(summary["unattributed"]["total_tokens"], 0)
            self.assertEqual(summary["reconciliation_status"], "reconciled")

    def test_resume_observes_only_appended_usage_and_reconciles_cumulative_total(self) -> None:
        with tempfile.TemporaryDirectory() as temp:
            home = Path(temp)
            path = self._ledger(home, [token_count(10, 10)])
            observer = CodexSessionLedgerObserver(home)
            first = observer.observe(THREAD_ID)
            with path.open("a", encoding="utf-8") as handle:
                handle.write(json.dumps(token_count(7, 17)) + "\n")

            second = observer.observe(THREAD_ID)

            self.assertEqual(first.terminal_usage.total_tokens, 10)
            self.assertEqual(second.terminal_usage.total_tokens, 7)
            self.assertEqual(second.session_usage.total_tokens, 17)
            self.assertEqual(
                [event.usage.total_tokens for event in second.events if event.kind == "usage_delta"],
                [7],
            )

    def test_component_mismatch_rejects_otherwise_matching_total(self) -> None:
        with tempfile.TemporaryDirectory() as temp:
            home = Path(temp)
            record = token_count(10, 10)
            record["payload"]["info"]["last_token_usage"]["cache_write_input_tokens"] = 1
            record["payload"]["info"]["total_token_usage"]["cache_write_input_tokens"] = 2
            self._ledger(home, [record])

            with self.assertRaisesRegex(CodexLedgerError, "component"):
                CodexSessionLedgerObserver(home).observe(THREAD_ID)

    def test_unidentified_rollout_is_recovered_by_exact_workspace(self) -> None:
        with tempfile.TemporaryDirectory() as temp:
            home = Path(temp) / "home"
            workspace = Path(temp) / "workspace"
            workspace.mkdir()
            observer = CodexSessionLedgerObserver(home)
            path = self._ledger(
                home,
                [
                    {
                        "type": "session_meta",
                        "payload": {"id": THREAD_ID, "cwd": str(workspace)},
                    },
                    token_count(10, 10),
                ],
            )

            recovered = observer.identify_new_thread(workspace)
            observed = observer.observe(recovered)

            self.assertEqual(recovered, THREAD_ID)
            self.assertEqual(observed.terminal_usage.total_tokens, 10)
            self.assertTrue(path.exists())

    def test_failed_terminal_reconciliation_does_not_consume_ledger_offset(self) -> None:
        with tempfile.TemporaryDirectory() as temp:
            home = Path(temp)
            path = self._ledger(home, [token_count(10, 10)])
            observer = CodexSessionLedgerObserver(home)

            with self.assertRaisesRegex(CodexLedgerError, "stdout terminal"):
                observer.observe_reconciled(
                    THREAD_ID, TokenUsage.unavailable()
                )
            with path.open("a", encoding="utf-8") as handle:
                handle.write(json.dumps(token_count(7, 17)) + "\n")

            recovered = observer.observe_reconciled(
                THREAD_ID,
                TokenUsage(17, 0, 0, 0, 17, "exact"),
            )

            self.assertEqual(recovered.terminal_usage.total_tokens, 17)
            self.assertEqual(
                [event.usage.total_tokens for event in recovered.events if event.kind == "usage_delta"],
                [10, 7],
            )

    def test_missing_stdout_terminal_keeps_partial_ledger_usage(self) -> None:
        with tempfile.TemporaryDirectory() as temp:
            home = Path(temp)
            self._ledger(home, [token_count(10, 10)])
            observer = CodexSessionLedgerObserver(home)

            events, terminal, capabilities, errors = observe_codex_usage(
                observer, THREAD_ID, TokenUsage.unavailable()
            )

            self.assertEqual(terminal.total_tokens, 10)
            self.assertEqual(terminal.measurement, "partial")
            self.assertTrue(capabilities.usage_delta_observed)
            self.assertTrue(
                all(
                    event.usage.measurement == "partial"
                    for event in events
                    if event.usage is not None
                )
            )
            self.assertEqual(errors, ("codex_stdout_terminal_unavailable",))

    def test_missing_component_fails_closed(self) -> None:
        with tempfile.TemporaryDirectory() as temp:
            home = Path(temp)
            record = token_count(10, 10)
            del record["payload"]["info"]["last_token_usage"]["cache_write_input_tokens"]
            self._ledger(home, [record])

            with self.assertRaisesRegex(CodexLedgerError, "missing usage"):
                CodexSessionLedgerObserver(home).observe(THREAD_ID)

    def test_temporary_home_removes_all_isolated_rollout_state(self) -> None:
        with tempfile.TemporaryDirectory() as temp:
            source = Path(temp) / "source"
            source.mkdir()
            (source / "auth.json").write_text("{}\n")
            (source / "config.toml").write_text("model = 'test'\n")
            (source / "skills").mkdir()
            temporary = CodexTemporaryHome(source)
            isolated = temporary.open()
            self.assertTrue((isolated / "auth.json").is_symlink())
            session = isolated / "sessions/2026/08/06/rollout-raw.jsonl"
            session.parent.mkdir(parents=True)
            session.write_text("raw\n")

            error = temporary.close()

            self.assertIsNone(error)
            self.assertFalse(isolated.exists())
            self.assertTrue((source / "auth.json").exists())

    def test_temporary_home_cleanup_failure_is_reported_without_raising(self) -> None:
        with tempfile.TemporaryDirectory() as temp:
            source = Path(temp) / "source"
            source.mkdir()
            temporary = CodexTemporaryHome(source)
            temporary.open()

            with mock.patch.object(
                temporary._temporary,
                "cleanup",
                side_effect=PermissionError("denied"),
            ):
                error = temporary.close()
            temporary.close()

            self.assertEqual(
                error, "codex_temporary_home_cleanup_failed:PermissionError"
            )


if __name__ == "__main__":
    unittest.main()
