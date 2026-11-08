"""`/agent-model`: choosing the background agent's model in the app."""

from __future__ import annotations

from pathlib import Path

import httpx
from pydantic import SecretStr

from conftest import make_client
from symphony_server.agent_model import agent_models
from symphony_server.file_store import FileSecretStore
from symphony_server.secret_store import DEFAULT_OWNER, ProviderAuth
from symphony_server.settings import Settings


def voice_only_xai(request: httpx.Request) -> httpx.Response:
    assert str(request.url) == "http://127.0.0.1:9/v1/language-models"
    return httpx.Response(200, json={"models": []})


def store() -> FileSecretStore:
    secrets = FileSecretStore(None)
    for provider in ("xai", "gemini"):
        secrets.put(DEFAULT_OWNER, ProviderAuth(provider=provider, kind="api_key", secret=SecretStr(f"{provider}-key")))
    return secrets


def settings(tmp_path: Path) -> Settings:
    return Settings(xai_base_url="http://127.0.0.1:9/v1", database_file=tmp_path / "osmo.db", agent_model_auto=True)


def test_options_skip_a_voice_only_key_and_a_choice_persists(tmp_path: Path) -> None:
    secrets = store()
    with make_client(settings(tmp_path), voice_only_xai, secret_store=secrets) as client:
        state = client.get("/agent-model").json()
        providers = {option["provider"] for option in state["options"]}
        assert providers == {"gemini"} and state["source"] == "automatic" and state["chosen"] is None
        assert state["effective"].startswith("gemini:")
        assert any("xAI" in note and "no text models" in note for note in state["notes"])
        assert all(option["via"] == "api_key" for option in state["options"])

        assert client.put("/agent-model", json={"model": "grok:grok-4.7"}).status_code == 400
        chosen = client.put("/agent-model", json={"model": "gemini:gemini-2.5-pro"}).json()
        assert chosen["chosen"] == chosen["effective"] == "gemini:gemini-2.5-pro" and chosen["source"] == "chosen"

    # Stored: a restarted server keeps the choice.
    with make_client(settings(tmp_path), voice_only_xai, secret_store=secrets) as client:
        assert client.get("/agent-model").json()["effective"] == "gemini:gemini-2.5-pro"
        reset = client.put("/agent-model", json={"model": None}).json()
        assert reset["chosen"] is None and reset["source"] == "automatic"


def test_the_option_list_leaves_out_models_an_agent_cannot_run() -> None:
    gemini = agent_models("gemini")
    assert "gemini-2.5-pro" in gemini
    assert not [name for name in gemini if "live" in name or "image" in name or "deep-research" in name]
    openai = agent_models("openai")
    assert "gpt-4o" in openai and "gpt-4o-2024-08-06" not in openai and "gpt-realtime-2.1" not in openai
