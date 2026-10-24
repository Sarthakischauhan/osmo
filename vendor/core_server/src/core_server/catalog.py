"""Map the live core_ai registry to the public model-picker protocol."""

from __future__ import annotations

from core_ai.models import ModelInfo
from core_ai.providers.catalog import get_provider

from core_server.config import ServerConfig
from core_server.models import (
    ModelRegistryResponse,
    RegistryModel,
    RegistryProvider,
    ThinkingLevel,
)


def is_accepted_model(config: ServerConfig, model_id: str) -> bool:
    """Accept a qualified model routed by one of the registered providers."""
    provider, separator, _model = model_id.partition(":")
    if not separator or provider not in config.registry.namespaces():
        return False
    return not config.supported_models or any(
        model.slug == model_id for model in config.supported_models
    )


def model_registry_response(config: ServerConfig) -> ModelRegistryResponse:
    """Map the live core_ai registry to the public model-picker response."""
    providers = [
        provider
        for provider_id in config.registry.namespaces()
        if (provider := _provider_response(config, provider_id)) is not None
    ]
    selected_provider = config.model_id.partition(":")[0]
    default_provider = next(
        (provider.id for provider in providers if provider.id == selected_provider),
        providers[0].id if providers else "",
    )
    return ModelRegistryResponse(
        default_provider_id=default_provider,
        providers=providers,
    )


def _provider_response(
    config: ServerConfig,
    provider_id: str,
) -> RegistryProvider | None:
    models = _registry_models(config, provider_id)
    if not models:
        return None

    label, default_model = _provider_metadata(provider_id)
    model_ids = {model.id for model in models}
    if config.model_id.startswith(f"{provider_id}:"):
        default_model = config.model_id
    if default_model not in model_ids:
        default_model = models[0].id

    return RegistryProvider(
        id=provider_id,
        label=label,
        logo=_provider_logo(provider_id),
        default_model=default_model,
        models=models,
    )


def _registry_models(config: ServerConfig, provider_id: str) -> list[RegistryModel]:
    catalog = {model.full_id: model for model in config.registry.models(provider_id)}
    if config.supported_models:
        choices = [
            (model.slug, model.label)
            for model in config.supported_models
            if model.slug.startswith(f"{provider_id}:")
        ]
    else:
        choices = [(slug, _model_label(model.id)) for slug, model in catalog.items()]
        if config.model_id.startswith(f"{provider_id}:") and config.model_id not in catalog:
            choices.append(
                (config.model_id, _model_label(config.model_id.partition(":")[2]))
            )

    return [
        RegistryModel(
            id=slug,
            label=label,
            thinking_levels=_thinking_levels(catalog.get(slug)),
        )
        for slug, label in choices
    ]


def _thinking_levels(model: ModelInfo | None) -> list[ThinkingLevel]:
    if model is None:
        return []
    return [
        "none" if level == "off" else level
        for level, provider_value in model.thinking_level_map
        if provider_value is not None
    ]


def _provider_metadata(provider_id: str) -> tuple[str, str]:
    try:
        provider = get_provider(provider_id)
        return provider.label, provider.default_model
    except KeyError:
        return provider_id.replace("-", " ").title(), ""


_MODEL_LABELS = {
    "gpt-4.1": "GPT-4.1",
    "claude-3-7-sonnet-20250219": "Claude 3.7 Sonnet",
}


def _model_label(model_id: str) -> str:
    if model_id in _MODEL_LABELS:
        return _MODEL_LABELS[model_id]
    words = model_id.replace("-", " ").replace("_", " ").split()
    return " ".join(
        word.upper()
        if word.lower() in {"gpt", "o1", "o3", "o4"}
        else word.capitalize()
        for word in words
    )


def _provider_logo(provider_id: str) -> str:
    logo_id = {"gemini": "google", "grok": "xai"}.get(provider_id, provider_id)
    return f"https://models.dev/logos/{logo_id}.svg"
