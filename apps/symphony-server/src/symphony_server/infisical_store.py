"""`InfisicalSecretStore`: credentials in an Infisical project, one folder per provider.

A credential lives at folder `/osmo/<owner>/<provider>` as the secret `KEY`; its auth kind
(`api_key` or `oauth`) is the secret's comment. Reads are served from memory, loaded once per owner;
writes go to Infisical first. The client is injected, so tests pass a fake; `connect` builds the
official SDK client (`infisicalsdk`) logged in with machine-identity universal auth.
"""

from __future__ import annotations

import logging
from collections.abc import Sequence
from dataclasses import dataclass
from typing import Protocol

from infisical_sdk import InfisicalError, InfisicalSDKClient
from pydantic import SecretStr, ValidationError

from symphony_server.secret_store import ROOT_FOLDER, ProviderAuth, SecretStoreError, owner_path, secret_path

log = logging.getLogger("symphony.secrets.infisical")

SECRET_NAME = "KEY"
DEFAULT_SITE_URL = "https://app.infisical.com"


class Secret(Protocol):
    @property
    def secretValue(self) -> str: ...

    @property
    def secretComment(self) -> str: ...


class Folder(Protocol):
    @property
    def name(self) -> str: ...


class FolderList(Protocol):
    @property
    def folders(self) -> Sequence[Folder]: ...


class SecretsApi(Protocol):
    def get_secret_by_name(self, *, secret_name: str, project_id: str, environment_slug: str, secret_path: str) -> Secret: ...

    def create_secret_by_name(
        self, *, secret_name: str, project_id: str, environment_slug: str, secret_path: str, secret_value: str, secret_comment: str
    ) -> Secret: ...

    def update_secret_by_name(
        self, *, current_secret_name: str, project_id: str, environment_slug: str, secret_path: str, secret_value: str, secret_comment: str
    ) -> Secret: ...


class FoldersApi(Protocol):
    def list_folders(self, *, project_id: str, environment_slug: str, path: str) -> FolderList: ...

    def create_folder(self, *, name: str, project_id: str, environment_slug: str, path: str) -> object: ...

    def delete_folder(
        self, *, folder_id_or_name: str, project_id: str, environment_slug: str, path: str, force_delete: bool
    ) -> object: ...


class InfisicalClient(Protocol):
    """The slice of `infisical_sdk.InfisicalSDKClient` this store uses."""

    @property
    def secrets(self) -> SecretsApi: ...

    @property
    def folders(self) -> FoldersApi: ...


@dataclass(frozen=True)
class InfisicalConfig:
    client_id: str
    client_secret: SecretStr
    project_id: str
    environment: str
    site_url: str = DEFAULT_SITE_URL


def connect(config: InfisicalConfig) -> InfisicalSecretStore:
    """The SDK client, logged in as the machine identity. SDK caching is off: the store caches."""
    client = InfisicalSDKClient(host=config.site_url, cache_ttl=None)
    try:
        client.auth.universal_auth.login(client_id=config.client_id, client_secret=config.client_secret.get_secret_value())
    except InfisicalError as exc:
        raise SecretStoreError(f"Infisical universal auth login failed ({type(exc).__name__})") from None
    log.info("Infisical store: project %s, environment %s", config.project_id, config.environment)
    return InfisicalSecretStore(client, config.project_id, config.environment)


class InfisicalSecretStore:
    def __init__(self, client: InfisicalClient, project_id: str, environment: str) -> None:
        self.client, self.project_id, self.environment = client, project_id, environment
        self.owners: dict[str, dict[str, ProviderAuth]] = {}

    def folder_names(self, path: str) -> set[str]:
        listing = self.client.folders.list_folders(project_id=self.project_id, environment_slug=self.environment, path=path)
        return {folder.name for folder in listing.folders}

    def ensure_folder(self, parent: str, name: str) -> None:
        if name not in self.folder_names(parent):
            self.client.folders.create_folder(name=name, project_id=self.project_id, environment_slug=self.environment, path=parent)

    def read(self, owner: str, provider: str) -> ProviderAuth:
        secret = self.client.secrets.get_secret_by_name(
            secret_name=SECRET_NAME, project_id=self.project_id, environment_slug=self.environment, secret_path=secret_path(owner, provider)
        )
        try:
            return ProviderAuth.model_validate({"provider": provider, "kind": secret.secretComment, "secret": secret.secretValue})
        except ValidationError:
            raise SecretStoreError(f"{secret_path(owner, provider)} has no valid auth kind in its comment") from None

    def auths(self, owner: str) -> dict[str, ProviderAuth]:
        cached = self.owners.get(owner)
        if cached is not None:
            return cached
        try:
            exists = ROOT_FOLDER in self.folder_names("/") and owner in self.folder_names(f"/{ROOT_FOLDER}")
            providers = self.folder_names(owner_path(owner)) if exists else set()
            loaded = {provider: self.read(owner, provider) for provider in sorted(providers)}
        except InfisicalError as exc:
            raise SecretStoreError(f"could not load {owner_path(owner)} from Infisical ({type(exc).__name__})") from None
        self.owners[owner] = loaded
        return loaded

    def get(self, owner: str, provider: str) -> ProviderAuth | None:
        return self.auths(owner).get(provider)

    def put(self, owner: str, auth: ProviderAuth) -> None:
        auths, path = self.auths(owner), secret_path(owner, auth.provider)
        value, comment = auth.secret.get_secret_value(), auth.kind
        try:
            if auth.provider in auths:
                self.client.secrets.update_secret_by_name(
                    current_secret_name=SECRET_NAME, project_id=self.project_id, environment_slug=self.environment,
                    secret_path=path, secret_value=value, secret_comment=comment,
                )
            else:
                self.ensure_folder("/", ROOT_FOLDER)
                self.ensure_folder(f"/{ROOT_FOLDER}", owner)
                self.ensure_folder(owner_path(owner), auth.provider)
                self.client.secrets.create_secret_by_name(
                    secret_name=SECRET_NAME, project_id=self.project_id, environment_slug=self.environment,
                    secret_path=path, secret_value=value, secret_comment=comment,
                )
        except InfisicalError as exc:
            raise SecretStoreError(f"could not write {path} to Infisical ({type(exc).__name__})") from None
        auths[auth.provider] = auth

    def delete(self, owner: str, provider: str) -> None:
        auths = self.auths(owner)
        if provider not in auths:
            return
        try:
            self.client.folders.delete_folder(
                folder_id_or_name=provider, project_id=self.project_id, environment_slug=self.environment,
                path=owner_path(owner), force_delete=True,
            )
        except InfisicalError as exc:
            raise SecretStoreError(f"could not delete {secret_path(owner, provider)} from Infisical ({type(exc).__name__})") from None
        del auths[provider]

    def list_providers(self, owner: str) -> list[str]:
        return sorted(self.auths(owner))
