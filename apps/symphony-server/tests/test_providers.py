"""Provider credentials: key validation, OAuth (PKCE + state), locking, and that secrets never leak."""

from __future__ import annotations

import base64
import hashlib
import json
import logging
from dataclasses import replace
from pathlib import Path
from urllib.parse import parse_qs, urlsplit

from typing import Any

import httpx
import pytest
from cryptography.fernet import Fernet
from fastapi.testclient import TestClient

from conftest import make_client
from fakes import FAKE_CONFIG, FakeInfisical, FakeSecretStore
from symphony_server.infisical_store import InfisicalSecretStore
from symphony_server.providers import PROVIDERS
from symphony_server.secret_store import SecretStore
from symphony_server.settings import Settings

GOOD_KEY = "sk-good-0123456789-secret"
BAD_KEY = "sk-bad-0123456789-secret"
OAUTH_KEY = "sk-or-v1-oauth-minted-secret"
OAUTH_CODE = "auth-code-123"


class FakeProviders:
    """Answers the providers' validation and OAuth token endpoints; records every request."""

    def __init__(self) -> None:
        self.requests: list[httpx.Request] = []
        self.challenge = ""

    def __call__(self, request: httpx.Request) -> httpx.Response:
        self.requests.append(request)
        if request.method == "GET" and request.url.path in ("/v1/models", "/api/v1/key", "/v1/credits"):
            ok = request.headers.get("Authorization") == f"Bearer {GOOD_KEY}"
            return httpx.Response(200, json={"data": []}) if ok else httpx.Response(401, json={"error": "invalid key"})
        if request.method == "GET" and str(request.url) == "https://generativelanguage.googleapis.com/v1beta/models":
            ok = request.headers.get("x-goog-api-key") == GOOD_KEY and "Authorization" not in request.headers
            return httpx.Response(200, json={"models": []}) if ok else httpx.Response(400, json={"error": "invalid key"})
        if request.method == "POST" and str(request.url) == "https://openrouter.ai/api/v1/auth/keys":
            body = json.loads(request.content)
            digest = hashlib.sha256(body["code_verifier"].encode()).digest()
            matches = base64.urlsafe_b64encode(digest).rstrip(b"=").decode() == self.challenge
            if body["code"] == OAUTH_CODE and matches and body["code_challenge_method"] == "S256":
                return httpx.Response(200, json={"key": OAUTH_KEY})
            return httpx.Response(403, json={"error": "Invalid code or code_verifier"})
        raise AssertionError(f"unexpected call to {request.url}")


@pytest.fixture
def fake() -> FakeProviders:
    return FakeProviders()


def settings(tmp_path: Path) -> Settings:
    return Settings(xai_base_url="http://127.0.0.1:9/v1", credentials_file=tmp_path / "creds.enc")


def connected(client_get: httpx.Response) -> dict[str, bool]:
    return {p["id"]: p["connected"] for p in client_get.json()["providers"]}


def test_providers_lists_auth_kinds_and_openrouter_alone_offers_oauth(tmp_path: Path, fake: FakeProviders) -> None:
    with make_client(settings(tmp_path), fake) as client:
        body = client.get("/providers").json()
    kinds = {p["id"]: p["authKinds"] for p in body["providers"]}
    assert kinds == {"xai": ["api_key"], "openai": ["api_key"], "openrouter": ["api_key", "oauth"], "vercel": ["api_key"], "gemini": ["api_key"]}
    assert all(p["connected"] is False for p in body["providers"])
    assert set(body["providers"][0]) == {"id", "label", "authKinds", "connected"}


def test_key_validation_failure_stores_nothing(tmp_path: Path, fake: FakeProviders) -> None:
    with make_client(settings(tmp_path), fake) as client:
        response = client.post("/providers/openai/key", json={"key": BAD_KEY})
        assert response.status_code == 400
        assert "rejected" in response.json()["detail"]
        assert connected(client.get("/providers"))["openai"] is False
    assert [str(r.url) for r in fake.requests] == ["https://api.openai.com/v1/models"]
    assert not (tmp_path / "creds.enc").exists()


def test_valid_key_connects_unlocks_models_and_delete_disconnects(tmp_path: Path, fake: FakeProviders) -> None:
    with make_client(settings(tmp_path), fake) as client:
        models = client.get("/voice-models").json()["models"]
        assert models and all(m["locked"] is True for m in models)
        assert client.post("/providers/xai/key", json={"key": GOOD_KEY}).status_code == 204
        assert connected(client.get("/providers"))["xai"] is True
        assert unlocked(client.get("/voice-models").json()["models"]) == {"xai"}
        with client.websocket_connect("/ws") as ws:
            event = json.loads(ws.receive_text())
            assert event["type"] == "models" and unlocked(event["models"]) == {"xai"}
        assert client.delete("/providers/xai").status_code == 204
        assert connected(client.get("/providers"))["xai"] is False
        with client.websocket_connect("/ws") as ws:
            event = json.loads(ws.receive_text())
            assert all(m["locked"] is True for m in event["models"])


def unlocked(models: list[dict[str, Any]]) -> set[str]:
    return {m["provider"] for m in models if not m["locked"]}


def test_voice_models_serve_openai_and_gateway_models_unlocked_by_their_keys(tmp_path: Path, fake: FakeProviders) -> None:
    with make_client(settings(tmp_path), fake) as client:
        models = client.get("/voice-models").json()["models"]
        served = {(m["provider"], m["id"]) for m in models}
        assert {("openai", "gpt-realtime"), ("openai", "gpt-realtime-mini"), ("vercel", "openai/gpt-realtime-2")} <= served
        assert unlocked(models) == set()
        gpt = next(m for m in models if m["id"] == "gpt-realtime-2.1")
        assert (gpt["label"], gpt["shortLabel"], gpt["defaultVoice"]) == ("Gpt Realtime 2.1", "2.1", "alloy")
        assert {"marin", "cedar"} <= {v["id"] for v in gpt["voices"]}
        assert client.post("/providers/openai/key", json={"key": GOOD_KEY}).status_code == 204
        assert unlocked(client.get("/voice-models").json()["models"]) == {"openai"}
        assert client.post("/providers/vercel/key", json={"key": GOOD_KEY}).status_code == 204
        assert unlocked(client.get("/voice-models").json()["models"]) == {"openai", "vercel"}


def test_gemini_key_is_checked_in_its_own_header(tmp_path: Path, fake: FakeProviders) -> None:
    with make_client(settings(tmp_path), fake) as client:
        assert client.post("/providers/gemini/key", json={"key": BAD_KEY}).status_code == 400
        assert client.post("/providers/gemini/key", json={"key": GOOD_KEY}).status_code == 204
        assert connected(client.get("/providers"))["gemini"] is True


def test_xai_api_key_in_the_environment_is_not_a_credential(
    tmp_path: Path, fake: FakeProviders, monkeypatch: pytest.MonkeyPatch
) -> None:
    """Keys come only from the SecretStore (the provider auth flow), never from the environment."""
    monkeypatch.setenv("XAI_API_KEY", "env-key-secret")
    with make_client(replace(Settings.from_env(), credentials_file=tmp_path / "creds.enc"), fake) as client:
        assert connected(client.get("/providers"))["xai"] is False
        assert all(m["locked"] is True for m in client.get("/voice-models").json()["models"])


def test_unknown_provider_is_404(tmp_path: Path, fake: FakeProviders) -> None:
    with make_client(settings(tmp_path), fake) as client:
        assert client.post("/providers/nope/key", json={"key": GOOD_KEY}).status_code == 404
        assert client.delete("/providers/nope").status_code == 404


def start_oauth(client: TestClient, fake: FakeProviders) -> dict[str, str]:
    response = client.get("/providers/openrouter/oauth/start", follow_redirects=False)
    assert response.status_code == 302
    location = urlsplit(response.headers["location"])
    assert f"{location.scheme}://{location.netloc}{location.path}" == "https://openrouter.ai/auth"
    query = {k: v[0] for k, v in parse_qs(location.query).items()}
    assert query["code_challenge_method"] == "S256" and query["state"]
    assert urlsplit(query["callback_url"]).path == "/providers/openrouter/oauth/callback"
    fake.challenge = query["code_challenge"]
    return query


def test_oauth_pkce_round_trip_stores_the_minted_key(tmp_path: Path, fake: FakeProviders) -> None:
    with make_client(settings(tmp_path), fake) as client:
        query = start_oauth(client, fake)
        done = client.get(
            "/providers/openrouter/oauth/callback", params={"code": OAUTH_CODE, "state": query["state"]}, follow_redirects=False
        )
        assert done.status_code == 303 and done.headers["location"] == "/#connected=openrouter"
        assert connected(client.get("/providers"))["openrouter"] is True
        # The state is single-use.
        again = client.get("/providers/openrouter/oauth/callback", params={"code": OAUTH_CODE, "state": query["state"]})
        assert again.status_code == 400


def test_oauth_state_mismatch_is_rejected_before_any_token_exchange(tmp_path: Path, fake: FakeProviders) -> None:
    with make_client(settings(tmp_path), fake) as client:
        start_oauth(client, fake)
        response = client.get("/providers/openrouter/oauth/callback", params={"code": OAUTH_CODE, "state": "forged"})
        assert response.status_code == 400
        assert "state mismatch" in response.json()["detail"]
        assert connected(client.get("/providers"))["openrouter"] is False
    assert not any(r.method == "POST" for r in fake.requests)


def test_oauth_is_refused_for_api_key_only_providers(tmp_path: Path, fake: FakeProviders) -> None:
    with make_client(settings(tmp_path), fake) as client:
        for spec in PROVIDERS:
            if spec.oauth is None:
                response = client.get(f"/providers/{spec.id}/oauth/start", follow_redirects=False)
                assert response.status_code == 400


def test_secrets_never_appear_in_responses_or_logs(
    tmp_path: Path, fake: FakeProviders, caplog: pytest.LogCaptureFixture
) -> None:
    caplog.set_level(logging.DEBUG)
    bodies: list[str] = []
    with make_client(settings(tmp_path), fake) as client:
        for response in (
            client.post("/providers/openai/key", json={"key": BAD_KEY}),
            client.post("/providers/xai/key", json={"key": GOOD_KEY}),
            client.post("/providers/openai/key", json={"key": GOOD_KEY}),
            client.get("/providers"),
            client.get("/voice-models"),
        ):
            bodies.append(response.text + json.dumps(dict(response.headers)))
        query = start_oauth(client, fake)
        for response in (
            client.get("/providers/openrouter/oauth/callback", params={"code": OAUTH_CODE, "state": query["state"]}, follow_redirects=False),
            client.get("/providers"),
            client.post("/providers/openrouter/key", json={}),
        ):
            bodies.append(response.text + json.dumps(dict(response.headers)))
        with client.websocket_connect("/ws") as ws:
            bodies.append(ws.receive_text())
    everything = "\n".join(bodies) + "\n" + caplog.text
    for secret in (GOOD_KEY, BAD_KEY, OAUTH_KEY):
        assert secret not in everything
    assert "xai connected with an API key" in caplog.text


def test_credentials_are_encrypted_at_rest_and_survive_a_restart(tmp_path: Path, fake: FakeProviders) -> None:
    path = tmp_path / "creds.enc"
    with make_client(settings(tmp_path), fake) as client:
        assert client.post("/providers/xai/key", json={"key": GOOD_KEY}).status_code == 204
    stored = path.read_bytes()
    assert GOOD_KEY.encode() not in stored and b"xai" not in stored and b"api_key" not in stored
    keyfile = tmp_path / "creds.enc.key"
    assert keyfile.is_file() and keyfile.stat().st_mode & 0o077 == 0
    with make_client(settings(tmp_path), fake) as client:
        assert connected(client.get("/providers"))["xai"] is True


def test_explicit_key_from_env_is_used_and_a_wrong_key_cannot_read_the_file(tmp_path: Path, fake: FakeProviders) -> None:
    key = Fernet.generate_key().decode()
    with make_client(Settings(credentials_file=tmp_path / "c.enc", credentials_key=key), fake) as client:
        assert client.post("/providers/xai/key", json={"key": GOOD_KEY}).status_code == 204
    assert not (tmp_path / "c.enc.key").exists()
    assert GOOD_KEY.encode() in Fernet(key.encode()).decrypt((tmp_path / "c.enc").read_bytes())
    other = Fernet.generate_key().decode()
    with make_client(Settings(credentials_file=tmp_path / "c.enc", credentials_key=other), fake) as client:
        assert connected(client.get("/providers"))["xai"] is False


def test_locked_models_cannot_be_selected(tmp_path: Path, fake: FakeProviders) -> None:
    with make_client(settings(tmp_path), fake) as client:
        with client.websocket_connect("/ws") as ws:
            json.loads(ws.receive_text())
            ws.send_text(json.dumps({"type": "session.configure", "model": "grok-voice-3.0", "voice": "eve"}))
            event = json.loads(ws.receive_text())
            assert event["type"] == "voice.configured" and "locked" in event["error"]


def test_vercel_key_validation_failure_stores_nothing(tmp_path: Path, fake: FakeProviders) -> None:
    with make_client(settings(tmp_path), fake) as client:
        response = client.post("/providers/vercel/key", json={"key": BAD_KEY})
        assert response.status_code == 400
        assert BAD_KEY not in response.text
        assert connected(client.get("/providers"))["vercel"] is False
    assert [str(r.url) for r in fake.requests] == ["https://ai-gateway.vercel.sh/v1/credits"]
    assert not (tmp_path / "creds.enc").exists()


def test_vercel_valid_key_connects_and_secret_never_appears_in_responses_or_logs(
    tmp_path: Path, fake: FakeProviders, caplog: pytest.LogCaptureFixture
) -> None:
    caplog.set_level(logging.DEBUG)
    with make_client(settings(tmp_path), fake) as client:
        saved = client.post("/providers/vercel/key", json={"key": GOOD_KEY})
        assert saved.status_code == 204
        listing = client.get("/providers")
        assert connected(listing)["vercel"] is True
        bodies = [saved.text, listing.text, client.get("/voice-models").text]
        assert client.delete("/providers/vercel").status_code == 204
        assert connected(client.get("/providers"))["vercel"] is False
    assert fake.requests[0].headers["Authorization"] == f"Bearer {GOOD_KEY}"
    assert all(GOOD_KEY not in body for body in bodies)
    assert GOOD_KEY not in caplog.text
    assert GOOD_KEY.encode() not in (tmp_path / "creds.enc").read_bytes()


@pytest.mark.parametrize("store_kind", ["infisical", "fake"])
def test_api_through_any_store_never_returns_or_logs_the_secret(
    store_kind: str, fake: FakeProviders, caplog: pytest.LogCaptureFixture
) -> None:
    caplog.set_level(logging.DEBUG)
    backend = FakeInfisical()
    store: SecretStore = (
        InfisicalSecretStore(backend, FAKE_CONFIG) if store_kind == "infisical" else FakeSecretStore()
    )
    with make_client(Settings(xai_base_url="http://127.0.0.1:9/v1"), fake, secret_store=store) as client:
        saved = client.post("/providers/openai/key", json={"key": GOOD_KEY})
        listing = client.get("/providers")
        bodies = [saved.text, listing.text, client.get("/voice-models").text]
        assert saved.status_code == 204 and connected(listing)["openai"] is True
        assert set(listing.json()["providers"][0]) == {"id", "label", "authKinds", "connected"}
        assert client.delete("/providers/openai").status_code == 204
        assert connected(client.get("/providers"))["openai"] is False
    assert all(GOOD_KEY not in body for body in bodies)
    assert GOOD_KEY not in caplog.text
