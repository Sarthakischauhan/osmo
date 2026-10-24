"""Server-side harness configuration."""

from __future__ import annotations

import os
import uuid
from dataclasses import dataclass, field
from typing import Awaitable, Callable, Dict, List, Optional

from core_ai.registry import ModelRegistry
from core_harness import (
    Addon,
    HarnessConfig,
    Persistence,
    PersistenceAddon,
    SubagentAddon,
    Tool,
    compaction_from_config,
)
from fastapi import Request

from core_server.models import RunContext, SupportedModel, ThinkingLevel

DEFAULT_SYSTEM_PROMPT = "You are a helpful agent."

AddonFactory = Callable[[RunContext], Addon]
SubagentFactory = Callable[[RunContext], SubagentAddon]
RunContextResolver = Callable[[Request, Optional[str]], Awaitable[RunContext]]
RunAuthorizer = Callable[[Request, RunContext], Awaitable[bool]]


async def anonymous_run_context(
    request: Request,
    requested_session_id: Optional[str],
) -> RunContext:
    """Single-tenant default; authenticated apps should replace this resolver."""
    del request
    return RunContext(session_id=requested_session_id or str(uuid.uuid4()))


async def allow_run_access(request: Request, context: RunContext) -> bool:
    """Single-tenant default authorizer."""
    del request, context
    return True


@dataclass
class ServerConfig:
    """Values used to construct a CoreHarness for each run.

    ``supported_models`` is an optional extra allowlist on top of the live
    registry. Empty means ``GET /models`` and ``POST /runs`` use the registry
    and core_ai catalog unrestricted.
    """

    registry: ModelRegistry
    model_id: str
    supported_models: List[SupportedModel] = field(default_factory=list)
    system_prompt: str = DEFAULT_SYSTEM_PROMPT
    tools: List[Tool] = field(default_factory=list)
    persistence: Optional[Persistence] = None
    addon_factories: List[AddonFactory] = field(default_factory=list)
    enable_subagents: bool = False
    subagent_factory: Optional[SubagentFactory] = None
    resolve_run_context: RunContextResolver = anonymous_run_context
    authorize_run: RunAuthorizer = allow_run_access
    reasoning_effort: Optional[ThinkingLevel] = None
    max_turns: int = 8
    max_tool_calls: Optional[int] = None
    max_runtime_seconds: Optional[float] = None
    max_tokens: Optional[int] = None
    context_limits: Optional[Dict[str, int]] = None
    context_warn_threshold: Optional[int] = None
    context_compact_ratio: Optional[float] = 0.8
    tool_result_max_chars: Optional[int] = 32_000
    context_target_tokens: Optional[int] = None
    compaction_keep_recent_tools: int = 32
    max_spawn_depth: int = 1
    spawn_max_turns: Optional[int] = None
    max_parallel_tool_calls: int = 3
    cors_origins: List[str] = field(default_factory=list)
    max_request_bytes: int = 1_048_576
    max_message_chars: int = 32_000
    max_history_messages: int = 100
    max_history_chars: int = 500_000

    def __post_init__(self) -> None:
        self.supported_models = list(self.supported_models)
        slugs = [model.slug for model in self.supported_models]
        if len(slugs) != len(set(slugs)):
            raise ValueError("supported model slugs must be unique")
        if slugs and self.model_id not in slugs:
            raise ValueError("model_id must be present in supported_models")

        positive = {
            "max_turns": self.max_turns,
            "compaction_keep_recent_tools": self.compaction_keep_recent_tools,
            "max_parallel_tool_calls": self.max_parallel_tool_calls,
            "max_request_bytes": self.max_request_bytes,
            "max_message_chars": self.max_message_chars,
            "max_history_messages": self.max_history_messages,
            "max_history_chars": self.max_history_chars,
        }
        for name, value in positive.items():
            if value <= 0:
                raise ValueError(f"{name} must be positive")
        if self.spawn_max_turns is not None and self.spawn_max_turns <= 0:
            raise ValueError("spawn_max_turns must be positive")
        if self.max_spawn_depth < 0:
            raise ValueError("max_spawn_depth must be non-negative")

    def to_harness_config(self) -> HarnessConfig:
        """Settings object passed to ``CoreHarness`` for each run."""
        values: dict = {
            "max_turns": self.max_turns,
            "max_tool_calls": self.max_tool_calls,
            "max_runtime_seconds": self.max_runtime_seconds,
            "max_tokens": self.max_tokens,
            "context_warn_threshold": self.context_warn_threshold,
            "context_compact_ratio": self.context_compact_ratio,
            "tool_result_max_chars": self.tool_result_max_chars,
            "context_target_tokens": self.context_target_tokens,
            "compaction_keep_recent_tools": self.compaction_keep_recent_tools,
            "max_spawn_depth": self.max_spawn_depth,
            "spawn_max_turns": self.spawn_max_turns,
            "max_parallel_tool_calls": self.max_parallel_tool_calls,
        }
        if self.context_limits is not None:
            values["context_limits"] = self.context_limits
        return HarnessConfig(**values)

    def addons_for_run(self, context: RunContext) -> list[Addon]:
        """Persistence, compaction, and spawn add-ons for one ``CoreHarness``."""
        addons: list[Addon] = []
        if self.persistence is not None:
            addons.append(PersistenceAddon(self.persistence))
        if self.context_compact_ratio is not None:
            addons.append(compaction_from_config(self.to_harness_config()))
        if self.enable_subagents or self.subagent_factory is not None:
            factory = self.subagent_factory or (lambda _: SubagentAddon())
            addons.append(factory(context))
        addons.extend(factory(context) for factory in self.addon_factories)
        return addons

def build_config(
    *,
    registry: Optional[ModelRegistry] = None,
    model_id: Optional[str] = None,
    supported_models: Optional[List[SupportedModel]] = None,
    system_prompt: str = DEFAULT_SYSTEM_PROMPT,
    tools: Optional[List[Tool]] = None,
    persistence: Optional[Persistence] = None,
    addon_factories: Optional[List[AddonFactory]] = None,
    enable_subagents: bool = False,
    subagent_factory: Optional[SubagentFactory] = None,
    resolve_run_context: RunContextResolver = anonymous_run_context,
    authorize_run: RunAuthorizer = allow_run_access,
    reasoning_effort: Optional[ThinkingLevel] = None,
    max_turns: int = 8,
    max_tool_calls: Optional[int] = None,
    max_runtime_seconds: Optional[float] = None,
    max_tokens: Optional[int] = None,
    cors_origins: Optional[List[str]] = None,
    max_request_bytes: int = 1_048_576,
    max_message_chars: int = 32_000,
    max_history_messages: int = 100,
    max_history_chars: int = 500_000,
    context_limits: Optional[Dict[str, int]] = None,
    context_warn_threshold: Optional[int] = None,
    context_compact_ratio: Optional[float] = 0.8,
    tool_result_max_chars: Optional[int] = 32_000,
    context_target_tokens: Optional[int] = None,
    compaction_keep_recent_tools: int = 32,
    max_spawn_depth: int = 1,
    spawn_max_turns: Optional[int] = None,
    max_parallel_tool_calls: int = 3,
) -> ServerConfig:
    """Build a server config from explicit values or the process environment."""
    resolved_registry = registry if registry is not None else _default_registry()
    return ServerConfig(
        registry=resolved_registry,
        model_id=_resolve_model_id(resolved_registry, model_id),
        supported_models=list(supported_models or []),
        system_prompt=system_prompt,
        tools=list(tools or []),
        persistence=persistence,
        addon_factories=list(addon_factories or []),
        enable_subagents=enable_subagents,
        subagent_factory=subagent_factory,
        resolve_run_context=resolve_run_context,
        authorize_run=authorize_run,
        reasoning_effort=reasoning_effort,
        max_turns=max_turns,
        max_tool_calls=max_tool_calls,
        max_runtime_seconds=max_runtime_seconds,
        max_tokens=max_tokens,
        cors_origins=list(cors_origins or []),
        max_request_bytes=max_request_bytes,
        max_message_chars=max_message_chars,
        max_history_messages=max_history_messages,
        max_history_chars=max_history_chars,
        context_limits=context_limits,
        context_warn_threshold=context_warn_threshold,
        context_compact_ratio=context_compact_ratio,
        tool_result_max_chars=tool_result_max_chars,
        context_target_tokens=context_target_tokens,
        compaction_keep_recent_tools=compaction_keep_recent_tools,
        max_spawn_depth=max_spawn_depth,
        spawn_max_turns=spawn_max_turns,
        max_parallel_tool_calls=max_parallel_tool_calls,
    )


def _resolve_model_id(registry: ModelRegistry, model_id: Optional[str]) -> str:
    selected = (
        model_id
        or os.getenv("SYMPHONY_MODEL")
        or os.getenv("OPENAI_MODEL")
        or os.getenv("ANTHROPIC_MODEL")
        or os.getenv("GEMINI_MODEL")
        or os.getenv("GROK_MODEL")
        or os.getenv("XAI_MODEL")
        or os.getenv("OPENROUTER_MODEL")
        or os.getenv("VERCEL_MODEL")
        or os.getenv("AI_GATEWAY_MODEL")
        or os.getenv("OLLAMA_MODEL")
        or os.getenv("LOCAL_MODEL")
    )
    if selected:
        return selected if ":" in selected else _qualify_model(selected)
    from core_ai.providers.defaults import default_model_id

    try:
        return default_model_id(registry)
    except RuntimeError:
        return "openai:gpt-5.6-luna"


def _qualify_model(model_name: str) -> str:
    if model_name.startswith("claude-"):
        return f"anthropic:{model_name}"
    if model_name.startswith("gemini-"):
        return f"gemini:{model_name}"
    if model_name.startswith("grok-"):
        return f"grok:{model_name}"
    return f"openai:{model_name}"


def _default_registry() -> ModelRegistry:
    from core_ai.providers.defaults import build_default_registry

    return build_default_registry()
