from __future__ import annotations

import json
import signal
import tempfile
import unittest
from pathlib import Path
from unittest import mock

from long_horizon.protocol import atomic_write_json
from long_horizon.session import LongSessionRunner
from orchestrator.agent_runtime.codex_ledger import (
    CodexLedgerObservation,
    CodexSessionLedgerObserver,
)
from orchestrator.agent_runtime.model import NormalizedAgentEvent, TokenUsage


class SessionRecoveryTests(unittest.TestCase):
    @staticmethod
    def _claude_terminated_event() -> str:
        return json.dumps(
            {
                "model": "<synthetic>",
                "error": "unknown",
                "isApiErrorMessage": True,
                "message": {
                    "content": [{"type": "text", "text": "API Error: terminated"}]
                },
            }
        )

    def test_missing_handoff_resumes_same_session(self) -> None:
        with tempfile.TemporaryDirectory() as temp:
            workspace = Path(temp)
            handoff = workspace / "handoff.json"
            commands: list[list[str]] = []
            attempt_ids: list[str | None] = []

            def execute(command, cwd, timeout, environment):
                commands.append(command)
                attempt_ids.append(environment.get("ATREX_TELEMETRY_ATTEMPT_ID"))
                if len(commands) == 2:
                    atomic_write_json(handoff, {"status": "pivot"})
                return "", "", 0, False

            with mock.patch("long_horizon.main_adapter.session_environment", return_value={}), mock.patch(
                "long_horizon.main_adapter.fresh_session_command",
                side_effect=lambda prompt, sid, effort: ["claude", "--session-id", sid, prompt],
            ), mock.patch(
                "long_horizon.main_adapter.resume_session_command",
                side_effect=lambda prompt, sid, effort: ["claude", "--resume", sid, prompt],
            ):
                result = LongSessionRunner(executor=execute).run(
                    workspace,
                    "work",
                    timeout=60,
                    handoff_path=handoff,
                    handoff_resumes=2,
                    completion_check=lambda value: "",
                    telemetry_environment={
                        "ATREX_TELEMETRY_ATTEMPT_ID": "invocation"
                    },
                )
            self.assertEqual(result.resume_count, 1)
            self.assertEqual(result.handoff.status, "pivot")
            self.assertEqual(commands[0][2], commands[1][2])
            self.assertEqual(commands[1][1], "--resume")
            self.assertEqual(attempt_ids, ["invocation-1", "invocation-2"])

    def test_nonzero_exit_does_not_resume(self) -> None:
        with tempfile.TemporaryDirectory() as temp:
            workspace = Path(temp)
            with mock.patch("long_horizon.main_adapter.session_environment", return_value={}), mock.patch(
                "long_horizon.main_adapter.fresh_session_command", return_value=["claude"]
            ):
                result = LongSessionRunner(
                    executor=lambda command, cwd, timeout, environment: ("", "boom", 2, False)
                ).run(
                    workspace,
                    "work",
                    timeout=60,
                    handoff_path=workspace / "handoff.json",
                    handoff_resumes=2,
                    completion_check=lambda value: "",
                )
            self.assertEqual(result.resume_count, 0)
            self.assertEqual(result.exit_status, 2)

    def test_claude_transient_api_error_resumes_same_session(self) -> None:
        with tempfile.TemporaryDirectory() as temp:
            workspace = Path(temp)
            handoff = workspace / "handoff.json"
            commands: list[list[str]] = []

            def execute(command, cwd, timeout, environment):
                commands.append(command)
                if len(commands) == 1:
                    return self._claude_terminated_event(), "", 1, False
                atomic_write_json(handoff, {"status": "pivot"})
                return "", "", 0, False

            with mock.patch("long_horizon.main_adapter.session_environment", return_value={}), mock.patch(
                "long_horizon.main_adapter.fresh_session_command",
                side_effect=lambda prompt, sid, effort: ["claude", "--session-id", sid, prompt],
            ), mock.patch(
                "long_horizon.main_adapter.resume_session_command",
                side_effect=lambda prompt, sid, effort: ["claude", "--resume", sid, prompt],
            ):
                result = LongSessionRunner(executor=execute).run(
                    workspace,
                    "work",
                    timeout=60,
                    handoff_path=handoff,
                    handoff_resumes=2,
                    completion_check=lambda value: "",
                )

            self.assertEqual(result.exit_status, 0)
            self.assertEqual(result.resume_count, 1)
            self.assertEqual(result.handoff.status, "pivot")
            self.assertEqual(commands[0][2], commands[1][2])
            self.assertEqual(commands[1][1], "--resume")

    def test_claude_shell_style_sigterm_resumes_same_session(self) -> None:
        with tempfile.TemporaryDirectory() as temp:
            workspace = Path(temp)
            handoff = workspace / "handoff.json"
            commands: list[list[str]] = []

            def execute(command, cwd, timeout, environment):
                commands.append(command)
                if len(commands) == 1:
                    return "", "", 128 + signal.SIGTERM, False
                atomic_write_json(handoff, {"status": "pivot"})
                return "", "", 0, False

            with mock.patch(
                "long_horizon.main_adapter.session_environment", return_value={}
            ), mock.patch(
                "long_horizon.main_adapter.fresh_session_command",
                side_effect=lambda prompt, sid, effort: [
                    "claude", "--session-id", sid, prompt
                ],
            ), mock.patch(
                "long_horizon.main_adapter.resume_session_command",
                side_effect=lambda prompt, sid, effort: [
                    "claude", "--resume", sid, prompt
                ],
            ):
                result = LongSessionRunner(executor=execute).run(
                    workspace,
                    "work",
                    timeout=60,
                    handoff_path=handoff,
                    handoff_resumes=2,
                    completion_check=lambda value: "",
                )

            self.assertEqual(result.exit_status, 0)
            self.assertEqual(result.resume_count, 1)
            self.assertEqual(result.handoff.status, "pivot")
            self.assertEqual(commands[0][2], commands[1][2])
            self.assertEqual(commands[1][1], "--resume")

    def test_unstructured_api_error_text_does_not_resume(self) -> None:
        with tempfile.TemporaryDirectory() as temp:
            workspace = Path(temp)
            with mock.patch("long_horizon.main_adapter.session_environment", return_value={}), mock.patch(
                "long_horizon.main_adapter.fresh_session_command", return_value=["claude"]
            ):
                result = LongSessionRunner(
                    executor=lambda command, cwd, timeout, environment: (
                        "API Error: terminated",
                        "",
                        1,
                        False,
                    )
                ).run(
                    workspace,
                    "work",
                    timeout=60,
                    handoff_path=workspace / "handoff.json",
                    handoff_resumes=2,
                    completion_check=lambda value: "",
                )

            self.assertEqual(result.resume_count, 0)
            self.assertEqual(result.exit_status, 1)

    def test_dependency_violation_overrides_claude_transient_error(self) -> None:
        with tempfile.TemporaryDirectory() as temp:
            workspace = Path(temp)
            stderr = "[orchestrator] dependency policy violation; terminated coding session"
            with mock.patch("long_horizon.main_adapter.session_environment", return_value={}), mock.patch(
                "long_horizon.main_adapter.fresh_session_command", return_value=["claude"]
            ):
                result = LongSessionRunner(
                    executor=lambda command, cwd, timeout, environment: (
                        self._claude_terminated_event(),
                        stderr,
                        1,
                        False,
                    )
                ).run(
                    workspace,
                    "work",
                    timeout=60,
                    handoff_path=workspace / "handoff.json",
                    handoff_resumes=2,
                    completion_check=lambda value: "",
                )

            self.assertEqual(result.resume_count, 0)
            self.assertEqual(result.exit_status, 1)

    def test_codex_missing_handoff_resumes_observed_thread(self) -> None:
        with tempfile.TemporaryDirectory() as temp:
            workspace = Path(temp)
            handoff = workspace / "handoff.json"
            commands: list[list[str]] = []
            thread_id = "019c1234-5678-7abc-8def-0123456789ab"

            def execute(command, cwd, timeout, environment):
                commands.append(command)
                if len(commands) == 1:
                    return json.dumps({"type": "thread.started", "thread_id": thread_id}), "", 0, False
                atomic_write_json(handoff, {"status": "pivot"})
                return "", "", 0, False

            with mock.patch("long_horizon.main_adapter.session_environment", return_value={}):
                result = LongSessionRunner(executor=execute, agent_cli="codex").run(
                    workspace,
                    "work",
                    timeout=60,
                    handoff_path=handoff,
                    handoff_resumes=2,
                    completion_check=lambda value: "",
                )

            self.assertEqual(result.session_id, thread_id)
            self.assertEqual(result.resume_count, 1)
            self.assertEqual(result.handoff.status, "pivot")
            self.assertEqual(commands[1][:3], ["codex", "exec", "resume"])
            self.assertIn(thread_id, commands[1])
            self.assertNotIn("--ephemeral", commands[0])

    def test_codex_resume_uses_incremental_ledger_usage(self) -> None:
        with tempfile.TemporaryDirectory() as temp:
            workspace = Path(temp) / "workspace"
            workspace.mkdir()
            home = Path(temp) / "codex-home"
            handoff = workspace / "handoff.json"
            thread_id = "019c1234-5678-7abc-8def-0123456789ab"
            ledger = home / "sessions/2026/08/06" / f"rollout-test-{thread_id}.jsonl"
            observer = CodexSessionLedgerObserver(home)
            calls = 0

            def token_record(last, total):
                return json.dumps(
                    {
                        "type": "event_msg",
                        "payload": {
                            "type": "token_count",
                            "info": {
                                "last_token_usage": last,
                                "total_token_usage": total,
                            },
                        },
                    }
                ) + "\n"

            def execute(command, cwd, timeout, environment):
                nonlocal calls
                calls += 1
                ledger.parent.mkdir(parents=True, exist_ok=True)
                if calls == 1:
                    ledger.write_text(
                        token_record(
                            {"input_tokens": 8, "cached_input_tokens": 6, "cache_write_input_tokens": 1, "output_tokens": 2, "total_tokens": 10},
                            {"input_tokens": 8, "cached_input_tokens": 6, "cache_write_input_tokens": 1, "output_tokens": 2, "total_tokens": 10},
                        )
                    )
                    stdout = "\n".join(
                        [
                            json.dumps({"type": "thread.started", "thread_id": thread_id}),
                            json.dumps({"type": "turn.completed", "usage": {"input_tokens": 8, "cached_input_tokens": 6, "output_tokens": 2}}),
                        ]
                    )
                    return stdout, "", 0, False
                with ledger.open("a") as handle:
                    handle.write(
                        token_record(
                            {"input_tokens": 6, "cached_input_tokens": 5, "cache_write_input_tokens": 0, "output_tokens": 1, "total_tokens": 7},
                            {"input_tokens": 14, "cached_input_tokens": 11, "cache_write_input_tokens": 1, "output_tokens": 3, "total_tokens": 17},
                        )
                    )
                atomic_write_json(handoff, {"status": "pivot"})
                return json.dumps({"type": "turn.completed", "usage": {"input_tokens": 14, "cached_input_tokens": 11, "output_tokens": 3}}), "", 0, False

            with (
                mock.patch("long_horizon.main_adapter.session_environment", return_value={}),
                mock.patch(
                    "long_horizon.session.CodexSessionLedgerObserver",
                    return_value=observer,
                ),
            ):
                result = LongSessionRunner(executor=execute, agent_cli="codex").run(
                    workspace,
                    "work",
                    timeout=60,
                    handoff_path=handoff,
                    handoff_resumes=1,
                    completion_check=lambda value: "",
                )

            self.assertEqual(result.tokens, 17)
            self.assertEqual(result.resume_count, 1)
            self.assertEqual(
                [value.terminal_usage.total_tokens for value in result.invocations],
                [10, 7],
            )
            self.assertTrue(
                all(value.capabilities.usage_delta_observed for value in result.invocations)
            )
            self.assertTrue(
                all(value.resume_usage_qualified for value in result.invocations)
            )

    def test_codex_resume_ledger_failure_uses_cumulative_stdout_delta(self) -> None:
        class FailingSecondObserver:
            def __init__(self):
                self.calls = 0
                self._offset = 0
                self._session_usage = None

            def observe(self, thread_id):
                self.calls += 1
                if self.calls == 2:
                    raise ValueError("ledger unavailable")
                usage = TokenUsage(8, 2, 6, 1, 10, "exact")
                return CodexLedgerObservation(
                    events=(
                        NormalizedAgentEvent(0, "usage_delta", usage=usage),
                        NormalizedAgentEvent(1, "terminal_usage", usage=usage),
                    ),
                    terminal_usage=usage,
                    session_usage=usage,
                )

            def observe_reconciled(self, thread_id, stream_terminal):
                return self.observe(thread_id)

            def close(self):
                return None

        with tempfile.TemporaryDirectory() as temp:
            workspace = Path(temp)
            handoff = workspace / "handoff.json"
            thread_id = "019c1234-5678-7abc-8def-0123456789ab"
            calls = 0

            def execute(command, cwd, timeout, environment):
                nonlocal calls
                calls += 1
                if calls == 1:
                    return "\n".join(
                        [
                            json.dumps({"type": "thread.started", "thread_id": thread_id}),
                            json.dumps({"type": "turn.completed", "usage": {"input_tokens": 8, "cached_input_tokens": 6, "output_tokens": 2}}),
                        ]
                    ), "", 0, False
                atomic_write_json(handoff, {"status": "pivot"})
                return json.dumps({"type": "turn.completed", "usage": {"input_tokens": 14, "cached_input_tokens": 11, "output_tokens": 3}}), "", 0, False

            with (
                mock.patch("long_horizon.main_adapter.session_environment", return_value={}),
                mock.patch(
                    "long_horizon.session.CodexSessionLedgerObserver",
                    return_value=FailingSecondObserver(),
                ),
            ):
                result = LongSessionRunner(executor=execute, agent_cli="codex").run(
                    workspace,
                    "work",
                    timeout=60,
                    handoff_path=handoff,
                    handoff_resumes=1,
                    completion_check=lambda value: "",
                )

            self.assertEqual(result.tokens, 17)
            self.assertEqual(
                [value.terminal_usage.total_tokens for value in result.invocations],
                [10, 7],
            )
            self.assertIn(
                "codex_ledger_unavailable:ValueError",
                result.invocations[1].observation_errors,
            )
            self.assertFalse(result.invocations[1].resume_usage_qualified)

    def test_codex_fallback_preserves_last_valid_cumulative_baseline(self) -> None:
        class FirstOnlyObserver:
            def __init__(self):
                self.calls = 0
                self._offset = 0
                self._session_usage = None

            def observe(self, thread_id):
                self.calls += 1
                if self.calls > 1:
                    raise ValueError("ledger unavailable")
                usage = TokenUsage(8, 2, 6, 1, 10, "exact")
                return CodexLedgerObservation(
                    events=(
                        NormalizedAgentEvent(0, "usage_delta", usage=usage),
                        NormalizedAgentEvent(1, "terminal_usage", usage=usage),
                    ),
                    terminal_usage=usage,
                    session_usage=usage,
                )

            def observe_reconciled(self, thread_id, stream_terminal):
                return self.observe(thread_id)

            def close(self):
                return None

        with tempfile.TemporaryDirectory() as temp:
            workspace = Path(temp)
            handoff = workspace / "handoff.json"
            thread_id = "019c1234-5678-7abc-8def-0123456789ab"
            calls = 0

            def execute(command, cwd, timeout, environment):
                nonlocal calls
                calls += 1
                if calls == 1:
                    return "\n".join(
                        [
                            json.dumps({"type": "thread.started", "thread_id": thread_id}),
                            json.dumps({"type": "turn.completed", "usage": {"input_tokens": 8, "cached_input_tokens": 6, "output_tokens": 2}}),
                        ]
                    ), "", 0, False
                if calls == 2:
                    return json.dumps({"type": "turn.completed", "usage": {"input_tokens": 4, "cached_input_tokens": 3, "output_tokens": 1}}), "", 0, False
                atomic_write_json(handoff, {"status": "pivot"})
                return json.dumps({"type": "turn.completed", "usage": {"input_tokens": 14, "cached_input_tokens": 11, "output_tokens": 3}}), "", 0, False

            observer = FirstOnlyObserver()
            with (
                mock.patch("long_horizon.main_adapter.session_environment", return_value={}),
                mock.patch(
                    "long_horizon.session.CodexSessionLedgerObserver",
                    return_value=observer,
                ),
            ):
                result = LongSessionRunner(executor=execute, agent_cli="codex").run(
                    workspace,
                    "work",
                    timeout=60,
                    handoff_path=handoff,
                    handoff_resumes=2,
                    completion_check=lambda value: "",
                )

            self.assertEqual(result.tokens, 17)
            self.assertEqual(
                [value.terminal_usage.total_tokens for value in result.invocations],
                [10, None, 7],
            )
            self.assertEqual(observer.calls, 2)

    def test_codex_observer_setup_failure_keeps_long_session_running(self) -> None:
        with tempfile.TemporaryDirectory() as temp:
            workspace = Path(temp)
            handoff = workspace / "handoff.json"
            thread_id = "019c1234-5678-7abc-8def-0123456789ab"

            def execute(command, cwd, timeout, environment):
                atomic_write_json(handoff, {"status": "pivot"})
                return "\n".join(
                    [
                        json.dumps({"type": "thread.started", "thread_id": thread_id}),
                        json.dumps({"type": "turn.completed", "usage": {"input_tokens": 8, "cached_input_tokens": 6, "output_tokens": 2}}),
                    ]
                ), "", 0, False

            with (
                mock.patch("long_horizon.main_adapter.session_environment", return_value={}),
                mock.patch(
                    "long_horizon.session.CodexSessionLedgerObserver",
                    side_effect=PermissionError("ledger inaccessible"),
                ),
            ):
                result = LongSessionRunner(executor=execute, agent_cli="codex").run(
                    workspace,
                    "work",
                    timeout=60,
                    handoff_path=handoff,
                    handoff_resumes=0,
                    completion_check=lambda value: "",
                )

            self.assertEqual(result.exit_status, 0)
            self.assertEqual(result.tokens, 10)
            self.assertEqual(result.handoff.status, "pivot")
            self.assertIn(
                "codex_ledger_setup_failed:PermissionError",
                result.invocations[0].observation_errors,
            )

    def test_codex_sigterm_resumes_observed_thread(self) -> None:
        with tempfile.TemporaryDirectory() as temp:
            workspace = Path(temp)
            handoff = workspace / "handoff.json"
            commands: list[list[str]] = []
            thread_id = "019c1234-5678-7abc-8def-0123456789ab"

            def execute(command, cwd, timeout, environment):
                commands.append(command)
                if len(commands) == 1:
                    stdout = json.dumps({"type": "thread.started", "thread_id": thread_id})
                    return stdout, "", -signal.SIGTERM, False
                atomic_write_json(handoff, {"status": "pivot"})
                return "", "", 0, False

            with mock.patch("long_horizon.main_adapter.session_environment", return_value={}):
                result = LongSessionRunner(executor=execute, agent_cli="codex").run(
                    workspace,
                    "work",
                    timeout=60,
                    handoff_path=handoff,
                    handoff_resumes=2,
                    completion_check=lambda value: "",
                )

            self.assertEqual(result.exit_status, 0)
            self.assertEqual(result.resume_count, 1)
            self.assertEqual(result.handoff.status, "pivot")
            self.assertIn(thread_id, commands[1])

    def test_single_invocation_captures_structured_usage_and_marker_events(self) -> None:
        with tempfile.TemporaryDirectory() as temp:
            workspace = Path(temp)
            handoff = workspace / "handoff.json"
            observed_environment: dict[str, str] = {}
            receipt = (
                'ATREX_TRACE_EVENT={"schema":"atrex.iteration_trace.v1",'
                '"kind":"phase_marker","action":"start","phase":"research",'
                '"marker_id":"marker-1"}'
            )
            stdout = "\n".join(
                [
                    json.dumps(
                        {
                            "type": "message_update",
                            "message": {
                                "role": "assistant",
                                "usage": {
                                    "input": 2,
                                    "output": 3,
                                    "cacheRead": 5,
                                    "cacheWrite": 0,
                                    "totalTokens": 10,
                                },
                            },
                        }
                    ),
                    json.dumps(
                        {
                            "type": "message_end",
                            "message": {
                                "role": "assistant",
                                "usage": {
                                    "input": 2,
                                    "output": 3,
                                    "cacheRead": 5,
                                    "cacheWrite": 0,
                                    "totalTokens": 10,
                                },
                            },
                        }
                    ),
                    json.dumps(
                        {
                            "type": "message_end",
                            "message": {
                                "role": "toolResult",
                                "content": [{"type": "text", "text": receipt}],
                                "usage": {
                                    "input": 1,
                                    "output": 1,
                                    "cacheRead": 3,
                                    "cacheWrite": 0,
                                    "totalTokens": 5,
                                },
                            },
                        }
                    ),
                    json.dumps({"type": "agent_settled"}),
                ]
            )

            def execute(command, cwd, timeout, environment):
                observed_environment.update(environment)
                atomic_write_json(handoff, {"status": "pivot"})
                return stdout, "", 0, False

            with mock.patch(
                "long_horizon.main_adapter.session_environment", return_value={}
            ):
                result = LongSessionRunner(executor=execute, agent_cli="pi").run(
                    workspace,
                    "work",
                    timeout=60,
                    handoff_path=handoff,
                    handoff_resumes=0,
                    completion_check=lambda value: "",
                    telemetry_environment={"ATREX_TELEMETRY_TRACE": "/tmp/trace.jsonl"},
                )

            self.assertEqual(result.tokens, 15)
            self.assertEqual(len(result.invocations), 1)
            observation = result.invocations[0]
            self.assertEqual(observation.terminal_usage.total_tokens, 15)
            self.assertEqual(
                [event.kind for event in observation.events],
                ["usage_delta", "usage_delta", "phase_marker", "terminal_usage"],
            )
            self.assertTrue(observation.capabilities.usage_delta_observed)
            self.assertEqual(
                observed_environment["ATREX_TELEMETRY_TRACE"], "/tmp/trace.jsonl"
            )

    def test_dependency_policy_sigterm_does_not_resume(self) -> None:
        with tempfile.TemporaryDirectory() as temp:
            workspace = Path(temp)
            thread_id = "019c1234-5678-7abc-8def-0123456789ab"
            stdout = json.dumps({"type": "thread.started", "thread_id": thread_id})
            stderr = "[orchestrator] dependency policy violation; terminated coding session"
            with mock.patch("long_horizon.main_adapter.session_environment", return_value={}):
                result = LongSessionRunner(
                    executor=lambda command, cwd, timeout, environment: (
                        stdout,
                        stderr,
                        -signal.SIGTERM,
                        False,
                    ),
                    agent_cli="codex",
                ).run(
                    workspace,
                    "work",
                    timeout=60,
                    handoff_path=workspace / "handoff.json",
                    handoff_resumes=2,
                    completion_check=lambda value: "",
                )

            self.assertEqual(result.resume_count, 0)
            self.assertEqual(result.exit_status, -signal.SIGTERM)


if __name__ == "__main__":
    unittest.main()
