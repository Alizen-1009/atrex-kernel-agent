from __future__ import annotations

import io
import json
import tempfile
import unittest
from contextlib import redirect_stdout
from pathlib import Path
from unittest import mock

from orchestrator.telemetry.phase_tokens import PHASES as TELEMETRY_PHASES
from tools import iteration_trace, sandbox


class IterationTraceToolTest(unittest.TestCase):
    def test_tool_and_telemetry_share_the_same_phase_contract(self) -> None:
        self.assertEqual(iteration_trace.PHASES, set(TELEMETRY_PHASES))

    def test_phase_and_source_commands_append_metadata_only_events(self) -> None:
        with tempfile.TemporaryDirectory(prefix="trace-tool-") as temp_dir:
            trace = Path(temp_dir) / "trace.jsonl"
            environment = {
                "ATREX_TELEMETRY_TRACE": str(trace),
                "ATREX_TELEMETRY_CAMPAIGN_ID": "demo",
                "ATREX_TELEMETRY_ITERATION_ID": "v3",
                "ATREX_TELEMETRY_ATTEMPT_ID": "attempt-1",
            }
            output = io.StringIO()
            with (
                mock.patch.dict(iteration_trace.os.environ, environment, clear=True),
                redirect_stdout(output),
            ):
                self.assertEqual(iteration_trace.main(["phase-start", "research"]), 0)
                self.assertEqual(
                    iteration_trace.main(
                        ["source-read", "gpu_wiki", "docs/kernel-opt/example.md"]
                    ),
                    0,
                )
                self.assertEqual(iteration_trace.main(["phase-end", "research"]), 0)
            events = [json.loads(line) for line in trace.read_text().splitlines()]
            receipts = [
                json.loads(line.removeprefix("ATREX_TRACE_EVENT="))
                for line in output.getvalue().splitlines()
            ]

        self.assertEqual(
            [event["event"] for event in events],
            ["phase_started", "source_read", "phase_completed"],
        )
        self.assertEqual(events[1]["source_kind"], "gpu_wiki")
        self.assertEqual(events[1]["reference"], "docs/kernel-opt/example.md")
        self.assertNotIn("content", events[1])
        self.assertEqual([receipt["action"] for receipt in receipts], ["start", "end"])
        self.assertEqual({receipt["phase"] for receipt in receipts}, {"research"})
        self.assertTrue(all(receipt["marker_id"] for receipt in receipts))

    def test_phase_command_emits_no_receipt_when_trace_write_is_disabled(self) -> None:
        output = io.StringIO()
        with (
            mock.patch.dict(iteration_trace.os.environ, {}, clear=True),
            redirect_stdout(output),
        ):
            self.assertEqual(iteration_trace.main(["phase-start", "research"]), 0)

        self.assertEqual(output.getvalue(), "")

    def test_source_command_rejects_absolute_or_parent_paths(self) -> None:
        for reference in ("/absolute/private/file", "../private/file"):
            with self.subTest(reference=reference):
                with self.assertRaisesRegex(ValueError, "safe relative reference"):
                    iteration_trace.main(["source-read", "workspace", reference])

    def test_public_source_strips_query_fragment_and_rejects_userinfo(self) -> None:
        sanitized = iteration_trace._safe_ref(
            "https://example.test/docs/page?credential=sensitive#private",
            source_kind="public_web",
        )

        self.assertEqual(sanitized, "https://example.test/docs/page")
        with self.assertRaisesRegex(ValueError, "credential-free HTTP URL"):
            iteration_trace._safe_ref(
                "https://user:password@example.test/docs",
                source_kind="public_web",
            )

    def test_sandbox_wrapper_records_operation_without_changing_execution(self) -> None:
        with tempfile.TemporaryDirectory(prefix="sandbox-trace-") as temp_dir:
            trace = Path(temp_dir) / "trace.jsonl"
            with (
                mock.patch.dict(
                    sandbox.os.environ,
                    {
                        "ATREX_TELEMETRY_TRACE": str(trace),
                        "ATREX_TELEMETRY_CAMPAIGN_ID": "demo",
                        "ATREX_TELEMETRY_ITERATION_ID": "v3",
                        "ATREX_TELEMETRY_ATTEMPT_ID": "attempt-1",
                    },
                    clear=True,
                ),
                mock.patch.object(sandbox, "_main", return_value=0) as run,
            ):
                result = sandbox.main(
                    ["--kind", "run", "--", "python", "test_kernel.py"]
                )
            events = [json.loads(line) for line in trace.read_text().splitlines()]

        self.assertEqual(result, 0)
        run.assert_called_once()
        self.assertEqual(
            [event["event"] for event in events],
            ["sandbox_operation_started", "sandbox_operation_completed"],
        )
        self.assertEqual(events[0]["category"], "benchmark")
        self.assertEqual(events[1]["status"], "succeeded")


if __name__ == "__main__":
    unittest.main()
