"""Sign in with Vercel: OAuth 2 authorization code + PKCE with a registered client, token refresh."""

from __future__ import annotations

import base64
import hashlib
import time
from pathlib import Path
from urllib.parse import parse_qs, urlsplit

import httpx
from pydantic import SecretStr

from conftest import make_client
from symphony_server.providers import CredentialStore, TokenRefresher, TokenSet, token_auth
from symphony_server.file_store import FileSecretStore
from symphony_server.settings import OAuthClient, Settings

TOKEN_URL = "https://api.vercel.com/login/oauth/token"
CLIENT = OAuthClient("cl_osmo", SecretStr("shh-client-secret"))


class FakeVercel:
    def __init__(self) -> None:
        self.forms: list[dict[str, list[str]]] = []
        self.challenge = ""

    def __call__(self, request: httpx.Request) -> httpx.Response:
        assert request.method == "POST" and str(request.url) == TOKEN_URL
        assert request.headers["content-type"] == "application/x-www-form-urlencoded"
        form = parse_qs(request.content.decode())
        self.forms.append(form)
        if form["grant_type"] == ["refresh_token"]:
            return httpx.Response(200, json={"access_token": "vat-2", "expires_in": 3600})
        digest = base64.urlsafe_b64encode(hashlib.sha256(form["code_verifier"][0].encode()).digest()).rstrip(b"=").decode()
        if form["code"] == ["vc-code"] and digest == self.challenge:
            return httpx.Response(200, json={"access_token": "vat-1", "refresh_token": "vrt-1", "expires_in": 3600})
        return httpx.Response(400, json={"error": "invalid_grant"})


def settings(tmp_path: Path, clients: dict[str, OAuthClient]) -> Settings:
    return Settings(xai_base_url="http://127.0.0.1:9/v1", credentials_file=tmp_path / "creds.enc", oauth_clients=clients)


def test_vercel_offers_oauth_only_with_a_registered_client(tmp_path: Path) -> None:
    with make_client(settings(tmp_path, {})) as client:
        kinds = {p["id"]: p["authKinds"] for p in client.get("/providers").json()["providers"]}
        assert kinds["vercel"] == ["api_key"]
        assert client.get("/providers/vercel/oauth/start", follow_redirects=False).status_code == 400


def test_vercel_sign_in_round_trip_stores_the_access_token(tmp_path: Path) -> None:
    fake = FakeVercel()
    with make_client(settings(tmp_path, {"vercel": CLIENT}), fake) as client:
        assert {p["id"]: p["authKinds"] for p in client.get("/providers").json()["providers"]}["vercel"] == ["api_key", "oauth"]
        start = client.get("/providers/vercel/oauth/start", follow_redirects=False)
        location = urlsplit(start.headers["location"])
        assert f"{location.scheme}://{location.netloc}{location.path}" == "https://vercel.com/oauth/authorize"
        query = {k: v[0] for k, v in parse_qs(location.query).items()}
        assert query["client_id"] == "cl_osmo" and query["response_type"] == "code"
        assert query["code_challenge_method"] == "S256" and query["scope"] == "openid offline_access"
        assert urlsplit(query["redirect_uri"]).path == "/providers/vercel/oauth/callback"
        fake.challenge = query["code_challenge"]

        denied = client.get("/providers/vercel/oauth/callback", params={"error": "access_denied", "state": "forged"})
        assert denied.status_code == 400
        done = client.get(
            "/providers/vercel/oauth/callback", params={"code": "vc-code", "state": query["state"]}, follow_redirects=False
        )
        assert done.status_code == 303
        form = fake.forms[-1]
        assert form["client_id"] == ["cl_osmo"] and form["client_secret"] == ["shh-client-secret"]
        assert form["redirect_uri"] == [query["redirect_uri"]] and form["grant_type"] == ["authorization_code"]
        assert {p["id"]: p["connected"] for p in client.get("/providers").json()["providers"]}["vercel"] is True
        assert "vat-1" not in client.get("/providers").text


async def test_a_token_about_to_expire_is_refreshed_with_the_refresh_token() -> None:
    fake = FakeVercel()
    store = CredentialStore(FileSecretStore(None))
    soon = TokenSet(access_token=SecretStr("vat-1"), refresh_token=SecretStr("vrt-1"), expires_at=time.time() + 30)
    store.save(token_auth("vercel", soon))
    async with httpx.AsyncClient(transport=httpx.MockTransport(fake)) as http:
        await TokenRefresher(store, http, {"vercel": CLIENT}).refresh_if_due("vercel")
    assert fake.forms[-1]["refresh_token"] == ["vrt-1"] and fake.forms[-1]["client_id"] == ["cl_osmo"]
    assert store.secret("vercel") == "vat-2"
    tokens = store.tokens("vercel")
    assert tokens is not None and tokens.refresh_token is not None and tokens.refresh_token.get_secret_value() == "vrt-1"
