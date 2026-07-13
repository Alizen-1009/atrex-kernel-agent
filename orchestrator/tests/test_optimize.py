import json
import subprocess
import tempfile
import unittest
from pathlib import Path
from unittest.mock import patch

import optimize


class TokenParsingTests(unittest.TestCase):
    def test_claude_uses_terminal_result(self):
        stream = "\n".join([
            json.dumps({"message": {"usage": {"input_tokens": 10, "output_tokens": 2}}}),
            json.dumps({"type": "result", "usage": {
                "input_tokens": 20,
                "output_tokens": 3,
                "cache_read_input_tokens": 5,
            }}),
        ])
        self.assertEqual(optimize._tokens_from_stream(stream, "claude"), 28)

    def test_codex_sums_completed_turns_only(self):
        stream = "\n".join([
            json.dumps({"type": "turn.completed", "usage": {
                "input_tokens": 20,
                "cached_input_tokens": 7,
                "output_tokens": 3,
                "reasoning_output_tokens": 2,
            }}),
            json.dumps({"type": "item.completed", "usage": {"input_tokens": 999}}),
            json.dumps({"type": "turn.completed", "usage": {
                "input_tokens": 4,
                "output_tokens": 1,
            }}),
        ])
        self.assertEqual(optimize._tokens_from_stream(stream, "codex"), 28)


class SessionCommandTests(unittest.TestCase):
    def test_codex_command_is_ephemeral_json_and_writable(self):
        workspace = Path("/tmp/workspace")
        command = optimize._session_command("codex", workspace, "prompt")
        self.assertEqual(command[:4], ["codex", "exec", "--json", "--ephemeral"])
        self.assertIn("danger-full-access", command)
        self.assertEqual(command[-3:], ["-C", str(workspace), "prompt"])

    def test_claude_command_keeps_existing_stream_mode(self):
        with patch.object(optimize.uuid, "uuid4", return_value="session-id"):
            command = optimize._session_command("claude", Path("/tmp/workspace"), "prompt")
        self.assertEqual(command, [
            "claude", "--print", "--verbose", "--output-format", "stream-json",
            "--session-id", "session-id", "prompt",
        ])


class RuntimeLinkTests(unittest.TestCase):
    def test_links_codex_agent_playbooks(self):
        with tempfile.TemporaryDirectory() as directory:
            workspace = Path(directory)
            optimize.link_runtime(workspace)
            self.assertTrue((workspace / "agents").is_symlink())
            self.assertIn("/agents", (workspace / ".gitignore").read_text())

    def test_adds_codex_rules_to_existing_workspace(self):
        with tempfile.TemporaryDirectory() as directory:
            workspace = Path(directory)
            (workspace / "CLAUDE.md").write_text("shared rules")
            optimize.link_runtime(workspace)
            agents_md = workspace / "AGENTS.md"
            self.assertTrue(agents_md.is_symlink())
            self.assertEqual(agents_md.readlink(), Path("CLAUDE.md"))

    def test_preserves_existing_agents_file(self):
        with tempfile.TemporaryDirectory() as directory:
            workspace = Path(directory)
            (workspace / "CLAUDE.md").write_text("claude rules")
            (workspace / "AGENTS.md").write_text("custom codex rules")
            optimize.link_runtime(workspace)
            self.assertFalse((workspace / "AGENTS.md").is_symlink())
            self.assertEqual((workspace / "AGENTS.md").read_text(), "custom codex rules")

    def test_adds_agents_ignore_to_existing_workspace(self):
        with tempfile.TemporaryDirectory() as directory:
            workspace = Path(directory)
            (workspace / ".gitignore").write_text("/tools\n/reference\n/skills\n")
            optimize.link_runtime(workspace)
            lines = (workspace / ".gitignore").read_text().splitlines()
            self.assertEqual(lines.count("/agents"), 1)


class InstallerContractTests(unittest.TestCase):
    def test_installer_copies_self_contained_optimizer(self):
        install_script = (Path(__file__).parents[2] / "install.sh").read_text()
        self.assertIn(
            "SKILL_WHITELIST=(orchestrator agents reference skills tools SKILL.md)",
            install_script,
        )
        self.assertIn("link_skill_gpu_wiki", install_script)


class OperatorResolutionTests(unittest.TestCase):
    def test_uses_reference_as_initial_kernel_by_default(self):
        with tempfile.TemporaryDirectory() as directory:
            op_dir = Path(directory)
            reference = op_dir / "reference.py"
            reference.write_text("class Model: pass\n")

            op = optimize._resolve_op(str(op_dir))

            self.assertEqual(op["kernel_demo"], str(reference.resolve()))

    def test_uses_explicit_initial_kernel_when_provided(self):
        with tempfile.TemporaryDirectory() as directory:
            op_dir = Path(directory) / "op"
            op_dir.mkdir()
            (op_dir / "reference.py").write_text("class Model: pass\n")
            initial_kernel = Path(directory) / "kernel_v0.py"
            initial_kernel.write_text("class Model: pass\n")

            op = optimize._resolve_op(str(op_dir), str(initial_kernel))

            self.assertEqual(op["kernel_demo"], str(initial_kernel.resolve()))

    def test_rejects_missing_explicit_initial_kernel(self):
        with tempfile.TemporaryDirectory() as directory:
            op_dir = Path(directory)
            (op_dir / "reference.py").write_text("class Model: pass\n")

            with self.assertRaisesRegex(SystemExit, "--initial-kernel not found"):
                optimize._resolve_op(str(op_dir), str(op_dir / "missing.py"))

    def test_main_passes_explicit_initial_kernel_to_campaign(self):
        with tempfile.TemporaryDirectory() as directory:
            op_dir = Path(directory) / "op"
            op_dir.mkdir()
            (op_dir / "reference.py").write_text("class Model: pass\n")
            initial_kernel = Path(directory) / "kernel_v0.py"
            initial_kernel.write_text("class Model: pass\n")

            with patch.object(optimize, "detect_arch", return_value="sm_90"), \
                    patch.object(optimize, "Campaign") as campaign:
                result = optimize.main([
                    "--op-dir", str(op_dir),
                    "--initial-kernel", str(initial_kernel),
                    "--platform", "H20",
                    "--framework", "CuteDSL",
                ])

            self.assertEqual(result, 0)
            self.assertEqual(
                campaign.call_args.kwargs["kernel_demo"],
                str(initial_kernel.resolve()),
            )
            self.assertEqual(
                campaign.call_args.kwargs["reference"],
                str((op_dir / "reference.py").resolve()),
            )
            campaign.return_value.run.assert_called_once_with()


class WorkspaceInitializationTests(unittest.TestCase):
    def test_creates_codex_rules_symlink(self):
        init_script = Path(__file__).parents[2] / "reference" / "workspace_init.sh"
        with tempfile.TemporaryDirectory() as directory:
            run_dir = Path(directory)
            kernel_demo = run_dir / "reference.py"
            kernel_demo.write_text("def run():\n    return None\n")
            subprocess.run(
                ["bash", str(init_script), "demo", str(kernel_demo)],
                cwd=run_dir,
                check=True,
                capture_output=True,
                text=True,
            )
            workspace = run_dir / "kernel_opt_demo"
            agents_md = workspace / "AGENTS.md"
            self.assertTrue(agents_md.is_symlink())
            self.assertEqual(agents_md.readlink(), Path("CLAUDE.md"))
            self.assertEqual(agents_md.read_text(), (workspace / "CLAUDE.md").read_text())


if __name__ == "__main__":
    unittest.main()
