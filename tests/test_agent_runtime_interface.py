from __future__ import annotations

import json
import tempfile
import unittest
from pathlib import Path
from unittest import mock

from orchestrator.agent_runtime import (
    AgentBackendAdapter,
    AgentRunRequest,
    BackendAdapterRegistry,
    TokenUsage,
    build_agent_runtime,
)
from orchestrator.agent_runtime.codex_ledger import CodexTemporaryHome


class AgentRuntimeInterfaceTest(unittest.TestCase):
    def test_factory_builds_each_supported_runtime(self) -> None:
        for runtime_id in ("claude", "qodercli", "codex", "pi"):
            with self.subTest(runtime_id=runtime_id):
                self.assertEqual(build_agent_runtime(runtime_id).id, runtime_id)

    def test_unknown_runtime_fails_closed(self) -> None:
        with self.assertRaisesRegex(ValueError, "unsupported agent CLI"):
            build_agent_runtime("unknown")

    def test_runtime_executes_one_request_through_the_injected_process_runner(self) -> None:
        captured: dict[str, object] = {}

        def process_runner(
            command: list[str], cwd: Path, timeout: int, env: dict | None = None
        ) -> tuple[str, str, int, bool]:
            captured.update(command=command, cwd=cwd, timeout=timeout, env=env)
            return (
                '{"type":"turn.completed","usage":{"input_tokens":7,"output_tokens":2}}',
                "stderr",
                0,
                False,
            )

        with tempfile.TemporaryDirectory(prefix="agent-runtime-interface-") as temp_dir:
            workspace = Path(temp_dir)
            runtime = build_agent_runtime("codex", process_runner=process_runner)
            result = runtime.run(
                AgentRunRequest(
                    workspace=workspace,
                    prompt="one bounded iteration",
                    timeout_s=123,
                    reasoning_effort="high",
                    sandbox_hardware="REMOTE_GPU",
                    sandbox_profile="",
                    sandbox_url="https://gateway.example.test",
                    sandbox_timeout_s=456,
                    extra_environment={"ATREX_TELEMETRY_TRACE": "trace.jsonl"},
                )
            )

        self.assertEqual(captured["cwd"], workspace)
        self.assertEqual(captured["timeout"], 123)
        command = captured["command"]
        self.assertEqual(command[:2], ["codex", "exec"])
        self.assertEqual(command[-1], "one bounded iteration")
        environment = captured["env"]
        self.assertEqual(environment["IS_SANDBOX"], "1")
        self.assertEqual(environment["ATREX_SANDBOX_GPU"], "REMOTE_GPU")
        self.assertEqual(
            environment["ATREX_SANDBOX_URL"], "https://gateway.example.test"
        )
        self.assertEqual(environment["ATREX_SANDBOX_TIMEOUT"], "456")
        self.assertEqual(environment["ATREX_TELEMETRY_TRACE"], "trace.jsonl")
        self.assertEqual(result.runtime_id, "codex")
        self.assertEqual(result.tokens, 9)
        self.assertEqual(
            result.terminal_usage,
            TokenUsage(
                input_tokens=7,
                output_tokens=2,
                cache_read_tokens=None,
                cache_write_tokens=None,
                total_tokens=9,
                measurement="exact",
            ),
        )
        self.assertEqual([event.kind for event in result.events], ["terminal_usage"])
        self.assertFalse(result.capabilities.usage_delta_observed)
        self.assertEqual(result.stderr_tail, "stderr")

    def test_codex_runtime_recovers_persisted_per_turn_usage_then_deletes_ledger(self) -> None:
        thread_id = "019fd5dd-84f3-7471-8934-eb19657dfd56"
        with tempfile.TemporaryDirectory(prefix="codex-home-") as home_dir, tempfile.TemporaryDirectory(
            prefix="codex-workspace-"
        ) as workspace_dir:
            home = Path(home_dir)
            observed_home = {}

            def process_runner(command, cwd, timeout, env=None):
                self.assertNotIn("--ephemeral", command)
                isolated_home = Path(env["CODEX_HOME"])
                observed_home["path"] = isolated_home
                ledger = isolated_home / "sessions/2026/08/06" / f"rollout-test-{thread_id}.jsonl"
                ledger.parent.mkdir(parents=True)
                ledger.write_text(
                    json.dumps(
                        {
                            "type": "event_msg",
                            "payload": {
                                "type": "token_count",
                                "info": {
                                    "last_token_usage": {
                                        "input_tokens": 8,
                                        "cached_input_tokens": 6,
                                        "cache_write_input_tokens": 1,
                                        "output_tokens": 2,
                                        "total_tokens": 10,
                                    },
                                    "total_token_usage": {
                                        "input_tokens": 8,
                                        "cached_input_tokens": 6,
                                        "cache_write_input_tokens": 1,
                                        "output_tokens": 2,
                                        "total_tokens": 10,
                                    },
                                },
                            },
                        }
                    )
                    + "\n"
                )
                stdout = "\n".join(
                    [
                        json.dumps({"type": "thread.started", "thread_id": thread_id}),
                        json.dumps(
                            {
                                "type": "turn.completed",
                                "usage": {
                                    "input_tokens": 8,
                                    "cached_input_tokens": 6,
                                    "output_tokens": 2,
                                },
                            }
                        ),
                    ]
                )
                return stdout, "", 0, False

            with mock.patch.dict("os.environ", {"CODEX_HOME": str(home)}, clear=False):
                result = build_agent_runtime(
                    "codex", process_runner=process_runner
                ).run(
                    AgentRunRequest(
                        workspace=Path(workspace_dir), prompt="observe", timeout_s=10
                    )
                )

            self.assertEqual(
                [event.kind for event in result.events],
                ["usage_delta", "terminal_usage"],
            )
            self.assertEqual(result.terminal_usage.total_tokens, 10)
            self.assertEqual(result.terminal_usage.cache_read_tokens, 6)
            self.assertEqual(result.terminal_usage.cache_write_tokens, 1)
            self.assertTrue(result.capabilities.usage_delta)
            self.assertTrue(result.capabilities.usage_delta_observed)
            self.assertEqual(result.session_id, thread_id)
            self.assertFalse(observed_home["path"].exists())

    def test_codex_runtime_recovers_thread_when_stdout_omits_thread_started(self) -> None:
        thread_id = "019fd5dd-84f3-7471-8934-eb19657dfd56"
        with tempfile.TemporaryDirectory(prefix="codex-home-") as home_dir, tempfile.TemporaryDirectory(
            prefix="codex-workspace-"
        ) as workspace_dir:
            home = Path(home_dir)
            workspace = Path(workspace_dir).resolve()
            observed_home = {}

            def process_runner(command, cwd, timeout, env=None):
                isolated_home = Path(env["CODEX_HOME"])
                observed_home["path"] = isolated_home
                ledger = isolated_home / "sessions/2026/08/06" / f"rollout-test-{thread_id}.jsonl"
                ledger.parent.mkdir(parents=True)
                records = [
                    {
                        "type": "session_meta",
                        "payload": {"id": thread_id, "cwd": str(workspace)},
                    },
                    {
                        "type": "event_msg",
                        "payload": {
                            "type": "token_count",
                            "info": {
                                "last_token_usage": {"input_tokens": 8, "cached_input_tokens": 0, "cache_write_input_tokens": 0, "output_tokens": 2, "total_tokens": 10},
                                "total_token_usage": {"input_tokens": 8, "cached_input_tokens": 0, "cache_write_input_tokens": 0, "output_tokens": 2, "total_tokens": 10},
                            },
                        },
                    },
                ]
                ledger.write_text("".join(json.dumps(value) + "\n" for value in records))
                return json.dumps(
                    {
                        "type": "turn.completed",
                        "usage": {"input_tokens": 8, "output_tokens": 2},
                    }
                ), "", 0, False

            with mock.patch.dict("os.environ", {"CODEX_HOME": str(home)}, clear=False):
                result = build_agent_runtime(
                    "codex", process_runner=process_runner
                ).run(
                    AgentRunRequest(
                        workspace=workspace, prompt="observe", timeout_s=10
                    )
                )

            self.assertEqual(result.session_id, thread_id)
            self.assertEqual(result.terminal_usage.total_tokens, 10)
            self.assertTrue(result.capabilities.usage_delta_observed)
            self.assertFalse(observed_home["path"].exists())

    def test_codex_observer_setup_failure_uses_ephemeral_terminal_fallback(self) -> None:
        captured = {}

        def process_runner(command, cwd, timeout, env=None):
            captured["command"] = command
            captured["codex_home_exists"] = Path(env["CODEX_HOME"]).exists()
            return json.dumps(
                {
                    "type": "turn.completed",
                    "usage": {"input_tokens": 8, "output_tokens": 2},
                }
            ), "", 0, False

        with (
            tempfile.TemporaryDirectory(prefix="codex-setup-failure-") as temp_dir,
            mock.patch(
                "orchestrator.agent_runtime.runtime.CodexSessionLedgerObserver",
                side_effect=PermissionError("cannot inspect CODEX_HOME"),
            ),
        ):
            result = build_agent_runtime(
                "codex", process_runner=process_runner
            ).run(
                AgentRunRequest(
                    workspace=Path(temp_dir), prompt="observe", timeout_s=10
                )
            )

        self.assertIn("--ephemeral", captured["command"])
        self.assertTrue(captured["codex_home_exists"])
        self.assertEqual(result.terminal_usage.total_tokens, 10)
        self.assertEqual(
            result.observation_errors,
            ("codex_ledger_setup_failed:PermissionError",),
        )

    def test_codex_temporary_home_failure_restores_original_home(self) -> None:
        captured = {}
        with tempfile.TemporaryDirectory(prefix="codex-source-home-") as source_dir, tempfile.TemporaryDirectory(
            prefix="codex-home-failure-workspace-"
        ) as workspace_dir:
            source = Path(source_dir)

            def process_runner(command, cwd, timeout, env=None):
                captured["command"] = command
                captured["codex_home"] = env.get("CODEX_HOME")
                return json.dumps(
                    {
                        "type": "turn.completed",
                        "usage": {"input_tokens": 8, "output_tokens": 2},
                    }
                ), "", 0, False

            with (
                mock.patch.dict("os.environ", {"CODEX_HOME": str(source)}, clear=False),
                mock.patch(
                    "orchestrator.agent_runtime.runtime.CodexTemporaryHome.open",
                    side_effect=PermissionError("cannot create isolated home"),
                ),
            ):
                result = build_agent_runtime(
                    "codex", process_runner=process_runner
                ).run(
                    AgentRunRequest(
                        workspace=Path(workspace_dir),
                        prompt="observe",
                        timeout_s=10,
                    )
                )

        self.assertIn("--ephemeral", captured["command"])
        self.assertEqual(captured["codex_home"], str(source))
        self.assertEqual(result.terminal_usage.total_tokens, 10)
        self.assertIn(
            "codex_ledger_setup_failed:PermissionError",
            result.observation_errors,
        )

    def test_codex_process_failure_still_cleans_temporary_home(self) -> None:
        with tempfile.TemporaryDirectory(prefix="codex-process-failure-") as temp_dir:
            isolated = Path(temp_dir) / "isolated"
            isolated.mkdir()
            temporary = mock.Mock()
            temporary.open.return_value = isolated
            temporary.path = isolated
            temporary.close.return_value = None

            def process_runner(*args, **kwargs):
                raise RuntimeError("process failed")

            with mock.patch(
                "orchestrator.agent_runtime.runtime.CodexTemporaryHome",
                return_value=temporary,
            ):
                with self.assertRaisesRegex(RuntimeError, "process failed"):
                    build_agent_runtime(
                        "codex", process_runner=process_runner
                    ).run(
                        AgentRunRequest(
                            workspace=Path(temp_dir),
                            prompt="observe",
                            timeout_s=10,
                        )
                    )

            temporary.close.assert_called_once()

    def test_codex_ledger_failure_preserves_stdout_terminal_usage(self) -> None:
        thread_id = "019fd5dd-84f3-7471-8934-eb19657dfd56"
        stdout = "\n".join(
            [
                json.dumps({"type": "thread.started", "thread_id": thread_id}),
                json.dumps(
                    {
                        "type": "turn.completed",
                        "usage": {"input_tokens": 8, "output_tokens": 2},
                    }
                ),
            ]
        )

        def process_runner(*args, **kwargs):
            return stdout, "", 0, False

        original_close = CodexTemporaryHome.close

        def close_with_error(instance):
            original_close(instance)
            return "codex_temporary_home_cleanup_failed:PermissionError"

        with (
            tempfile.TemporaryDirectory(prefix="codex-ledger-failure-") as temp_dir,
            mock.patch(
                "orchestrator.agent_runtime.runtime.CodexSessionLedgerObserver.observe",
                side_effect=ValueError("private ledger detail"),
            ),
            mock.patch(
                "orchestrator.agent_runtime.runtime.CodexTemporaryHome.close",
                autospec=True,
                side_effect=close_with_error,
            ),
        ):
            result = build_agent_runtime(
                "codex", process_runner=process_runner
            ).run(
                AgentRunRequest(
                    workspace=Path(temp_dir), prompt="observe", timeout_s=10
                )
            )

        self.assertEqual(result.terminal_usage.total_tokens, 10)
        self.assertEqual([event.kind for event in result.events], ["terminal_usage"])
        self.assertFalse(result.capabilities.usage_delta_observed)
        self.assertEqual(
            result.observation_errors,
            (
                "codex_ledger_unavailable:ValueError",
                "codex_temporary_home_cleanup_failed:PermissionError",
            ),
        )

    def test_adapter_normalizes_message_deltas_separately_from_terminal_usage(self) -> None:
        stdout = "\n".join(
            [
                '{"type":"assistant","message":{"usage":{"input_tokens":3,"output_tokens":2}}}',
                '{"type":"result","usage":{"input_tokens":3,"output_tokens":2}}',
            ]
        )

        def process_runner(*args, **kwargs):
            return stdout, "", 0, False

        with tempfile.TemporaryDirectory(prefix="agent-runtime-events-") as temp_dir:
            result = build_agent_runtime(
                "claude", process_runner=process_runner
            ).run(
                AgentRunRequest(
                    workspace=Path(temp_dir),
                    prompt="observe events",
                    timeout_s=10,
                )
            )

        self.assertEqual(
            [event.kind for event in result.events],
            ["usage_delta", "terminal_usage"],
        )
        self.assertEqual(result.events[0].usage.total_tokens, 5)
        self.assertEqual(result.terminal_usage.total_tokens, 5)
        self.assertTrue(result.capabilities.usage_delta_observed)

    def test_custom_adapter_can_be_registered_without_changing_the_runtime(self) -> None:
        class FakeAdapter(AgentBackendAdapter):
            id = "fake"
            settings_variable = "ATREX_FAKE_SESSION_SETTINGS"

            def build_command(self, prompt, session_id, reasoning_effort, settings):
                return ["fake-agent", prompt]

            def normalize_stream(self, stdout):
                return (), TokenUsage.unavailable()

            def auth_hint(self):
                return "configure fake-agent"

        registry = BackendAdapterRegistry()
        registry.register("fake", lambda humanize_dir: FakeAdapter())
        captured: dict[str, object] = {}

        def process_runner(command, cwd, timeout, env=None):
            captured["command"] = command
            return "", "", 0, False

        with tempfile.TemporaryDirectory(prefix="agent-runtime-registry-") as temp_dir:
            result = build_agent_runtime(
                "fake", process_runner=process_runner, registry=registry
            ).run(
                AgentRunRequest(
                    workspace=Path(temp_dir), prompt="hello", timeout_s=10
                )
            )

        self.assertEqual(captured["command"], ["fake-agent", "hello"])
        self.assertEqual(result.runtime_id, "fake")
        self.assertEqual(result.tokens, 0)
        self.assertEqual(result.terminal_usage.measurement, "unavailable")

    def test_observation_parser_failure_preserves_terminal_budget_tokens(self) -> None:
        class BrokenAdapter(AgentBackendAdapter):
            id = "broken"
            settings_variable = "ATREX_BROKEN_SESSION_SETTINGS"

            def build_command(self, prompt, session_id, reasoning_effort, settings):
                return ["broken-agent", prompt]

            def normalize_stream(self, stdout):
                raise RuntimeError("raw parser detail must not escape")

            def auth_hint(self):
                return "configure broken-agent"

        registry = BackendAdapterRegistry()
        registry.register("broken", lambda humanize_dir: BrokenAdapter())

        def process_runner(*args, **kwargs):
            return (
                '{"type":"result","usage":{"input_tokens":7,"output_tokens":2}}',
                "",
                0,
                False,
            )

        with tempfile.TemporaryDirectory(prefix="agent-runtime-failure-") as temp_dir:
            result = build_agent_runtime(
                "broken", process_runner=process_runner, registry=registry
            ).run(
                AgentRunRequest(
                    workspace=Path(temp_dir), prompt="hello", timeout_s=10
                )
            )

        self.assertEqual(result.tokens, 9)
        self.assertEqual(result.events, ())
        self.assertEqual(
            result.observation_errors,
            ("stream_normalization_failed:RuntimeError",),
        )


if __name__ == "__main__":
    unittest.main()
