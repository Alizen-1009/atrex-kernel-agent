from __future__ import annotations

import json
import os
import re
import subprocess
import tempfile
import time
from collections.abc import Callable, Mapping, Sequence
from pathlib import Path
from typing import Any

try:
    from ..agent_runtime.model import (
        AgentRuntimeCapabilities,
        NormalizedAgentEvent,
        TokenUsage,
    )
except ImportError:  # direct script execution: python orchestrator/optimize.py
    from agent_runtime.model import (  # type: ignore[no-redef]
        AgentRuntimeCapabilities,
        NormalizedAgentEvent,
        TokenUsage,
    )

from .phase_tokens import (
    PHASES,
    aggregate_attempt_tokens,
    summarize_phase_tokens,
    usage_dict,
)


EVENT_SCHEMA_VERSION = "atrex_iteration_event_v1"
SUMMARY_SCHEMA_VERSION = "atrex_iteration_summary_v2"
TELEMETRY_ROOT = Path(".atrex") / "telemetry"
def observed_outcome(
    *,
    exit_status: int,
    timed_out: bool,
    memory: Mapping[str, Any] | None,
    kernel_changed: bool,
) -> tuple[str, list[str]]:
    """Normalize current runtime/memory/Git facts without changing authority."""
    gate = (memory.get("quality_gate") or {}).get("result") if memory else None
    correctness = (memory.get("correctness") or {}).get("status") if memory else None
    gate = str(gate or "").strip().upper()
    correctness = str(correctness or "").strip().upper()

    if kernel_changed:
        if gate == "PASS" and correctness == "PASS":
            return "accepted", []
        return "unknown", ["kernel_memory_disagreement"]
    if timed_out or exit_status != 0:
        return "runtime_failure", []
    if gate == "PASS":
        return "unknown", ["memory_git_disagreement"]
    if gate in {"FAIL", "TIMEOUT_FAIL"}:
        if correctness == "PASS":
            return "performance_rejection", []
        return "validation_failure", []
    if memory is None:
        return "interrupted", ["memory_missing"]
    return "unknown", ["terminal_outcome_unclassified"]


def changed_paths_since(workspace: Path, base_head: str) -> list[str]:
    """Return committed and worktree paths changed since one pre-session HEAD."""
    changed: set[str] = set()
    if base_head:
        diff = subprocess.run(
            ["git", "diff", "--name-only", base_head, "HEAD", "--"],
            cwd=workspace,
            capture_output=True,
            text=True,
            check=False,
        )
        if diff.returncode == 0:
            changed.update(path for path in diff.stdout.splitlines() if path)
    status = subprocess.run(
        ["git", "status", "--porcelain", "--untracked-files=all"],
        cwd=workspace,
        capture_output=True,
        text=True,
        check=False,
    )
    if status.returncode == 0:
        for line in status.stdout.splitlines():
            path = line[3:].strip() if len(line) >= 4 else ""
            if " -> " in path:
                path = path.split(" -> ", 1)[1]
            if path and not path.startswith(".atrex/"):
                changed.add(path)
    return sorted(changed)


def _summarize_events(
    events: Sequence[Mapping[str, Any]], total_seconds: float
) -> tuple[dict[str, Any], dict[str, Any], dict[str, Any], dict[str, Any]]:
    durations: dict[str, float] = {phase: 0.0 for phase in PHASES}
    interval_counts: dict[str, int] = {phase: 0 for phase in PHASES}
    invalid_phases: set[str] = set()
    timing_reasons: set[str] = set()
    active_phase: str | None = None
    active_started = 0.0
    invalid_stack: list[str] = []
    sources: list[dict[str, Any]] = []
    sandbox: list[dict[str, Any]] = []

    for event in events:
        kind = event.get("event")
        phase = str(event.get("phase") or "")
        stamp = event.get("monotonic_seconds")
        if kind in {"phase_started", "phase_completed"}:
            if phase not in durations or not isinstance(stamp, (int, float)):
                timing_reasons.add("invalid_phase_marker")
                continue
            action = "start" if kind == "phase_started" else "end"
            stamp = float(stamp)
            if invalid_stack:
                invalid_phases.add(phase)
                if action == "start":
                    invalid_stack.append(phase)
                    timing_reasons.add("overlapping_phase")
                elif phase == invalid_stack[-1]:
                    invalid_stack.pop()
                elif phase in invalid_stack:
                    timing_reasons.add("mismatched_phase_end")
                    while invalid_stack and invalid_stack[-1] != phase:
                        invalid_stack.pop()
                    if invalid_stack:
                        invalid_stack.pop()
                else:
                    timing_reasons.add("orphan_phase_end")
                continue
            if action == "start":
                if active_phase is None:
                    active_phase = phase
                    active_started = stamp
                else:
                    invalid_phases.update({active_phase, phase})
                    timing_reasons.add("overlapping_phase")
                    invalid_stack = [active_phase, phase]
                    active_phase = None
                continue
            if active_phase is None:
                invalid_phases.add(phase)
                timing_reasons.add("orphan_phase_end")
            elif active_phase != phase:
                invalid_phases.update({active_phase, phase})
                timing_reasons.add("mismatched_phase_end")
                invalid_stack = [active_phase]
                active_phase = None
            elif stamp < active_started:
                invalid_phases.add(phase)
                timing_reasons.add("negative_phase_duration")
                active_phase = None
            else:
                durations[phase] += stamp - active_started
                interval_counts[phase] += 1
                active_phase = None
            continue
        if kind == "source_read":
            sources.append(
                {
                    "source_kind": event.get("source_kind") or "unknown",
                    "reference": event.get("reference"),
                    "measurement": event.get("measurement") or "explicit",
                }
            )
        elif kind == "sandbox_operation_completed":
            sandbox.append(
                {
                    key: event.get(key)
                    for key in (
                        "operation_id",
                        "category",
                        "duration_seconds",
                        "status",
                        "exit_status",
                        "failure_type",
                    )
                    if event.get(key) is not None
                }
            )

    if active_phase is not None:
        invalid_phases.add(active_phase)
        timing_reasons.add("unclosed_phase")
    if invalid_stack:
        invalid_phases.update(invalid_stack)
        timing_reasons.add("unclosed_phase")

    phases: dict[str, Any] = {}
    for phase in PHASES:
        count = interval_counts[phase]
        measurement = (
            "partial"
            if count and phase in invalid_phases
            else ("explicit" if count else "unavailable")
        )
        phases[phase] = {
            "wall_seconds": round(durations[phase], 6) if count else None,
            "percentage": (
                round(durations[phase] / total_seconds * 100.0, 3)
                if count and total_seconds > 0
                else None
            ),
            "interval_count": count,
            "measurement": measurement,
        }

    attributed_seconds = sum(durations.values())
    if attributed_seconds > total_seconds:
        timing_reasons.add("phase_time_exceeds_iteration")
    orchestration_seconds = max(0.0, total_seconds - attributed_seconds)
    semantic_coverage = (
        round(attributed_seconds / total_seconds, 6)
        if total_seconds > 0
        else None
    )
    timing_summary = {
        "attributed_seconds": round(attributed_seconds, 6),
        "orchestration_seconds": round(orchestration_seconds, 6),
        "unattributed_seconds": 0.0 if total_seconds >= attributed_seconds else None,
        "coverage": semantic_coverage,
        "semantic_phase_coverage": semantic_coverage,
        "accounted_coverage": 1.0 if total_seconds >= attributed_seconds else None,
        "measurement": (
            "partial"
            if timing_reasons
            else ("explicit" if any(interval_counts.values()) else "unavailable")
        ),
        "reason_codes": sorted(timing_reasons),
    }
    source_summary = {
        "coverage": "explicit" if sources else "unavailable",
        "count": len(sources),
        "unique_count": len(
            {(item["source_kind"], item["reference"]) for item in sources}
        ),
        "items": sources,
    }
    sandbox_summary = {
        "coverage": "exact" if sandbox else "unavailable",
        "total_seconds": round(
            sum(float(item.get("duration_seconds") or 0.0) for item in sandbox), 6
        ),
        "items": sandbox,
    }
    return phases, source_summary, sandbox_summary, timing_summary


def _safe_id(value: str, *, fallback: str) -> str:
    normalized = re.sub(r"[^A-Za-z0-9_.-]+", "-", str(value or "")).strip("-.")
    return normalized[:120] or fallback


def _atomic_json(path: Path, payload: Mapping[str, Any]) -> None:
    path.parent.mkdir(parents=True, exist_ok=True)
    descriptor, temporary_name = tempfile.mkstemp(
        prefix=f".{path.name}.", dir=path.parent
    )
    temporary = Path(temporary_name)
    try:
        with os.fdopen(descriptor, "w", encoding="utf-8") as handle:
            json.dump(payload, handle, ensure_ascii=False, indent=2, sort_keys=True)
            handle.write("\n")
        os.replace(temporary, path)
    finally:
        temporary.unlink(missing_ok=True)


def _ensure_local_git_exclude(workspace: Path) -> None:
    exclude = workspace / ".git" / "info" / "exclude"
    if not exclude.parent.is_dir():
        return
    current = exclude.read_text(encoding="utf-8") if exclude.exists() else ""
    entry = "/.atrex/"
    if entry in current.splitlines():
        return
    exclude.parent.mkdir(parents=True, exist_ok=True)
    with exclude.open("a", encoding="utf-8") as handle:
        if current and not current.endswith("\n"):
            handle.write("\n")
        handle.write("\n# Atrex local iteration telemetry\n")
        handle.write(entry + "\n")


def render_iteration_brief(summary: Mapping[str, Any]) -> str:
    """Render one decision-ready human summary without copying raw output."""
    phases = summary.get("phases") if isinstance(summary.get("phases"), Mapping) else {}
    ranked = sorted(
        (
            (name, float(value["wall_seconds"]), value.get("measurement"))
            for name, value in phases.items()
            if isinstance(value, Mapping) and isinstance(value.get("wall_seconds"), (int, float))
            and name != "agent_session"
        ),
        key=lambda item: item[1],
        reverse=True,
    )
    phase_text = ", ".join(
        f"{name}={seconds:.1f}s ({measurement})"
        for name, seconds, measurement in ranked[:4]
    ) or "phase detail unavailable"
    source_reads = summary.get("source_reads") if isinstance(summary.get("source_reads"), Mapping) else {}
    sandbox = summary.get("sandbox_operations") if isinstance(summary.get("sandbox_operations"), Mapping) else {}
    token_summary = summary.get("phase_tokens")
    token_summary = token_summary if isinstance(token_summary, Mapping) else {}
    token_phases = token_summary.get("phases")
    token_phases = token_phases if isinstance(token_phases, Mapping) else {}

    def token_cell(value: object) -> str:
        return f"{value:,}" if isinstance(value, int) else "—"

    token_rows: list[str] = []
    for phase in PHASES:
        phase_value = token_phases.get(phase)
        phase_value = phase_value if isinstance(phase_value, Mapping) else {}
        usage = phase_value.get("usage")
        usage = usage if isinstance(usage, Mapping) else {}
        token_rows.append(
            "| "
            + " | ".join(
                [
                    phase,
                    token_cell(usage.get("input_tokens")),
                    token_cell(usage.get("output_tokens")),
                    token_cell(usage.get("cache_read_tokens")),
                    token_cell(usage.get("cache_write_tokens")),
                    token_cell(usage.get("total_tokens")),
                    str(phase_value.get("measurement") or "unavailable"),
                ]
            )
            + " |"
        )
    for label in ("orchestration", "unattributed"):
        usage = token_summary.get(label)
        usage = usage if isinstance(usage, Mapping) else {}
        token_rows.append(
            "| "
            + " | ".join(
                [
                    label,
                    token_cell(usage.get("input_tokens")),
                    token_cell(usage.get("output_tokens")),
                    token_cell(usage.get("cache_read_tokens")),
                    token_cell(usage.get("cache_write_tokens")),
                    token_cell(usage.get("total_tokens")),
                    str(usage.get("measurement") or "unavailable"),
                ]
            )
            + " |"
        )
    lines = [
        f"# Iteration {summary.get('iteration_id') or 'unknown'}",
        "",
        f"- outcome: `{summary.get('observed_outcome') or 'unknown'}`",
        f"- total wall: `{float(summary.get('total_wall_seconds') or 0.0):.1f}s`",
        f"- agent wall: `{float(summary.get('agent_wall_seconds') or 0.0):.1f}s`",
        f"- phases: {phase_text}",
        f"- source reads: `{int(source_reads.get('count') or 0)}` / unique `{int(source_reads.get('unique_count') or 0)}` ({source_reads.get('coverage') or 'unavailable'})",
        f"- sandbox: `{float(sandbox.get('total_seconds') or 0.0):.1f}s` ({sandbox.get('coverage') or 'unavailable'})",
        f"- orchestration time: `{float(summary.get('orchestration_wall_seconds') or 0.0):.1f}s`",
        f"- unattributed time: `{float(summary.get('unattributed_wall_seconds') or 0.0):.1f}s`",
        "- semantic token coverage: `"
        + (
            str(token_summary.get("coverage"))
            if isinstance(token_summary.get("coverage"), (int, float))
            else "unavailable"
        )
        + f"` ({token_summary.get('measurement') or 'unavailable'})",
        f"- accounted token coverage: `{token_summary.get('accounted_coverage') if token_summary.get('accounted_coverage') is not None else 'unavailable'}`",
        "",
        "## Phase token usage",
        "",
        "| Phase | Input | Output | Cache read | Cache write | Total | Measurement |",
        "|---|---:|---:|---:|---:|---:|---|",
        *token_rows,
    ]
    return "\n".join(lines)


class IterationTelemetryRecorder:
    """Write a bounded local trace for one ordinary optimization attempt."""

    def __init__(
        self,
        *,
        workspace: Path,
        campaign_id: str,
        version: int,
        runtime_id: str,
        base_head: str,
        base_kernel_blob: str,
        monotonic_clock: Callable[[], float] = time.monotonic,
        utc_clock: Callable[[], str],
        attempt_id: str,
    ) -> None:
        self.workspace = workspace
        self.campaign_id = _safe_id(campaign_id, fallback="campaign")
        self.version = int(version)
        self.runtime_id = _safe_id(runtime_id, fallback="runtime")
        self.base_head = str(base_head or "")
        self.base_kernel_blob = str(base_kernel_blob or "")
        self._monotonic = monotonic_clock
        self._utc = utc_clock
        self.attempt_id = _safe_id(attempt_id, fallback="attempt")
        self._iteration_started = self._monotonic()
        self._agent_started: float | None = None
        self._agent_completed: float | None = None
        self._runtime: dict[str, Any] = {}
        self._phase_tokens = summarize_phase_tokens(
            events=(),
            terminal_usage=TokenUsage.unavailable(),
            capabilities=AgentRuntimeCapabilities(False, False, False),
            observation_errors=(),
        )
        _ensure_local_git_exclude(workspace)
        self.directory = workspace / TELEMETRY_ROOT / f"v{self.version}"
        self.trace_path = self.directory / f"attempt-{self.attempt_id}.jsonl"
        self.attempt_summary_path = (
            self.directory / f"attempt-{self.attempt_id}.summary.json"
        )
        self.iteration_summary_path = self.directory / "iteration.summary.json"
        self.directory.mkdir(parents=True, exist_ok=True)
        self._append_event(
            "iteration_started",
            measurement="exact",
            monotonic_seconds=self._iteration_started,
        )

    def _append_event(
        self,
        event: str,
        *,
        measurement: str,
        fields: Mapping[str, Any] | None = None,
        monotonic_seconds: float | None = None,
    ) -> None:
        payload = {
            "schema_version": EVENT_SCHEMA_VERSION,
            "campaign_id": self.campaign_id,
            "iteration_id": f"v{self.version}",
            "attempt_id": self.attempt_id,
            "event": event,
            "timestamp": self._utc(),
            "monotonic_seconds": (
                self._monotonic() if monotonic_seconds is None else monotonic_seconds
            ),
            "source": "orchestrator",
            "measurement": measurement,
            **dict(fields or {}),
        }
        with self.trace_path.open("a", encoding="utf-8") as handle:
            handle.write(json.dumps(payload, ensure_ascii=False, sort_keys=True) + "\n")

    def environment(self) -> dict[str, str]:
        return {
            "ATREX_TELEMETRY_TRACE": str(self.trace_path),
            "ATREX_TELEMETRY_CAMPAIGN_ID": self.campaign_id,
            "ATREX_TELEMETRY_ITERATION_ID": f"v{self.version}",
            "ATREX_TELEMETRY_ATTEMPT_ID": self.attempt_id,
        }

    def agent_started(self) -> None:
        self._agent_started = self._monotonic()
        self._append_event(
            "agent_session_started",
            measurement="exact",
            monotonic_seconds=self._agent_started,
        )

    def agent_completed(
        self,
        *,
        session_id: str,
        exit_status: int,
        timed_out: bool,
        terminal_usage: TokenUsage | None = None,
        events: Sequence[NormalizedAgentEvent] = (),
        capabilities: AgentRuntimeCapabilities | None = None,
        observation_errors: Sequence[str] = (),
    ) -> None:
        self._agent_completed = self._monotonic()
        self._runtime = {
            "runtime_id": self.runtime_id,
            "session_id": _safe_id(session_id, fallback="unknown"),
            "exit_status": int(exit_status),
            "timed_out": bool(timed_out),
        }
        resolved_usage = terminal_usage or TokenUsage.unavailable()
        resolved_capabilities = capabilities or AgentRuntimeCapabilities(
            False, False, False
        )
        self._phase_tokens = summarize_phase_tokens(
            events=events,
            terminal_usage=resolved_usage,
            capabilities=resolved_capabilities,
            observation_errors=observation_errors,
        )
        for runtime_event in events:
            fields: dict[str, Any] = {"sequence": runtime_event.sequence}
            if runtime_event.usage is not None:
                fields["usage"] = usage_dict(runtime_event.usage)
            if runtime_event.phase is not None:
                fields["phase"] = runtime_event.phase
            if runtime_event.action is not None:
                fields["action"] = runtime_event.action
            if runtime_event.marker_id is not None:
                fields["marker_id"] = runtime_event.marker_id
            self._append_event(
                f"agent_{runtime_event.kind}",
                measurement=(
                    runtime_event.usage.measurement
                    if runtime_event.usage is not None
                    else "explicit"
                ),
                fields=fields,
                monotonic_seconds=self._agent_completed,
            )
        self._append_event(
            "agent_session_completed",
            measurement="exact",
            fields={
                "exit_status": int(exit_status),
                "timed_out": bool(timed_out),
                "observation_errors": list(observation_errors),
            },
            monotonic_seconds=self._agent_completed,
        )
        _atomic_json(self.attempt_summary_path, self._attempt_summary())

    def _attempt_summary(self) -> dict[str, Any]:
        agent_seconds = None
        if self._agent_started is not None and self._agent_completed is not None:
            agent_seconds = round(self._agent_completed - self._agent_started, 6)
        return {
            "schema_version": SUMMARY_SCHEMA_VERSION,
            "campaign_id": self.campaign_id,
            "iteration_id": f"v{self.version}",
            "attempt_id": self.attempt_id,
            "runtime": dict(self._runtime),
            "agent_wall_seconds": agent_seconds,
            "phase_tokens": self._phase_tokens,
            "measurement": {
                "agent_wall_time": "exact" if agent_seconds is not None else "unavailable",
            },
        }

    def finalize(
        self,
        *,
        memory: Mapping[str, Any] | None,
        post_head: str,
        post_kernel_blob: str,
        changed_paths: Sequence[str],
    ) -> Path:
        completed = self._monotonic()
        total_seconds = round(completed - self._iteration_started, 6)
        kernel_changed = bool(
            self.base_kernel_blob
            and post_kernel_blob
            and self.base_kernel_blob != post_kernel_blob
        )
        runtime_exit = int(self._runtime.get("exit_status") or 0)
        timed_out = self._runtime.get("timed_out") is True
        outcome, reasons = observed_outcome(
            exit_status=runtime_exit,
            timed_out=timed_out,
            memory=memory,
            kernel_changed=kernel_changed,
        )
        self._append_event(
            "outcome_observed",
            measurement="inferred",
            fields={"observed_outcome": outcome, "reason_codes": reasons},
            monotonic_seconds=completed,
        )
        self._append_event(
            "iteration_completed",
            measurement="exact",
            monotonic_seconds=completed,
        )

        attempt = self._attempt_summary()
        attempt_summaries: list[dict[str, Any]] = []
        for path in sorted(self.directory.glob("attempt-*.summary.json")):
            try:
                value = json.loads(path.read_text(encoding="utf-8"))
            except (OSError, json.JSONDecodeError):
                continue
            if (
                isinstance(value, dict)
                and value.get("campaign_id") == self.campaign_id
                and value.get("iteration_id") == f"v{self.version}"
            ):
                attempt_summaries.append(value)
        if not any(
            value.get("attempt_id") == self.attempt_id
            for value in attempt_summaries
        ):
            attempt_summaries.append(attempt)
        attempt_summaries.sort(key=lambda value: str(value.get("attempt_id") or ""))
        phase_tokens = aggregate_attempt_tokens(attempt_summaries)
        agent_seconds = attempt["agent_wall_seconds"]
        events = [
            value
            for line in self.trace_path.read_text(encoding="utf-8").splitlines()
            if line.strip()
            for value in [json.loads(line)]
            if isinstance(value, dict)
        ]
        phases, source_reads, sandbox_operations, phase_timing = _summarize_events(
            events, total_seconds
        )
        summary = {
            "schema_version": SUMMARY_SCHEMA_VERSION,
            "campaign_id": self.campaign_id,
            "iteration_id": f"v{self.version}",
            "attempts": attempt_summaries,
            "total_wall_seconds": total_seconds,
            "agent_wall_seconds": agent_seconds,
            "phases": {
                "agent_session": {
                    "wall_seconds": agent_seconds,
                    "percentage": (
                        round(agent_seconds / total_seconds * 100.0, 3)
                        if agent_seconds is not None and total_seconds > 0
                        else None
                    ),
                    "measurement": "exact" if agent_seconds is not None else "unavailable",
                },
                **phases,
            },
            "source_reads": source_reads,
            "sandbox_operations": sandbox_operations,
            "phase_timing": phase_timing,
            "phase_tokens": phase_tokens,
            "runtime": dict(self._runtime),
            "git": {
                "base_head": self.base_head,
                "post_head": str(post_head or ""),
                "base_kernel_blob": self.base_kernel_blob,
                "post_kernel_blob": str(post_kernel_blob or ""),
                "kernel_changed": kernel_changed,
                "changed_paths": sorted({str(path) for path in changed_paths}),
            },
            "memory": {
                "quality_gate": (memory.get("quality_gate") or {}).get("result") if memory else None,
                "correctness": (memory.get("correctness") or {}).get("status") if memory else None,
                "latency_us": (memory.get("performance") or {}).get("latency_us") if memory else None,
            },
            "observed_outcome": outcome,
            "reason_codes": reasons,
            "coverage": {
                "phase_timing": phase_timing["measurement"],
                "source_reads": source_reads["coverage"],
                "sandbox_operations": sandbox_operations["coverage"],
                "phase_tokens": phase_tokens["measurement"],
            },
            "orchestration_wall_seconds": phase_timing["orchestration_seconds"],
            "unattributed_wall_seconds": phase_timing["unattributed_seconds"],
        }
        _atomic_json(self.attempt_summary_path, attempt)
        _atomic_json(self.iteration_summary_path, summary)
        (self.directory / "iteration.brief.md").write_text(
            render_iteration_brief(summary) + "\n", encoding="utf-8"
        )
        return self.iteration_summary_path
