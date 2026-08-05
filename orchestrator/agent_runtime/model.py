from __future__ import annotations

from collections.abc import Mapping
from dataclasses import dataclass
from pathlib import Path
from typing import Literal, Protocol


UsageMeasurement = Literal["exact", "partial", "unavailable"]
NormalizedEventKind = Literal["usage_delta", "terminal_usage", "phase_marker"]
PhaseMarkerAction = Literal["start", "end"]


@dataclass(frozen=True)
class TokenUsage:
    input_tokens: int | None
    output_tokens: int | None
    cache_read_tokens: int | None
    cache_write_tokens: int | None
    total_tokens: int | None
    measurement: UsageMeasurement

    @classmethod
    def unavailable(cls) -> "TokenUsage":
        return cls(
            input_tokens=None,
            output_tokens=None,
            cache_read_tokens=None,
            cache_write_tokens=None,
            total_tokens=None,
            measurement="unavailable",
        )


@dataclass(frozen=True)
class NormalizedAgentEvent:
    sequence: int
    kind: NormalizedEventKind
    usage: TokenUsage | None = None
    phase: str | None = None
    action: PhaseMarkerAction | None = None
    marker_id: str | None = None


@dataclass(frozen=True)
class AgentRuntimeCapabilities:
    terminal_usage: bool
    usage_delta: bool
    phase_marker_receipt: bool
    usage_delta_observed: bool = False


@dataclass(frozen=True)
class AgentRunRequest:
    workspace: Path
    prompt: str
    timeout_s: int
    reasoning_effort: str = "max"
    sandbox_hardware: str = ""
    sandbox_profile: str = ""
    sandbox_url: str = ""
    sandbox_timeout_s: int = 600
    session_id: str | None = None
    extra_environment: Mapping[str, str] | None = None


@dataclass(frozen=True)
class AgentRunResult:
    runtime_id: str
    exit_status: int
    timed_out: bool
    terminal_usage: TokenUsage
    events: tuple[NormalizedAgentEvent, ...]
    capabilities: AgentRuntimeCapabilities
    observation_errors: tuple[str, ...]
    stdout_tail: str
    stderr_tail: str
    session_id: str = ""

    @property
    def tokens(self) -> int:
        """Compatibility total used by existing campaign token budgets."""
        return self.terminal_usage.total_tokens or 0


class AgentRuntime(Protocol):
    @property
    def id(self) -> str:
        ...

    def run(self, request: AgentRunRequest) -> AgentRunResult:
        ...
