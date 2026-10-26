"""Pick the `SecretStore` named by `OSMO_SECRET_STORE` (`file`, the default, or `infisical`)."""

from __future__ import annotations

from symphony_server import infisical_store
from symphony_server.file_store import FileSecretStore
from symphony_server.secret_store import SecretStore
from symphony_server.settings import Settings


def open_secret_store(settings: Settings) -> SecretStore:
    if settings.secret_store == "file":
        return FileSecretStore(settings.credentials_file, settings.credentials_key)
    if settings.infisical is None:
        raise ValueError("OSMO_SECRET_STORE=infisical needs the INFISICAL_* settings")
    return infisical_store.connect(settings.infisical)
