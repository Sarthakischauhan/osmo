"""`/agent-model`: which model background agents (run_agent) run on, chosen in the app.

Precedence: the model chosen here (stored in Storage), then `OSMO_AGENT_MODEL`, then automatic
selection (`AgentRuns.select_model`). The options are core_ai's catalog models of every provider that
can run text now, through a subscription or an API key; an xAI key offers only the text models it
lists (a voice-only key offers none)."""

from __future__ import annotations

import re
from typing import TYPE_CHECKING, Literal

from core_ai.models import list_models
from fastapi import APIRouter, HTTPException
from pydantic import BaseModel, ConfigDict

from symphony_server.providers import provider_label

if TYPE_CHECKING:
    from symphony_server.agents import AgentRuns

AGENT_MODEL_SETTING = "agent_model"

# Text providers in automatic-selection order, with their core_ai namespaces.
AGENT_PROVIDERS: tuple[tuple[str, str], ...] = (
    ("xai", "grok"),
    ("openai", "openai"),
    ("anthropic", "anthropic"),
    ("gemini", "gemini"),
    ("vercel", "vercel"),
)
# Catalog models that cannot run an agent's text-and-tools loop (images, audio, live, research agents).
NOT_AGENT = re.compile(r"image|realtime|live|tts|audio|embedding|computer-use|deep-research|customtools")
# Dated snapshots (`gpt-4o-2024-08-06`, `claude-haiku-4-5-20251001`) duplicate their alias.
SNAPSHOT = re.compile(r"-\d{4}-\d{2}-\d{2}$|-\d{8}$")

Source = Literal["chosen", "configured", "automatic"]
Via = Literal["subscription", "api_key"]


class ModelOption(BaseModel):
    model_config = ConfigDict(extra="forbid")

    id: str
    label: str
    provider: str
    providerLabel: str
    via: Via


class AgentModelState(BaseModel):
    model_config = ConfigDict(extra="forbid")

    # The model chosen in the app; None leaves it to OSMO_AGENT_MODEL or automatic selection.
    chosen: str | None
    # What the next run uses.
    effective: str
    source: Source
    options: list[ModelOption]
    # Why a connected provider offers no models (e.g. an xAI key limited to voice).
    notes: list[str]


class AgentModelRequest(BaseModel):
    model_config = ConfigDict(extra="forbid")

    model: str | None


def agent_models(namespace: str) -> list[str]:
    """`namespace`'s catalog models an agent can run on, newest-looking first."""
    names = {m.id for m in list_models(namespace) if not NOT_AGENT.search(m.id) and not SNAPSHOT.search(m.id)}
    return sorted(names, reverse=True)


async def model_options(runs: AgentRuns) -> tuple[list[ModelOption], list[str]]:
    access = runs.access
    options: list[ModelOption] = []
    notes: list[str] = []
    for provider, namespace in AGENT_PROVIDERS:
        if not await access.prepare(provider):
            continue
        via: Via = "subscription" if provider in access.tokens else "api_key"
        names = agent_models(namespace)
        if via == "api_key":
            listed = await access.key_text_models(provider)
            if listed is not None:
                names = [name for name in names if name in listed]
                if not names:
                    notes.append(f"{provider_label(provider)}: this API key has no text models (it may be limited to voice)")
        label = provider_label(provider)
        options.extend(ModelOption(id=f"{namespace}:{name}", label=name, provider=provider, providerLabel=label, via=via) for name in names)
    return options, notes


async def agent_model_state(runs: AgentRuns) -> AgentModelState:
    options, notes = await model_options(runs)
    source: Source = "chosen" if runs.chosen_model else "automatic" if runs.auto_model else "configured"
    return AgentModelState(chosen=runs.chosen_model, effective=await runs.select_model(), source=source, options=options, notes=notes)


def create_agent_model_router(runs: AgentRuns) -> APIRouter:
    router = APIRouter(prefix="/agent-model")

    @router.get("")
    async def get_agent_model() -> AgentModelState:
        return await agent_model_state(runs)

    @router.put("")
    async def set_agent_model(body: AgentModelRequest) -> AgentModelState:
        if body.model is not None:
            options, _ = await model_options(runs)
            if body.model not in {option.id for option in options}:
                raise HTTPException(400, f"{body.model} is not available; connect its provider first")
        runs.choose_model(body.model)
        return await agent_model_state(runs)

    return router
