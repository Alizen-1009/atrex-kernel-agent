from __future__ import annotations

import json
import signal
import tempfile
import unittest
from pathlib import Path
from unittest import mock

from long_horizon.protocol import atomic_write_json
from long_horizon.session import LongSessionRunner


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
