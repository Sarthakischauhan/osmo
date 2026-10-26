"""The `SecretStore` protocol: where provider credentials live, one set per owner.

Every credential is addressed as `/osmo/<owner>/<provider>`. The owner is always `DEFAULT_OWNER`
until Osmo has users; it is a parameter so per-user keys need no protocol change.
Implementations never log or return a secret anywhere but `get`.
"""

from __future__ import annotations

from typing import Literal, Protocol

from pydantic import BaseModel, ConfigDict, SecretStr

AuthKind = Literal["api_key", "oauth"]

DEFAULT_OWNER = "default"
ROOT_FOLDER = "osmo"


class ProviderAuth(BaseModel):
    """One provider's credential. `secret` is a `SecretStr`, so it never shows in a repr or log."""

    model_config = ConfigDict(extra="forbid", frozen=True)

    provider: str
    kind: AuthKind
    secret: SecretStr


class SecretStoreError(Exception):
    """A store could not be read or written. The message never contains a secret."""


def owner_path(owner: str) -> str:
    return f"/{ROOT_FOLDER}/{owner}"


def secret_path(owner: str, provider: str) -> str:
    return f"{owner_path(owner)}/{provider}"


class SecretStore(Protocol):
    def get(self, owner: str, provider: str) -> ProviderAuth | None: ...

    def put(self, owner: str, auth: ProviderAuth) -> None: ...

    def delete(self, owner: str, provider: str) -> None: ...

    def list_providers(self, owner: str) -> list[str]: ...
