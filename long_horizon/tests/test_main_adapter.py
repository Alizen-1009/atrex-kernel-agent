from __future__ import annotations

import json
import unittest
from unittest import mock

from long_horizon import main_adapter


class SessionAdapterTests(unittest.TestCase):
    def test_fresh_codex_session_is_persistent(self) -> None:
        with mock.patch.dict("os.environ", {}, clear=True):
            command = main_adapter.fresh_session_command(
                "work", "unused-supervisor-id", "high", "codex"
            )
        self.assertEqual(command[:2], ["codex", "exec"])
        self.assertNotIn("--ephemeral", command)

    def test_codex_resume_command_uses_exec_resume(self) -> None:
        thread_id = "019c1234-5678-7abc-8def-0123456789ab"
        with mock.patch.dict("os.environ", {}, clear=True):
            command = main_adapter.resume_session_command(
                "continue", thread_id, "high", "codex"
            )
        self.assertEqual(command[:3], ["codex", "exec", "resume"])
        self.assertEqual(command[-2:], [thread_id, "continue"])
        self.assertNotIn("--ephemeral", command)
        self.assertNotIn("--color", command)

    def test_pi_uses_one_persistent_json_session_without_resume_support(self) -> None:
        with mock.patch.dict("os.environ", {}, clear=True):
            command = main_adapter.fresh_session_command(
                "work", "pi-session-id", "high", "pi"
            )

        self.assertEqual(command[:3], ["pi", "--mode", "json"])
        self.assertEqual(
            command[command.index("--session-id") + 1], "pi-session-id"
        )
        self.assertFalse(main_adapter.supports_same_session_resume("pi"))

    def test_codex_thread_id_is_read_from_jsonl(self) -> None:
        thread_id = "019c1234-5678-7abc-8def-0123456789ab"
        stdout = "\n".join(
            [
                "not json",
                json.dumps({"type": "thread.started", "thread_id": thread_id}),
            ]
        )
        self.assertEqual(
            main_adapter.session_id_from_stream("codex", stdout, "unused"), thread_id
        )


if __name__ == "__main__":
    unittest.main()
