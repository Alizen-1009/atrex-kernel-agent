from __future__ import annotations

import json
import tempfile
import unittest
from pathlib import Path
from unittest import mock

from orchestrator import optimize


class CampaignRuntimeBindingTest(unittest.TestCase):
    def test_campaign_workspace_policy_records_the_selected_runtime(self) -> None:
        with tempfile.TemporaryDirectory(prefix="campaign-runtime-binding-") as temp_dir:
            campaign = optimize.Campaign(
                name="demo",
                kernel_demo="/tmp/reference.py",
                platform="H20",
                framework="Triton",
                work_dir=temp_dir,
                agent_cli="codex",
            )
            campaign.workspace.mkdir(parents=True)
            with mock.patch.object(optimize, "link_runtime"):
                campaign._link_runtime()
            state = json.loads(
                (campaign.workspace / ".orchestrator_mode.json").read_text(
                    encoding="utf-8"
                )
            )
        self.assertEqual(state["agent_runtime"], "codex")

    def test_layer_policy_helper_records_the_selected_runtime(self) -> None:
        with tempfile.TemporaryDirectory(prefix="layer-runtime-binding-") as temp_dir:
            layer = optimize.LayerCampaign(
                name="decoder",
                layer_demo="/tmp/reference.py",
                platform="H20",
                framework="CuteDSL",
                work_dir=temp_dir,
                agent_cli="qodercli",
            )
            workspace = Path(temp_dir) / "policy-target"
            workspace.mkdir()
            layer._install_workspace_policy(workspace)
            state = json.loads(
                (workspace / ".orchestrator_mode.json").read_text(encoding="utf-8")
            )
        self.assertEqual(state["agent_runtime"], "qodercli")


if __name__ == "__main__":
    unittest.main()
