"""Supported model metadata and Chat SDK registry response models."""

from __future__ import annotations

from dataclasses import dataclass, field
from typing import Any, Dict, List, Literal, Mapping, Optional

from core_ai.types import Message
from core_harness.models import (
    ControlPlaneEvent,
    ControlPlaneEventType,
    HarnessResult,
    PendingToolCall,
    RunLimits,
    ToolCall,
    ToolResult,
    UsageTotals,
)

from pydantic import BaseModel, ConfigDict, Field

ThinkingLevel = Literal[
    "none",
    "minimal",
    "low",
    "medium",
    "high",
    "xhigh",
    "max",
]

RunStatus = Literal[
    "queued",
    "running",
    "completed",
    "failed",
    "cancelled",
]


@dataclass(frozen=True)
class RunContext:
    """Application-resolved identity and storage session for a run.

    Applications should use ``principal_id`` and/or an opaque, namespaced
    ``session_id`` to keep tenant conversations isolated.
    """

    session_id: str
    principal_id: Optional[str] = None
    metadata: Mapping[str, Any] = field(default_factory=dict)


class RunRequest(BaseModel):
    model_config = ConfigDict(extra="forbid")

    message: str = Field(..., min_length=1)
    conversation: Optional[List[Message]] = None
    session_id: Optional[str] = Field(default=None, min_length=1)
    model_id: Optional[str] = Field(default=None, min_length=1)
    reasoning_effort: Optional[ThinkingLevel] = None


@dataclass(frozen=True)
class RunSubmission:
    """Accepted request plus resolved identity, suitable for queue serialization."""

    context: RunContext
    request: RunRequest


@dataclass(frozen=True)
class SupportedModel:
    """Optional allowlist entry that further restricts ``GET /models`` and ``POST /runs``.

    When omitted, the live ``ModelRegistry`` and core_ai catalog are the source
    of truth. When provided, only these slugs are advertised and accepted, and
    each must still be routable by the registry (``provider:model``).
    """

    slug: str
    label: str = ""

    def __post_init__(self) -> None:
        slug = self.slug.strip()
        if not slug:
            raise ValueError("model slug must not be empty")
        object.__setattr__(self, "slug", slug)
        object.__setattr__(self, "label", self.label.strip() or slug)


class RegistryModel(BaseModel):
    model_config = ConfigDict(populate_by_name=True)

    id: str
    label: str
    thinking_levels: List[ThinkingLevel] = Field(alias="thinkingLevels")


class RegistryProvider(BaseModel):
    model_config = ConfigDict(populate_by_name=True)

    id: str
    label: str
    logo: str
    default_model: str = Field(alias="defaultModel")
    models: List[RegistryModel]


class StreamedTurn(BaseModel):
    assistant_text: str = ""
    reasoning_texts: Dict[int, str] = Field(default_factory=dict)
    pending_calls: Dict[int, PendingToolCall] = Field(default_factory=dict)
    saw_usage: bool = False
    attempt_usage: UsageTotals = Field(default_factory=UsageTotals)
    budget_tokens: int = 0
    estimated_message_tokens: int = 0


class TurnResult(BaseModel):
    assistant_text: str
    tool_calls: List[ToolCall]
    usage: UsageTotals
    budget_tokens: int
    context_left: Optional[int]
    message_sizes: List[Dict[str, Any]]


class ModelRegistryResponse(BaseModel):
    model_config = ConfigDict(populate_by_name=True)

    default_provider_id: str = Field(alias="defaultProviderId")
    providers: List[RegistryProvider]


class RunAccepted(BaseModel):
    run_id: str
    session_id: str
    status: RunStatus
    events_url: str


class RunStatusResponse(BaseModel):
    run_id: str
    session_id: str
    status: RunStatus
    created_at: float
    started_at: Optional[float] = None
    finished_at: Optional[float] = None
    error: Optional[str] = None


__all__ = [
    "ControlPlaneEvent",
    "ControlPlaneEventType",
    "HarnessResult",
    "ModelRegistryResponse",
    "PendingToolCall",
    "RegistryModel",
    "RegistryProvider",
    "RunAccepted",
    "RunContext",
    "RunRequest",
    "RunStatus",
    "RunStatusResponse",
    "RunLimits",
    "StreamedTurn",
    "SupportedModel",
    "ThinkingLevel",
    "ToolCall",
    "ToolResult",
    "TurnResult",
    "UsageTotals",
]
