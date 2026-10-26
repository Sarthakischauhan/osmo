"""The slice of the official Infisical SDK (`infisicalsdk`) that Osmo uses, typed as Protocols.

The SDK ships no type information; these describe exactly the calls `InfisicalSecretStore` and
`InfisicalSession` make, so tests can inject a fake client.
"""

from __future__ import annotations

from collections.abc import Sequence
from dataclasses import dataclass
from typing import Protocol

from pydantic import SecretStr

DEFAULT_SITE_URL = "https://app.infisical.com"
DEFAULT_CACHE_TTL_S = 60.0


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


class LoginResponse(Protocol):
    @property
    def expiresIn(self) -> int: ...


class UniversalAuthApi(Protocol):
    def login(self, *, client_id: str, client_secret: str) -> LoginResponse: ...


class AuthApi(Protocol):
    @property
    def universal_auth(self) -> UniversalAuthApi: ...


class InfisicalClient(Protocol):
    """The slice of `infisical_sdk.InfisicalSDKClient` Osmo uses."""

    @property
    def auth(self) -> AuthApi: ...

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
    # Seconds an owner's credentials are served from memory before Infisical is read again.
    cache_ttl_s: float = DEFAULT_CACHE_TTL_S
