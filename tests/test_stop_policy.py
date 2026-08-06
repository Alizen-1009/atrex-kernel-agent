from __future__ import annotations

import json
import subprocess
import tempfile
import unittest
from pathlib import Path
from unittest import mock

from orchestrator import optimize
from orchestrator.stop_policy import (
    DefaultStopPolicy,
    StopDecision,
    StopDecisionStatus,
)


class RecordingStopPolicy:
    def __init__(self, decision: StopDecision) -> None:
        self.decision = decision
        self.calls: list[tuple[int, dict]] = []

    def evaluate_accepted_iteration(
        self,
        campaign: optimize.Campaign,
        version: int,
        memory: dict,
    ) -> StopDecision:
        self.calls.append((version, memory))
        return self.decision


def _git(workspace: Path, *args: str) -> str:
    return subprocess.run(
        ["git", *args], cwd=workspace, check=True, capture_output=True, text=True
    ).stdout.strip()


def _campaign(root: Path, policy: RecordingStopPolicy | None = None) -> optimize.Campaign:
    campaign = optimize.Campaign(
        name="demo",
        kernel_demo=str(root / "reference.py"),
        platform="H20",
        framework="Triton",
        work_dir=str(root),
        framework_baseline="never",
        max_iters=1,
        stop_policy=policy,
    )
    workspace = campaign.workspace
    (workspace / "memory").mkdir(parents=True)
    (workspace / "kernel.py").write_text("# v0\n", encoding="utf-8")
    (workspace / "README.md").write_text("# test\n", encoding="utf-8")
    (workspace / "memory/v0.json").write_text(
        json.dumps(
            {
                "version": "v0",
                "performance": {"latency_us": 10.0},
                "correctness": {"status": "PASS"},
                "quality_gate": {"result": "PASS"},
            }
        ),
        encoding="utf-8",
    )
    _git(workspace, "init", "-q")
    _git(workspace, "config", "user.email", "test@local")
    _git(workspace, "config", "user.name", "test")
    _git(workspace, "add", "-A")
    _git(workspace, "commit", "-q", "-m", "v0")
    return campaign


class DefaultStopPolicyTest(unittest.TestCase):
    def test_default_policy_preserves_peak_utilization_semantics_and_reason(self) -> None:
        campaign = optimize.Campaign(
            name="demo",
            kernel_demo="/tmp/reference.py",
            platform="H20",
            framework="Triton",
            target_util=90.0,
        )
        policy = DefaultStopPolicy()

        below = policy.evaluate_accepted_iteration(
            campaign,
            1,
            {
                "performance": {
                    "tflops_peak_utilization_pct": 89.9,
                    "bandwidth_peak_utilization_pct": 80.0,
                }
            },
        )
        reached = policy.evaluate_accepted_iteration(
            campaign,
            2,
            {
                "performance": {
                    "tflops_peak_utilization_pct": 70.0,
                    "bandwidth_peak_utilization_pct": 91.25,
                }
            },
        )

        self.assertEqual(below.status, StopDecisionStatus.CONTINUE)
        self.assertEqual(reached.status, StopDecisionStatus.SUCCESS)
        self.assertEqual(reached.reason, "success: peak_util 91.2% >= 90%")


class CampaignStopPolicyIntegrationTest(unittest.TestCase):
    def _run_with(self, decision: StopDecision) -> tuple[str, RecordingStopPolicy, int]:
        with tempfile.TemporaryDirectory(prefix="stop-policy-") as temp_dir:
            policy = RecordingStopPolicy(decision)
            campaign = _campaign(Path(temp_dir), policy)

            def accepted_iteration(workspace: Path, _prompt: str, **_kwargs: object):
                (workspace / "kernel.py").write_text("# v1 accepted\n", encoding="utf-8")
                (workspace / "memory/v1.json").write_text(
                    json.dumps(
                        {
                            "version": "v1",
                            "performance": {
                                "latency_us": 9.0,
                                "tflops_peak_utilization_pct": 50.0,
                            },
                            "correctness": {"status": "PASS"},
                            "quality_gate": {"result": "PASS"},
                        }
                    ),
                    encoding="utf-8",
                )
                _git(workspace, "add", "kernel.py", "memory/v1.json")
                _git(workspace, "commit", "-q", "-m", "v1")
                return optimize.SessionResult(0, False, 100, "", "", "sid")

            with (
                mock.patch.object(campaign, "_link_runtime"),
                mock.patch.object(optimize, "run_session", side_effect=accepted_iteration),
                mock.patch.object(optimize, "mask_half_memory"),
                mock.patch.object(campaign, "_finish", side_effect=lambda reason: reason),
            ):
                reason = campaign.run()
            stall = optimize.read_stall(campaign.workspace) or 0
            calls = list(policy.calls)

        self.assertEqual(len(calls), 1)
        self.assertEqual(calls[0][0], 1)
        self.assertEqual(calls[0][1]["performance"]["latency_us"], 9.0)
        return reason, policy, stall

    def test_custom_success_stops_with_policy_reason(self) -> None:
        reason, _policy, stall = self._run_with(
            StopDecision(StopDecisionStatus.SUCCESS, "success: teacher ABBA passed")
        )
        self.assertEqual(reason, "success: teacher ABBA passed")
        self.assertEqual(stall, 0)

    def test_custom_continue_keeps_the_accepted_iteration_and_hits_budget(self) -> None:
        reason, _policy, stall = self._run_with(StopDecision.continue_())
        self.assertEqual(reason, "budget: max-iters")
        self.assertEqual(stall, 0)

    def test_policy_infrastructure_error_does_not_turn_a_win_into_a_stall(self) -> None:
        reason, _policy, stall = self._run_with(
            StopDecision(StopDecisionStatus.INFRA_ERROR, "teacher verifier unavailable")
        )
        self.assertEqual(reason, "budget: max-iters")
        self.assertEqual(stall, 0)

    def test_invalid_policy_result_fails_closed(self) -> None:
        class InvalidPolicy:
            def evaluate_accepted_iteration(self, _campaign, _version, _memory):
                return "stop"

        campaign = optimize.Campaign(
            name="demo",
            kernel_demo="/tmp/reference.py",
            platform="H20",
            framework="Triton",
            stop_policy=InvalidPolicy(),
        )
        with self.assertRaisesRegex(TypeError, "StopDecision"):
            campaign._accepted_stop_decision(1, {})


if __name__ == "__main__":
    unittest.main()
