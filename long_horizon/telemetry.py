from __future__ import annotations

from collections.abc import Mapping, Sequence
from typing import Any

from orchestrator.agent_runtime.model import (
    AgentRuntimeCapabilities,
    TokenUsage,
)
from orchestrator.telemetry.phase_tokens import (
    PHASES,
    aggregate_attempt_tokens,
    summarize_phase_tokens,
)

from .models import InvocationObservation


EPISODE_TELEMETRY_SCHEMA_VERSION = "atrex_long_horizon_episode_telemetry_v1"


def _fallback_phase_tokens(control_tokens: int) -> dict[str, Any]:
    terminal = TokenUsage(
        input_tokens=None,
        output_tokens=None,
        cache_read_tokens=None,
        cache_write_tokens=None,
        total_tokens=max(0, int(control_tokens)),
        measurement="partial",
    )
    return summarize_phase_tokens(
        events=(),
        terminal_usage=terminal,
        capabilities=AgentRuntimeCapabilities(False, False, False),
        observation_errors=("structured_usage_unavailable",),
    )


def summarize_episode(
    *,
    episode: int,
    version: int,
    status: str,
    accepted: bool,
    control_tokens: int,
    resume_count: int,
    invocations: Sequence[InvocationObservation],
) -> dict[str, Any]:
    """Summarize one episode without changing its existing control-token contract."""
    invocation_summaries: list[dict[str, Any]] = []
    for index, invocation in enumerate(invocations, start=1):
        phase_tokens = summarize_phase_tokens(
            events=invocation.events,
            terminal_usage=invocation.terminal_usage,
            capabilities=invocation.capabilities,
            observation_errors=invocation.observation_errors,
        )
        invocation_summaries.append(
            {
                "invocation": index,
                "phase_tokens": phase_tokens,
                "measurement": phase_tokens["measurement"],
            }
        )

    phase_tokens = (
        aggregate_attempt_tokens(invocation_summaries)
        if invocation_summaries
        else _fallback_phase_tokens(control_tokens)
    )
    reasons = set(str(value) for value in phase_tokens.get("reason_codes", []))
    if not invocation_summaries:
        reasons.add("structured_usage_unavailable")
    if resume_count > 0 or len(invocation_summaries) > 1:
        reasons.add("same_session_resume_usage_semantics_unqualified")
    structured_total = (phase_tokens.get("terminal_usage") or {}).get("total_tokens")
    if (
        isinstance(structured_total, int)
        and structured_total != max(0, int(control_tokens))
    ):
        reasons.add("control_token_total_mismatch")

    measurement = str(phase_tokens.get("measurement") or "unavailable")
    if invocation_summaries and reasons:
        measurement = "partial"
    phase_tokens["reason_codes"] = sorted(reasons)
    if reasons and phase_tokens.get("measurement") == "exact":
        phase_tokens["measurement"] = "partial"

    return {
        "schema_version": EPISODE_TELEMETRY_SCHEMA_VERSION,
        "episode": int(episode),
        "version": f"v{int(version)}",
        "status": str(status),
        "accepted": bool(accepted),
        "control_tokens": max(0, int(control_tokens)),
        "resume_count": max(0, int(resume_count)),
        "invocation_count": len(invocation_summaries),
        "invocations": invocation_summaries,
        "phase_tokens": phase_tokens,
        "measurement": measurement,
        "reason_codes": sorted(reasons),
    }


def render_episode_brief(summary: Mapping[str, Any]) -> str:
    tokens = summary.get("phase_tokens")
    tokens = tokens if isinstance(tokens, Mapping) else {}
    phases = tokens.get("phases")
    phases = phases if isinstance(phases, Mapping) else {}

    def cell(value: object) -> str:
        return f"{value:,}" if isinstance(value, int) else "—"

    rows: list[str] = []
    for phase in PHASES:
        payload = phases.get(phase)
        payload = payload if isinstance(payload, Mapping) else {}
        usage = payload.get("usage")
        usage = usage if isinstance(usage, Mapping) else {}
        rows.append(
            "| "
            + " | ".join(
                [
                    phase,
                    cell(usage.get("input_tokens")),
                    cell(usage.get("output_tokens")),
                    cell(usage.get("cache_read_tokens")),
                    cell(usage.get("cache_write_tokens")),
                    cell(usage.get("total_tokens")),
                    str(payload.get("interval_count") or 0),
                    str(payload.get("measurement") or "unavailable"),
                ]
            )
            + " |"
        )
    for label in ("orchestration", "unattributed"):
        usage = tokens.get(label)
        usage = usage if isinstance(usage, Mapping) else {}
        rows.append(
            "| "
            + " | ".join(
                [
                    label,
                    cell(usage.get("input_tokens")),
                    cell(usage.get("output_tokens")),
                    cell(usage.get("cache_read_tokens")),
                    cell(usage.get("cache_write_tokens")),
                    cell(usage.get("total_tokens")),
                    "—",
                    str(usage.get("measurement") or "unavailable"),
                ]
            )
            + " |"
        )

    terminal = tokens.get("terminal_usage")
    terminal = terminal if isinstance(terminal, Mapping) else {}
    return "\n".join(
        [
            f"# Long-horizon Episode {summary.get('episode', 'unknown')}",
            "",
            f"- version: `{summary.get('version', 'unknown')}`",
            f"- status: `{summary.get('status', 'unknown')}`",
            f"- accepted: `{bool(summary.get('accepted'))}`",
            f"- invocations/resumes: `{summary.get('invocation_count', 0)}` / `{summary.get('resume_count', 0)}`",
            f"- control tokens: `{cell(summary.get('control_tokens'))}`",
            f"- structured terminal tokens: `{cell(terminal.get('total_tokens'))}`",
            f"- semantic coverage: `{tokens.get('semantic_phase_coverage', 'unavailable')}`",
            f"- accounted coverage: `{tokens.get('accounted_coverage', 'unavailable')}`",
            f"- measurement: `{summary.get('measurement', 'unavailable')}`",
            f"- reason codes: `{', '.join(summary.get('reason_codes') or []) or 'none'}`",
            "",
            "## Phase token usage",
            "",
            "| Phase | Input | Output | Cache read | Cache write | Total | Intervals | Measurement |",
            "|---|---:|---:|---:|---:|---:|---:|---|",
            *rows,
        ]
    )
