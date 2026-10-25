"""Infisical token renewal (proactive and on 401) and the per-owner read cache, on a fake clock."""

from __future__ import annotations

import logging

import pytest
from infisical_sdk.infisical_requests import APIError

from conftest import VOICE_LIMITS, make_client
from fakes import FAKE_CONFIG, FakeClock, FakeInfisical
from symphony_server.infisical_store import InfisicalSecretStore
from symphony_server.secret_store import DEFAULT_OWNER, ProviderAuth, SecretStoreError
from symphony_server.settings import Settings

SECRET = "sk-session-0123456789-secret"
READ_CALLS = ["list /", "list /osmo", "list /osmo/default", "get /osmo/default/openai/KEY"]


def auth(secret: str = SECRET) -> ProviderAuth:
    return ProviderAuth.model_validate({"provider": "openai", "kind": "api_key", "secret": secret})


def stocked() -> tuple[FakeInfisical, FakeClock, InfisicalSecretStore]:
    backend, clock = FakeInfisical(), FakeClock()
    InfisicalSecretStore(backend, FAKE_CONFIG, clock).put(DEFAULT_OWNER, auth())
    store = InfisicalSecretStore(backend, FAKE_CONFIG, clock)
    backend.calls.clear()
    backend.logins = 0
    return backend, clock, store


def test_logs_in_once_and_again_just_before_the_token_expires() -> None:
    backend, clock, store = stocked()
    store.list_providers(DEFAULT_OWNER)
    assert backend.logins == 1
    clock.now += 3600 - 61
    store.cache.clear()
    store.list_providers(DEFAULT_OWNER)
    assert backend.logins == 1
    clock.now += 2
    store.cache.clear()
    store.list_providers(DEFAULT_OWNER)
    assert backend.logins == 2


def test_short_tokens_refresh_ten_percent_early() -> None:
    backend, clock, store = stocked()
    backend.expires_in = 100
    store.list_providers(DEFAULT_OWNER)
    clock.now += 91
    store.cache.clear()
    store.list_providers(DEFAULT_OWNER)
    assert backend.logins == 2


def test_a_401_logs_in_again_and_retries_once() -> None:
    backend, _, store = stocked()
    store.session.login()
    backend.unauthorized = 1
    assert store.get(DEFAULT_OWNER, "openai") == auth()
    assert backend.logins == 2
    assert backend.calls == ["list /", *READ_CALLS]


def test_a_second_401_is_raised(caplog: pytest.LogCaptureFixture) -> None:
    caplog.set_level(logging.DEBUG)
    backend, _, store = stocked()
    backend.unauthorized = 2
    with pytest.raises(APIError):
        store.session.call(lambda: backend.list_folders(project_id="proj-1", environment_slug="prod", path="/"))
    assert backend.logins == 2
    backend.unauthorized = 2
    with pytest.raises(SecretStoreError) as raised:
        store.get(DEFAULT_OWNER, "openai")
    assert SECRET not in str(raised.value) and "machine-secret-value" not in caplog.text + str(raised.value)


def test_a_second_401_on_write_is_a_502_without_leaks(caplog: pytest.LogCaptureFixture) -> None:
    caplog.set_level(logging.DEBUG)
    backend, _, store = stocked()
    with make_client(Settings(voice_context_limits=VOICE_LIMITS, xai_base_url="http://127.0.0.1:9/v1"), secret_store=store) as client:
        assert client.get("/providers").status_code == 200
        backend.unauthorized = 2
        response = client.delete("/providers/openai")
    assert response.status_code == 502
    assert SECRET not in response.text + caplog.text and "machine-secret-value" not in caplog.text


def test_cache_serves_hits_within_ttl_and_refetches_after() -> None:
    backend, clock, store = stocked()
    for _ in range(3):
        assert store.get(DEFAULT_OWNER, "openai") == auth()
    assert backend.calls == READ_CALLS
    clock.now += FAKE_CONFIG.cache_ttl_s - 1
    store.get(DEFAULT_OWNER, "openai")
    assert backend.calls == READ_CALLS
    clock.now += 2
    store.get(DEFAULT_OWNER, "openai")
    assert backend.calls == READ_CALLS * 2


def test_put_and_delete_invalidate_the_cache() -> None:
    backend, _, store = stocked()
    other = InfisicalSecretStore(backend, FAKE_CONFIG, FakeClock())
    assert store.get(DEFAULT_OWNER, "openai") == auth()
    other.put(DEFAULT_OWNER, auth("sk-elsewhere"))
    assert store.get(DEFAULT_OWNER, "openai") == auth()
    store.put(DEFAULT_OWNER, auth("sk-new"))
    assert DEFAULT_OWNER not in store.cache
    assert store.get(DEFAULT_OWNER, "openai") == auth("sk-new")
    store.delete(DEFAULT_OWNER, "openai")
    assert DEFAULT_OWNER not in store.cache
    assert store.get(DEFAULT_OWNER, "openai") is None
