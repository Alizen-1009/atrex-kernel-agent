from __future__ import annotations

import json
import math
import os
from dataclasses import replace
from pathlib import Path
from typing import Protocol

from orchestrator import optimize
from orchestrator.stop_policy import StopDecision, StopDecisionStatus

from .abba import TeacherABBAResult
from .benchmark import MaterializedTeacherWorkspace
from .models import AbbaStatus, TeacherProgress, TeacherTarget


class TeacherVerifier(Protocol):
    def verify(
        self,
        *,
        candidate_workspace: Path,
        candidate_commit: str,
        teacher: MaterializedTeacherWorkspace,
    ) -> TeacherABBAResult:
        ...


class TeacherStopPolicy:
    def __init__(
        self,
        target: TeacherTarget,
        teacher: MaterializedTeacherWorkspace,
        verifier: TeacherVerifier,
    ) -> None:
        if target.measurement_config_hash != teacher.measurement_config_hash:
            raise ValueError("Teacher target and materialized measurement config do not match")
        if set(target.latency_us_by_shape) != set(teacher.expected_shape_keys):
            raise ValueError("Teacher target and materialized shape sets do not match")
        self.target = target
        self.teacher = teacher
        self.verifier = verifier

    @staticmethod
    def _positive(value: object, name: str) -> float:
        if (
            isinstance(value, bool)
            or not isinstance(value, (int, float))
            or not math.isfinite(float(value))
            or float(value) <= 0.0
        ):
            raise ValueError("%s must be finite and positive" % name)
        return float(value)

    def _candidate_progress(self, memory: dict) -> TeacherProgress:
        performance = memory.get("performance")
        if not isinstance(performance, dict):
            raise ValueError("accepted Candidate memory has no performance object")
        candidate_geomean = self._positive(performance.get("latency_us"), "performance.latency_us")
        by_shape = performance.get("latency_us_by_shape")
        if not isinstance(by_shape, dict) or set(by_shape) != set(self.target.latency_us_by_shape):
            raise ValueError("accepted Candidate shape measurements do not match Teacher target")
        ratios: dict[str, float] = {}
        for key, teacher_latency in self.target.latency_us_by_shape.items():
            candidate_latency = self._positive(
                by_shape.get(key), "performance.latency_us_by_shape[%s]" % key
            )
            ratios[key] = candidate_latency / teacher_latency
        candidate_ratio = candidate_geomean / self.target.geomean_latency_us
        worst_key = max(ratios, key=lambda key: ratios[key])
        worst_ratio = ratios[worst_key]
        geomean_met = candidate_ratio <= self.target.geomean_ratio
        shape_met = worst_ratio <= self.target.shape_ratio
        return TeacherProgress(
            target_id=self.target.teacher_id,
            candidate_to_teacher_geomean_ratio=candidate_ratio,
            worst_shape_ratio=worst_ratio,
            worst_shape_key=worst_key,
            geomean_gate_met=geomean_met,
            shape_gate_met=shape_met,
            provisional_target_met=geomean_met and shape_met,
            abba_status=AbbaStatus.NOT_RUN,
        )

    @staticmethod
    def _persist(workspace: Path, version: int, memory: dict, progress: TeacherProgress) -> None:
        memory["teacher_progress"] = progress.to_mapping()
        path = workspace / "memory" / ("v%d.json" % version)
        path.parent.mkdir(parents=True, exist_ok=True)
        temporary = path.with_name(path.name + ".tmp")
        temporary.write_text(
            json.dumps(memory, indent=2, ensure_ascii=False, sort_keys=True) + "\n",
            encoding="utf-8",
        )
        os.replace(temporary, path)

    def evaluate_accepted_iteration(
        self,
        campaign: optimize.Campaign,
        version: int,
        memory: dict,
    ) -> StopDecision:
        progress = self._candidate_progress(memory)
        self._persist(campaign.workspace, version, memory, progress)
        if not progress.provisional_target_met:
            return StopDecision.continue_()

        candidate_commit = optimize.git_head(campaign.workspace)
        if not candidate_commit:
            failed = replace(progress, abba_status=AbbaStatus.INFRA_ERROR)
            self._persist(campaign.workspace, version, memory, failed)
            return StopDecision(
                StopDecisionStatus.INFRA_ERROR,
                "Teacher ABBA cannot run without a committed Candidate",
            )
        try:
            verification = self.verifier.verify(
                candidate_workspace=campaign.workspace,
                candidate_commit=candidate_commit,
                teacher=self.teacher,
            )
        except Exception as exc:
            verification = TeacherABBAResult(
                status=AbbaStatus.INFRA_ERROR,
                candidate_latency_us=None,
                teacher_latency_us=None,
                candidate_to_teacher_ratio=None,
                worst_shape_ratio=None,
                worst_shape_key=None,
                error="%s: %s" % (type(exc).__name__, exc),
            )

        verified = TeacherProgress(
            target_id=progress.target_id,
            candidate_to_teacher_geomean_ratio=progress.candidate_to_teacher_geomean_ratio,
            worst_shape_ratio=progress.worst_shape_ratio,
            worst_shape_key=progress.worst_shape_key,
            geomean_gate_met=progress.geomean_gate_met,
            shape_gate_met=progress.shape_gate_met,
            provisional_target_met=progress.provisional_target_met,
            abba_status=verification.status,
            final_candidate_to_teacher_ratio=verification.candidate_to_teacher_ratio,
        )
        self._persist(campaign.workspace, version, memory, verified)
        if verification.status == AbbaStatus.PASS:
            return StopDecision(
                StopDecisionStatus.SUCCESS,
                "success: teacher ABBA passed (candidate/teacher %.3f)"
                % (verification.candidate_to_teacher_ratio or 0.0),
            )
        if verification.status == AbbaStatus.INFRA_ERROR:
            return StopDecision(
                StopDecisionStatus.INFRA_ERROR,
                "Teacher ABBA infrastructure error: %s" % (verification.error or "unknown"),
            )
        return StopDecision.continue_()
