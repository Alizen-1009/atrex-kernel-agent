from __future__ import annotations

import json
import shutil
import subprocess
import tempfile
from dataclasses import dataclass
from pathlib import Path
from typing import Any

from orchestrator import optimize

from .abba import TeacherABBAValidator
from .benchmark import (
    MaterializedTeacherWorkspace,
    TeacherBenchmarkResult,
    benchmark_teacher,
    materialize_teacher_workspace,
)
from .bundle import ValidatedTeacherBundle, validate_teacher_bundle
from .cli import TeacherDistillRequest
from .knowledge_view import KnowledgeView, build_knowledge_view
from .models import (
    CampaignLock,
    CampaignTerminalStatus,
    TeacherCampaignResult,
    TeacherTarget,
    canonical_json,
)
from .session_policy import TeacherSessionPolicy
from .state import (
    PRIVATE_BENCHMARK_FILE,
    PRIVATE_RESULT_FILE,
    PRIVATE_STATE_FILE,
    PUBLIC_LOCK_FILE,
    PUBLIC_TARGET_FILE,
    campaign_id_for,
    hash_operator_inputs,
    read_json_object,
    write_json_atomic,
)
from .stop_policy import TeacherStopPolicy


@dataclass(frozen=True)
class PreparedTeacherCampaign:
    campaign_id: str
    private_dir: Path
    bundle: ValidatedTeacherBundle
    knowledge_view: KnowledgeView
    materialized: MaterializedTeacherWorkspace
    target: TeacherTarget
    lock: CampaignLock


class TeacherDistillCampaign:
    def __init__(self, request: TeacherDistillRequest) -> None:
        if not isinstance(request, TeacherDistillRequest):
            raise TypeError("request must be a TeacherDistillRequest")
        self.request = request

    @property
    def candidate_workspace(self) -> Path:
        suffix = optimize.framework_workspace_suffix(
            self.request.framework,
            self.request.platform,
            "production",
        )
        return self.request.workspace_root / ("kernel_opt_%s_%s" % (self.request.name, suffix))

    @property
    def knowledge_views_root(self) -> Path:
        return self.request.workspace_root / ".atrex_knowledge_views"

    def _campaign_identity(self, bundle: ValidatedTeacherBundle, operator_hash: str) -> dict[str, Any]:
        return {
            "schema_version": 1,
            "name": self.request.name,
            "teacher_id": bundle.teacher_id,
            "bundle_hash": bundle.bundle_hash,
            "operator_input_hash": operator_hash,
            "platform": self.request.platform,
            "architecture": self.request.architecture,
            "framework": self.request.framework,
            "geomean_ratio": self.request.geomean_ratio,
            "shape_ratio": self.request.shape_ratio,
        }

    def _materialized_mapping(self, value: MaterializedTeacherWorkspace) -> dict[str, Any]:
        return {
            "workspace": str(value.workspace),
            "kind": value.kind,
            "expected_shape_keys": list(value.expected_shape_keys),
            "workload_hash": value.workload_hash,
            "evaluator_hash": value.evaluator_hash,
            "measurement_config_hash": value.measurement_config_hash,
        }

    @staticmethod
    def _materialized_from_mapping(value: dict[str, Any]) -> MaterializedTeacherWorkspace:
        return MaterializedTeacherWorkspace(
            workspace=Path(value["workspace"]).resolve(),
            kind=str(value["kind"]),
            expected_shape_keys=tuple(str(key) for key in value["expected_shape_keys"]),
            workload_hash=str(value["workload_hash"]),
            evaluator_hash=str(value["evaluator_hash"]),
            measurement_config_hash=str(value["measurement_config_hash"]),
        )

    @staticmethod
    def _benchmark_mapping(value: TeacherBenchmarkResult) -> dict[str, Any]:
        return {
            "schema_version": 1,
            "geomean_latency_us": value.geomean_latency_us,
            "latency_us_by_shape": dict(value.latency_us_by_shape),
            "workload_hash": value.workload_hash,
            "evaluator_hash": value.evaluator_hash,
            "measurement_config_hash": value.measurement_config_hash,
        }

    def _new_target(
        self,
        bundle: ValidatedTeacherBundle,
        knowledge_view: KnowledgeView,
        benchmark: TeacherBenchmarkResult,
    ) -> TeacherTarget:
        return TeacherTarget(
            schema_version=1,
            teacher_id=bundle.teacher_id,
            geomean_latency_us=benchmark.geomean_latency_us,
            latency_us_by_shape=benchmark.latency_us_by_shape,
            geomean_ratio=self.request.geomean_ratio,
            shape_ratio=self.request.shape_ratio,
            measurement_config_hash=benchmark.measurement_config_hash,
            knowledge_view_hash=knowledge_view.view_hash,
        )

    def _new_lock(
        self,
        campaign_id: str,
        target: TeacherTarget,
        benchmark: TeacherBenchmarkResult,
    ) -> CampaignLock:
        return CampaignLock(
            schema_version=1,
            campaign_id=campaign_id,
            teacher_id=target.teacher_id,
            platform=self.request.platform,
            architecture=self.request.architecture,
            framework=self.request.framework,
            workload_hash=benchmark.workload_hash,
            evaluator_hash=benchmark.evaluator_hash,
            measurement_config_hash=benchmark.measurement_config_hash,
            knowledge_view_hash=target.knowledge_view_hash,
            geomean_ratio=target.geomean_ratio,
            shape_ratio=target.shape_ratio,
        )

    def _build_view(self, bundle: ValidatedTeacherBundle) -> KnowledgeView:
        return build_knowledge_view(
            optimize.REPO_ROOT / "gpu-wiki",
            self.knowledge_views_root,
            self.request.architecture,
            self.request.framework,
            bundle.provenance,
        )

    def _materialize(
        self,
        bundle: ValidatedTeacherBundle,
        destination: Path,
    ) -> MaterializedTeacherWorkspace:
        return materialize_teacher_workspace(
            bundle,
            self.request.op_dir,
            destination,
            framework=self.request.framework,
            atrex_bench_root=self.request.atrex_bench_root,
        )

    def _prepare_fresh(
        self,
        bundle: ValidatedTeacherBundle,
        operator_hash: str,
        campaign_id: str,
        private_dir: Path,
    ) -> PreparedTeacherCampaign:
        if private_dir.exists() and any(private_dir.iterdir()):
            raise RuntimeError(
                "private Teacher state already exists without a resumable public lock: %s"
                % private_dir
            )
        private_dir.mkdir(parents=True, exist_ok=True)
        view = self._build_view(bundle)
        materialized = self._materialize(bundle, private_dir / "teacher_workspace")
        benchmark = benchmark_teacher(
            materialized,
            framework=self.request.framework,
            sandbox_hardware=self.request.sandbox_hardware,
            sandbox_profile=self.request.sandbox_profile,
            sandbox_url=self.request.sandbox_url,
            sandbox_timeout=self.request.sandbox_timeout,
        )
        target = self._new_target(bundle, view, benchmark)
        lock = self._new_lock(campaign_id, target, benchmark)
        private_state = {
            "schema_version": 1,
            "campaign_id": campaign_id,
            "bundle_path": str(bundle.root),
            "bundle_hash": bundle.bundle_hash,
            "operator_input_hash": operator_hash,
            "provenance": bundle.provenance.to_mapping(),
            "knowledge_view_root": str(view.root),
            "knowledge_view_hash": view.view_hash,
            "candidate_workspace": str(self.candidate_workspace),
            "materialized": self._materialized_mapping(materialized),
            "target": target.to_mapping(),
            "lock": lock.to_mapping(),
        }
        write_json_atomic(private_dir / PRIVATE_STATE_FILE, private_state)
        write_json_atomic(private_dir / PRIVATE_BENCHMARK_FILE, self._benchmark_mapping(benchmark))
        return PreparedTeacherCampaign(campaign_id, private_dir, bundle, view, materialized, target, lock)

    def _validate_resume_hashes(
        self,
        prepared: PreparedTeacherCampaign,
        operator_hash: str,
        private_state: dict[str, Any],
    ) -> None:
        errors: list[str] = []
        if private_state.get("bundle_hash") != prepared.bundle.bundle_hash:
            errors.append("Teacher bundle hash")
        if private_state.get("operator_input_hash") != operator_hash:
            errors.append("operator input hash")
        if private_state.get("knowledge_view_hash") != prepared.knowledge_view.view_hash:
            errors.append("knowledge view hash")
        if canonical_json(private_state.get("target") or {}) != canonical_json(prepared.target.to_mapping()):
            errors.append("Teacher target")
        if canonical_json(private_state.get("lock") or {}) != canonical_json(prepared.lock.to_mapping()):
            errors.append("campaign lock")
        if errors:
            raise RuntimeError("RESUME_CONFIG_MISMATCH: " + ", ".join(errors))

    def _prepare_resume(
        self,
        bundle: ValidatedTeacherBundle,
        operator_hash: str,
        public_lock: CampaignLock,
    ) -> PreparedTeacherCampaign:
        expected_id = campaign_id_for(self._campaign_identity(bundle, operator_hash))
        if public_lock.campaign_id != expected_id:
            raise RuntimeError("RESUME_CONFIG_MISMATCH: campaign identity")
        private_dir = self.request.private_root / public_lock.campaign_id
        private_state = read_json_object(private_dir / PRIVATE_STATE_FILE, "private campaign state")
        view = self._build_view(bundle)
        target = TeacherTarget.from_mapping(
            read_json_object(self.candidate_workspace / PUBLIC_TARGET_FILE, "public Teacher target")
        )
        stored_lock = CampaignLock.from_mapping(
            read_json_object(self.candidate_workspace / PUBLIC_LOCK_FILE, "public campaign lock")
        )
        materialized = self._materialized_from_mapping(private_state["materialized"])
        if not materialized.workspace.is_dir():
            raise RuntimeError("private Teacher workspace is missing")
        prepared = PreparedTeacherCampaign(
            public_lock.campaign_id,
            private_dir,
            bundle,
            view,
            materialized,
            target,
            stored_lock,
        )
        self._validate_resume_hashes(prepared, operator_hash, private_state)

        # Recompute immutable evaluator/workload hashes without executing the Teacher.
        with tempfile.TemporaryDirectory(prefix="teacher-resume-check-", dir=private_dir) as temp_dir:
            current = self._materialize(bundle, Path(temp_dir) / "workspace")
        for field in ("workload_hash", "evaluator_hash", "measurement_config_hash"):
            if getattr(current, field) != getattr(stored_lock, field):
                raise RuntimeError("RESUME_CONFIG_MISMATCH: %s" % field)
        return prepared

    def _load_or_prepare(self) -> PreparedTeacherCampaign:
        bundle = validate_teacher_bundle(
            self.request.teacher_solution,
            self.request.framework,
            self.request.architecture,
        )
        operator_hash = hash_operator_inputs(self.request.op_dir)
        identity = self._campaign_identity(bundle, operator_hash)
        campaign_id = campaign_id_for(identity)
        public_lock_path = self.candidate_workspace / PUBLIC_LOCK_FILE
        if public_lock_path.is_file():
            public_lock = CampaignLock.from_mapping(
                read_json_object(public_lock_path, "public campaign lock")
            )
            return self._prepare_resume(bundle, operator_hash, public_lock)
        private_dir = self.request.private_root / campaign_id
        return self._prepare_fresh(bundle, operator_hash, campaign_id, private_dir)

    def _candidate_campaign(
        self,
        prepared: PreparedTeacherCampaign,
    ) -> optimize.Campaign:
        session_policy = TeacherSessionPolicy(
            knowledge_view=prepared.knowledge_view.root,
            teacher_solution=prepared.bundle.root,
            private_root=prepared.private_dir,
            source_wiki=optimize.REPO_ROOT / "gpu-wiki",
            reference_projects=optimize.REPO_ROOT / "reference-projects",
        )
        verifier = TeacherABBAValidator(
            hardware=self.request.sandbox_hardware,
            profile=self.request.sandbox_profile,
            url=self.request.sandbox_url,
            timeout=self.request.sandbox_timeout,
            geomean_ratio=self.request.geomean_ratio,
            shape_ratio=self.request.shape_ratio,
        )
        stop_policy = TeacherStopPolicy(prepared.target, prepared.materialized, verifier)

        def runtime_linker(campaign: optimize.Campaign) -> None:
            native_root = Path(campaign.atrex_bench_root) if campaign.atrex_bench_root else None
            session_policy.link_runtime(campaign.workspace, native_root)

        return optimize.Campaign(
            name=self.request.name,
            kernel_demo=str(self.request.kernel_demo),
            platform=self.request.platform,
            framework=self.request.framework,
            notes=self.request.notes,
            arch=self.request.architecture,
            work_dir=str(self.request.workspace_root),
            workspace_suffix=optimize.framework_workspace_suffix(
                self.request.framework,
                self.request.platform,
                "production",
            ),
            max_iters=self.request.max_iters,
            token_budget=self.request.token_budget,
            iter_timeout=self.request.iter_timeout,
            setup_timeout=self.request.setup_timeout,
            salvage_timeout=self.request.salvage_timeout,
            max_stall=self.request.max_stall,
            convert_after=0,
            sandbox_hardware=self.request.sandbox_hardware,
            sandbox_profile=self.request.sandbox_profile,
            sandbox_url=self.request.sandbox_url,
            sandbox_timeout=self.request.sandbox_timeout,
            atrex_bench_root=(str(self.request.atrex_bench_root) if self.request.atrex_bench_root else ""),
            agent_cli=self.request.agent_cli,
            optimization_mode="production",
            framework_baseline="always",
            framework_baseline_timeout=self.request.framework_baseline_timeout,
            stop_policy=stop_policy,
            runtime_linker=runtime_linker,
            session_directive=session_policy.knowledge_directive(),
            session_access_policy=session_policy.process_access_policy(),
        )

    @staticmethod
    def _commit_public_contract(candidate: optimize.Campaign, prepared: PreparedTeacherCampaign) -> None:
        write_json_atomic(candidate.workspace / PUBLIC_TARGET_FILE, prepared.target.to_mapping())
        write_json_atomic(candidate.workspace / PUBLIC_LOCK_FILE, prepared.lock.to_mapping())
        subprocess.run(
            ["git", "add", PUBLIC_TARGET_FILE, PUBLIC_LOCK_FILE],
            cwd=candidate.workspace,
            check=True,
            stdout=subprocess.DEVNULL,
        )
        changed = subprocess.run(
            ["git", "diff", "--cached", "--quiet"],
            cwd=candidate.workspace,
            check=False,
        ).returncode
        if changed != 0:
            subprocess.run(
                ["git", "commit", "-m", "teacher-distill: lock immutable target"],
                cwd=candidate.workspace,
                check=True,
                stdout=subprocess.DEVNULL,
            )

    def _setup_native_reference_v0(self, candidate: optimize.Campaign) -> None:
        candidate.workspace.parent.mkdir(parents=True, exist_ok=True)
        subprocess.run(
            [
                "bash",
                str(optimize.WORKSPACE_INIT),
                candidate.campaign_name,
                str(self.request.kernel_demo),
            ],
            cwd=candidate.workspace.parent,
            check=True,
            stdout=subprocess.DEVNULL,
        )
        for name in (
            "reference.py",
            "input.py",
            "shapes.json",
            "roofline.json",
            "metadata.json",
            "valid.py",
        ):
            source = self.request.op_dir / name
            if source.is_file():
                shutil.copy2(source, candidate.workspace / name)
        candidate._link_runtime()
        candidate._install_native_evaluator()
        (candidate.workspace / "README.md").write_text(
            "# kernel_opt_%s\n\n"
            "Offline hidden-Teacher distillation campaign.\n\n"
            "## Goal\n\n"
            "Start from the immutable PyTorch/reference V0, create a naive %s V1, "
            "then optimize against the separately measured Teacher target.\n\n"
            "## Config\n\n"
            "- Target platform: `%s`\n"
            "- Runtime architecture: `%s`\n"
            "- Target framework: `%s`\n"
            "- Knowledge integrity: `hidden-audited`\n"
            % (
                candidate.campaign_name,
                self.request.framework,
                self.request.platform,
                self.request.architecture,
                self.request.framework,
            ),
            encoding="utf-8",
        )
        commands = (
            ["python", "test_kernel.py", "--version", "v0", "--no-memory"],
            [
                "python",
                "test_kernel.py",
                "--version",
                "v0",
                "--multi-seed",
                "5",
                "--no-memory",
            ],
        )
        result: dict[str, Any] | None = None
        for command in commands:
            process = optimize._sandbox_command(
                candidate.workspace,
                self.request.sandbox_hardware,
                self.request.sandbox_profile,
                self.request.sandbox_url,
                self.request.sandbox_timeout,
                command,
                gateway_kind="run",
            )
            if process.returncode != 0:
                raise RuntimeError("native V0 reference validation command failed")
            result = optimize._test_result_from_stdout(process.stdout)
            if not result.get("all_pass"):
                raise RuntimeError("native V0 reference correctness validation failed")
        assert result is not None
        memory_path = optimize._record_local_test_result(candidate.workspace, "v0", result)
        memory = json.loads(memory_path.read_text(encoding="utf-8"))
        memory.setdefault("optimization", {})["action_category"] = "baseline"
        memory["optimization"]["action_description"] = "immutable PyTorch/reference V0"
        memory_path.write_text(
            json.dumps(memory, indent=2, ensure_ascii=False) + "\n",
            encoding="utf-8",
        )
        (candidate.workspace / "baseline_report.md").write_text(
            "# V0 Reference Baseline\n\n"
            "Correctness: PASS\n\n"
            "Geomean latency: `%s us`\n"
            % result.get("latency_us_geomean"),
            encoding="utf-8",
        )
        subprocess.run(
            ["git", "add", "-A"],
            cwd=candidate.workspace,
            check=True,
            stdout=subprocess.DEVNULL,
        )
        subprocess.run(
            ["git", "commit", "-m", "V0: immutable reference baseline"],
            cwd=candidate.workspace,
            check=True,
            stdout=subprocess.DEVNULL,
        )

    def _prepare_candidate(self, candidate: optimize.Campaign, prepared: PreparedTeacherCampaign) -> None:
        latest = optimize.latest_version(candidate.workspace)
        if latest < 0:
            if self.request.atrex_bench_root is not None:
                self._setup_native_reference_v0(candidate)
            else:
                candidate.setup_baseline()
            latest = optimize.latest_version(candidate.workspace)
        if latest != 0 and not (candidate.workspace / PUBLIC_LOCK_FILE).is_file():
            raise RuntimeError("Teacher campaign requires an immutable V0 reference baseline")
        if not (candidate.workspace / PUBLIC_LOCK_FILE).is_file():
            self._commit_public_contract(candidate, prepared)
        candidate.ensure_framework_baseline()
        _commit, version = optimize.resolve_framework_baseline_commit(candidate.workspace)
        if version != 1:
            raise RuntimeError(
                "Teacher campaign requires Agent-generated V1 framework baseline; got v%d" % version
            )

    @staticmethod
    def _status_from_reason(reason: str) -> CampaignTerminalStatus:
        lowered = reason.casefold()
        if "teacher_leakage_violation" in lowered or "knowledge access policy" in lowered:
            return CampaignTerminalStatus.TEACHER_LEAKAGE_VIOLATION
        if reason.startswith("success: teacher ABBA passed"):
            return CampaignTerminalStatus.SUCCESS
        if reason.startswith("stall:"):
            return CampaignTerminalStatus.PLATEAU
        if reason.startswith("budget:"):
            return CampaignTerminalStatus.BUDGET_EXHAUSTED
        return CampaignTerminalStatus.INFRA_ERROR

    @staticmethod
    def _final_progress(candidate: optimize.Campaign) -> tuple[str | None, float | None]:
        latest = optimize.latest_version(candidate.workspace)
        if latest < 0:
            return None, None
        memory = optimize.read_memory(candidate.workspace, latest) or {}
        progress = memory.get("teacher_progress") or {}
        ratio = progress.get("final_candidate_to_teacher_ratio")
        if not isinstance(ratio, (int, float)):
            ratio = progress.get("candidate_to_teacher_geomean_ratio")
        return "v%d" % latest, float(ratio) if isinstance(ratio, (int, float)) else None

    def run(self) -> TeacherCampaignResult:
        prepared = self._load_or_prepare()
        candidate = self._candidate_campaign(prepared)
        reason = ""
        try:
            self._prepare_candidate(candidate, prepared)
            reason = candidate.run()
        except Exception as exc:
            reason = "%s: %s" % (type(exc).__name__, exc)
        audit_log = prepared.private_dir / "audit" / "access-violations.jsonl"
        if audit_log.is_file() and audit_log.stat().st_size > 0:
            reason = "TEACHER_LEAKAGE_VIOLATION: forbidden access was audited"
        status = self._status_from_reason(reason)
        final_version, final_ratio = self._final_progress(candidate)
        result = TeacherCampaignResult(
            schema_version=1,
            campaign_id=prepared.campaign_id,
            status=status,
            reason=reason,
            final_version=final_version,
            final_candidate_to_teacher_ratio=final_ratio,
        )
        write_json_atomic(prepared.private_dir / PRIVATE_RESULT_FILE, result.to_mapping())
        return result

    def run_cli(self) -> int:
        result = self.run()
        print(
            "[teacher-distill] %s — %s" % (result.status.value, result.reason),
            flush=True,
        )
        return 0 if result.status in {
            CampaignTerminalStatus.SUCCESS,
            CampaignTerminalStatus.PLATEAU,
            CampaignTerminalStatus.BUDGET_EXHAUSTED,
        } else 1
