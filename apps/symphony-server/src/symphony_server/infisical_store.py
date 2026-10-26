"""`InfisicalSecretStore`: credentials in an Infisical project, one folder per provider.

A credential lives at folder `/osmo/<owner>/<provider>` as the secret `KEY`; its auth kind
(`api_key` or `oauth`) is the secret's comment. Each owner's credentials are cached in memory for
`InfisicalConfig.cache_ttl_s` and dropped on every put or delete. The client is injected, so tests
pass a fake; `connect` builds the official SDK client (`infisicalsdk`).
"""

from __future__ import annotations

import logging
import time
from collections.abc import Callable
from dataclasses import dataclass

from infisical_sdk import InfisicalError, InfisicalSDKClient
from pydantic import ValidationError

from symphony_server.infisical_client import InfisicalClient, InfisicalConfig
from symphony_server.infisical_session import InfisicalSession
from symphony_server.secret_store import ROOT_FOLDER, ProviderAuth, SecretStoreError, owner_path, secret_path

log = logging.getLogger("symphony.secrets.infisical")

SECRET_NAME = "KEY"


def connect(config: InfisicalConfig) -> InfisicalSecretStore:
    """A store on the SDK client. SDK caching is off: the store caches. Login happens on first use."""
    log.info("Infisical store: project %s, environment %s", config.project_id, config.environment)
    return InfisicalSecretStore(InfisicalSDKClient(host=config.site_url, cache_ttl=None), config)


@dataclass(frozen=True)
class CachedOwner:
    loaded: float
    auths: dict[str, ProviderAuth]


class InfisicalSecretStore:
    def __init__(self, client: InfisicalClient, config: InfisicalConfig, clock: Callable[[], float] = time.monotonic) -> None:
        self.client, self.config, self.clock = client, config, clock
        self.session = InfisicalSession(client, config, clock)
        self.cache: dict[str, CachedOwner] = {}

    def folder_names(self, path: str) -> set[str]:
        listing = self.session.call(
            lambda: self.client.folders.list_folders(project_id=self.config.project_id, environment_slug=self.config.environment, path=path)
        )
        return {folder.name for folder in listing.folders}

    def ensure_folder(self, parent: str, name: str) -> None:
        if name not in self.folder_names(parent):
            self.session.call(
                lambda: self.client.folders.create_folder(
                    name=name, project_id=self.config.project_id, environment_slug=self.config.environment, path=parent
                )
            )

    def read(self, owner: str, provider: str) -> ProviderAuth:
        secret = self.session.call(
            lambda: self.client.secrets.get_secret_by_name(
                secret_name=SECRET_NAME, project_id=self.config.project_id,
                environment_slug=self.config.environment, secret_path=secret_path(owner, provider),
            )
        )
        try:
            return ProviderAuth.model_validate({"provider": provider, "kind": secret.secretComment, "secret": secret.secretValue})
        except ValidationError:
            raise SecretStoreError(f"{secret_path(owner, provider)} has no valid auth kind in its comment") from None

    def auths(self, owner: str) -> dict[str, ProviderAuth]:
        cached = self.cache.get(owner)
        if cached is not None and self.clock() - cached.loaded < self.config.cache_ttl_s:
            return cached.auths
        try:
            exists = ROOT_FOLDER in self.folder_names("/") and owner in self.folder_names(f"/{ROOT_FOLDER}")
            providers = self.folder_names(owner_path(owner)) if exists else set()
            loaded = {provider: self.read(owner, provider) for provider in sorted(providers)}
        except InfisicalError as exc:
            raise SecretStoreError(f"could not load {owner_path(owner)} from Infisical ({type(exc).__name__})") from None
        self.cache[owner] = CachedOwner(self.clock(), loaded)
        return loaded

    def get(self, owner: str, provider: str) -> ProviderAuth | None:
        return self.auths(owner).get(provider)

    def put(self, owner: str, auth: ProviderAuth) -> None:
        self.cache.pop(owner, None)
        exists, path = auth.provider in self.auths(owner), secret_path(owner, auth.provider)
        value, comment, project, env = auth.secret.get_secret_value(), auth.kind, self.config.project_id, self.config.environment
        self.cache.pop(owner, None)
        try:
            if exists:
                self.session.call(
                    lambda: self.client.secrets.update_secret_by_name(
                        current_secret_name=SECRET_NAME, project_id=project, environment_slug=env,
                        secret_path=path, secret_value=value, secret_comment=comment,
                    )
                )
                return
            self.ensure_folder("/", ROOT_FOLDER)
            self.ensure_folder(f"/{ROOT_FOLDER}", owner)
            self.ensure_folder(owner_path(owner), auth.provider)
            self.session.call(
                lambda: self.client.secrets.create_secret_by_name(
                    secret_name=SECRET_NAME, project_id=project, environment_slug=env,
                    secret_path=path, secret_value=value, secret_comment=comment,
                )
            )
        except InfisicalError as exc:
            raise SecretStoreError(f"could not write {path} to Infisical ({type(exc).__name__})") from None

    def delete(self, owner: str, provider: str) -> None:
        self.cache.pop(owner, None)
        if provider not in self.auths(owner):
            return
        self.cache.pop(owner, None)
        try:
            self.session.call(
                lambda: self.client.folders.delete_folder(
                    folder_id_or_name=provider, project_id=self.config.project_id, environment_slug=self.config.environment,
                    path=owner_path(owner), force_delete=True,
                )
            )
        except InfisicalError as exc:
            raise SecretStoreError(f"could not delete {secret_path(owner, provider)} from Infisical ({type(exc).__name__})") from None

    def list_providers(self, owner: str) -> list[str]:
        return sorted(self.auths(owner))
