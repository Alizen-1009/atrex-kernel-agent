from __future__ import annotations

import json
import re
import subprocess
from pathlib import Path
from typing import Protocol

from long_horizon.campaign import LongHorizonCampaign
from long_horizon.session import LongSessionRunner
from orchestrator import optimize
from orchestrator.agent_runtime.process import (
    ACCESS_POLICY_ENV,
    ProcessAccessPolicy,
    register_access_policy,
    unregister_access_policy,
)
from orchestrator.optimization_policy import install_workspace_policy
from orchestrator.stop_policy import StopDecisionStatus

from .session_policy import TeacherSessionPolicy
from .state import read_json_object, write_json_atomic


ESCALATION_STATE_FILE = "escalation_state.json"


class EpisodeRunner(Protocol):
    def run(self, candidate: optimize.Campaign) -> bool:
        ...


class _TeacherPolicyExecutor:
    def __init__(self, access_policy: ProcessAccessPolicy) -> None:
        self.access_policy = access_policy

    def __call__(
        self,
        command: list[str],
        workspace: Path,
        timeout: int,
        environment: dict[str, str],
    ) -> tuple[str, str, int, bool]:
        policy_id = register_access_policy(self.access_policy)
        scoped_environment = dict(environment)
        scoped_environment[ACCESS_POLICY_ENV] = policy_id
        try:
            return optimize._run_bounded(
                command,
                workspace,
                timeout,
                scoped_environment,
            )
        finally:
            unregister_access_policy(policy_id)


class LongHorizonEpisodeRunner:
    def __init__(
        self,
        *,
        session_policy: TeacherSessionPolicy,
        private_dir: Path,
    ) -> None:
        self.session_policy = session_policy
        self.private_dir = Path(private_dir).resolve()

    def run(self, candidate: optimize.Campaign) -> bool:
        before_blob = optimize.git_kernel_blob(candidate.workspace)
        access_policy = self.session_policy.process_access_policy()
        session_runner = LongSessionRunner(
            executor=_TeacherPolicyExecutor(access_policy),
            agent_cli=candidate.agent_cli,
        )

        def link_episode_runtime(base_campaign: optimize.Campaign, workspace: Path) -> None:
            native_root = (
                Path(base_campaign.atrex_bench_root)
                if base_campaign.atrex_bench_root
                else None
            )
            self.session_policy.link_runtime(workspace, native_root)
            install_workspace_policy(
                workspace,
                base_campaign.optimization_mode,
                base_campaign.framework,
                agent_runtime=base_campaign.agent_cli,
            )

        worktree_root = (
            candidate.workspace.parent
            / ".atrex_teacher_episode_worktrees"
            / self.private_dir.name
        )
        supervisor = LongHorizonCampaign(
            base_campaign=candidate,
            max_episodes=1,
            episode_limit=1,
            session_timeout=max(candidate.iter_timeout, 18_000),
            handoff_resumes=2,
            session_runner=session_runner,
            worktree_root=worktree_root,
            episode_runtime_linker=link_episode_runtime,
        )
        supervisor.run()
        after_blob = optimize.git_kernel_blob(candidate.workspace)
        return bool(before_blob and after_blob and before_blob != after_blob)


def mask_half_for_partial_restart(workspace: Path) -> tuple[str, ...]:
    records: list[tuple[int, Path, dict]] = []
    for path in (workspace / "memory").glob("v*.json"):
        match = re.fullmatch(r"v(\d+)", path.stem)
        if match is None:
            continue
        try:
            version = int(match.group(1))
            value = json.loads(path.read_text(encoding="utf-8"))
        except (ValueError, OSError, json.JSONDecodeError):
            continue
        if isinstance(value, dict):
            records.append((version, path, value))
    records.sort(key=lambda item: item[0])
    if not records:
        return ()
    latest = records[-1][0]
    candidates = [
        item
        for item in records
        if item[0] > 1 and item[0] != latest and not item[2].get("masked", False)
    ]
    selected = candidates[::2]
    masked: list[str] = []
    for version, path, value in selected:
        value["masked"] = True
        path.write_text(
            json.dumps(value, indent=2, ensure_ascii=False, sort_keys=True) + "\n",
            encoding="utf-8",
        )
        masked.append("v%d" % version)
    if masked and (workspace / ".git").exists():
        paths = ["memory/%s.json" % version for version in masked]
        subprocess.run(
            ["git", "add", "--", *paths],
            cwd=workspace,
            check=False,
            stdout=subprocess.DEVNULL,
            stderr=subprocess.DEVNULL,
        )
        changed = subprocess.run(
            ["git", "diff", "--cached", "--quiet"],
            cwd=workspace,
            check=False,
            stdout=subprocess.DEVNULL,
            stderr=subprocess.DEVNULL,
        ).returncode
        if changed != 0:
            subprocess.run(
                ["git", "commit", "-m", "teacher-distill: partial memory restart"],
                cwd=workspace,
                check=False,
                stdout=subprocess.DEVNULL,
                stderr=subprocess.DEVNULL,
            )
    return tuple(masked)


class TeacherEscalationManager:
    def __init__(
        self,
        *,
        private_dir: Path,
        episode_runner: EpisodeRunner,
        partial_restart_limit: int,
        final_max_stall: int,
    ) -> None:
        self.private_dir = Path(private_dir).resolve()
        self.episode_runner = episode_runner
        self.partial_restart_limit = max(0, int(partial_restart_limit))
        self.final_max_stall = max(1, int(final_max_stall))
        self.state_path = self.private_dir / ESCALATION_STATE_FILE

    def _load(self) -> dict:
        if not self.state_path.is_file():
            return {
                "schema_version": 1,
                "episodes_used": 0,
                "partial_restarts_used": 0,
                "masked_versions": [],
                "last_episode_promoted": None,
            }
        state = read_json_object(self.state_path, "Teacher escalation state")
        if state.get("schema_version") != 1:
            raise RuntimeError("unsupported Teacher escalation state schema")
        return state

    def _save(self, state: dict) -> None:
        write_json_atomic(self.state_path, state)

    def continue_after_stall(self, candidate: optimize.Campaign, reason: str) -> str:
        if not reason.startswith("stall:"):
            return reason
        state = self._load()
        if int(state.get("episodes_used", 0)) >= 1:
            return reason

        promoted = False
        try:
            promoted = bool(self.episode_runner.run(candidate))
        finally:
            state["episodes_used"] = int(state.get("episodes_used", 0)) + 1
            state["last_episode_promoted"] = promoted
            self._save(state)

        candidate.max_stall = self.final_max_stall
        optimize.write_stall(candidate.workspace, 0)
        if promoted:
            latest = optimize.latest_version(candidate.workspace)
            memory = optimize.read_memory(candidate.workspace, latest) if latest >= 0 else None
            if memory and hasattr(candidate, "_accepted_stop_decision"):
                decision = candidate._accepted_stop_decision(latest, memory)
                if decision.status == StopDecisionStatus.SUCCESS:
                    return decision.reason
                candidate._report_stop_policy_infra_error(decision)
            return candidate.run()

        restarts_used = int(state.get("partial_restarts_used", 0))
        if restarts_used >= self.partial_restart_limit:
            return reason
        masked = mask_half_for_partial_restart(candidate.workspace)
        state["partial_restarts_used"] = restarts_used + 1
        state["masked_versions"] = [*state.get("masked_versions", []), *masked]
        self._save(state)
        optimize.write_stall(candidate.workspace, 0)
        return candidate.run()
