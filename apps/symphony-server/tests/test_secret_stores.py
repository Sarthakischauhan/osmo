"""The `SecretStore` contract, run against every implementation, and how Infisical lays secrets out."""

from __future__ import annotations

import logging
from collections.abc import Callable
from pathlib import Path

import pytest
from pydantic import SecretStr

from conftest import make_client
from fakes import FAKE_CONFIG, FakeInfisical, FakeSecretStore
from symphony_server.file_store import FileSecretStore
from symphony_server.infisical_client import InfisicalConfig
from symphony_server.infisical_store import InfisicalSecretStore
from symphony_server.secret_store import DEFAULT_OWNER, ProviderAuth, SecretStore, secret_path
from symphony_server.settings import Settings

SECRET = "sk-contract-0123456789-secret"
Reopen = Callable[[], SecretStore]


def file_store(tmp_path: Path) -> Reopen:
    return lambda: FileSecretStore(tmp_path / "creds.enc")


def infisical_store(tmp_path: Path) -> Reopen:
    backend = FakeInfisical()
    return lambda: InfisicalSecretStore(backend, FAKE_CONFIG)


def fake_store(tmp_path: Path) -> Reopen:
    store = FakeSecretStore()
    return lambda: store


@pytest.fixture(params=[file_store, infisical_store, fake_store], ids=["file", "infisical", "fake"])
def reopen(request: pytest.FixtureRequest, tmp_path: Path) -> Reopen:
    factory: Callable[[Path], Reopen] = request.param
    return factory(tmp_path)


def auth(provider: str = "openai", secret: str = SECRET, kind: str = "api_key") -> ProviderAuth:
    return ProviderAuth.model_validate({"provider": provider, "kind": kind, "secret": secret})


def test_empty_store_has_nothing(reopen: Reopen) -> None:
    store = reopen()
    assert store.get(DEFAULT_OWNER, "openai") is None
    assert store.list_providers(DEFAULT_OWNER) == []


def test_put_get_overwrite_and_survive_a_reopen(reopen: Reopen) -> None:
    store = reopen()
    store.put(DEFAULT_OWNER, auth())
    store.put(DEFAULT_OWNER, auth("openrouter", "sk-or-1", "oauth"))
    store.put(DEFAULT_OWNER, auth(secret="sk-replaced"))
    for current in (store, reopen()):
        assert current.list_providers(DEFAULT_OWNER) == ["openai", "openrouter"]
        assert current.get(DEFAULT_OWNER, "openai") == auth(secret="sk-replaced")
        assert current.get(DEFAULT_OWNER, "openrouter") == auth("openrouter", "sk-or-1", "oauth")


def test_delete_removes_and_tolerates_missing(reopen: Reopen) -> None:
    store = reopen()
    store.put(DEFAULT_OWNER, auth())
    store.delete(DEFAULT_OWNER, "openai")
    store.delete(DEFAULT_OWNER, "never-stored")
    assert store.get(DEFAULT_OWNER, "openai") is None
    assert reopen().list_providers(DEFAULT_OWNER) == []


def test_owners_are_isolated(reopen: Reopen) -> None:
    store = reopen()
    store.put("alice", auth())
    assert store.get(DEFAULT_OWNER, "openai") is None
    assert reopen().list_providers("alice") == ["openai"]
    store.delete(DEFAULT_OWNER, "openai")
    assert reopen().get("alice", "openai") == auth()


def test_secret_never_shows_in_a_repr(reopen: Reopen) -> None:
    store = reopen()
    store.put(DEFAULT_OWNER, auth())
    assert SECRET not in repr(store.get(DEFAULT_OWNER, "openai"))
    assert SECRET not in repr(store)


def test_infisical_uses_one_folder_per_owner_and_provider() -> None:
    backend = FakeInfisical()
    store = InfisicalSecretStore(backend, FAKE_CONFIG)
    store.put(DEFAULT_OWNER, auth("xai"))
    assert secret_path(DEFAULT_OWNER, "xai") == "/osmo/default/xai"
    assert backend.folder_paths == {"/", "/osmo", "/osmo/default", "/osmo/default/xai"}
    assert backend.values[("/osmo/default/xai", "KEY")].secretComment == "api_key"
    store.delete(DEFAULT_OWNER, "xai")
    assert backend.folder_paths == {"/", "/osmo", "/osmo/default"} and backend.values == {}


def test_infisical_outage_is_a_502_and_leaks_nothing(caplog: pytest.LogCaptureFixture) -> None:
    caplog.set_level(logging.DEBUG)
    backend = FakeInfisical()
    store = InfisicalSecretStore(backend, FAKE_CONFIG)
    store.put(DEFAULT_OWNER, auth("xai"))
    backend.fail = True
    with make_client(Settings(xai_base_url="http://127.0.0.1:9/v1"), secret_store=store) as client:
        response = client.delete("/providers/xai")
        assert response.status_code == 502
        assert "connected" in client.get("/providers").text
    assert SECRET not in response.text and SECRET not in caplog.text


def test_infisical_config_secret_never_shows_in_a_repr() -> None:
    config = InfisicalConfig(client_id="id", client_secret=SecretStr("machine-secret"), project_id="p", environment="prod")
    assert "machine-secret" not in repr(config)


def test_env_selects_the_store_and_infisical_needs_its_variables(monkeypatch: pytest.MonkeyPatch) -> None:
    for name in ("OSMO_SECRET_STORE", "INFISICAL_CLIENT_ID", "INFISICAL_CLIENT_SECRET", "INFISICAL_PROJECT_ID", "INFISICAL_ENV", "INFISICAL_SITE_URL"):
        monkeypatch.delenv(name, raising=False)
    assert Settings.from_env().secret_store == "file"
    monkeypatch.setenv("OSMO_SECRET_STORE", "infisical")
    with pytest.raises(ValueError, match="INFISICAL_CLIENT_ID"):
        Settings.from_env()
    for name, value in (("INFISICAL_CLIENT_ID", "id"), ("INFISICAL_CLIENT_SECRET", "machine-secret"), ("INFISICAL_PROJECT_ID", "p"), ("INFISICAL_ENV", "prod")):
        monkeypatch.setenv(name, value)
    settings = Settings.from_env()
    assert settings.secret_store == "infisical" and settings.infisical is not None
    assert settings.infisical.site_url == "https://app.infisical.com"
    assert "machine-secret" not in repr(settings)
    monkeypatch.setenv("OSMO_SECRET_STORE", "vault")
    with pytest.raises(ValueError, match="OSMO_SECRET_STORE"):
        Settings.from_env()
