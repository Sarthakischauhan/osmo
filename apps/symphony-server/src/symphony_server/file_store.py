"""`FileSecretStore`: the local default. One Fernet-encrypted JSON file holds every owner's credentials.

- `OSMO_CREDENTIALS_FILE`: the encrypted file (default `~/.config/osmo/credentials.enc`).
- `OSMO_CREDENTIALS_KEY`: a Fernet key. When unset, a key is generated once into
  `<OSMO_CREDENTIALS_FILE>.key` (mode 0600) and reused.
"""

from __future__ import annotations

import json
import logging
import os
from pathlib import Path

from cryptography.fernet import Fernet, InvalidToken
from pydantic import TypeAdapter

from symphony_server.secret_store import ProviderAuth

log = logging.getLogger("symphony.secrets.file")

_stored_adapter: TypeAdapter[dict[str, dict[str, ProviderAuth]]] = TypeAdapter(dict[str, dict[str, ProviderAuth]])


class FileSecretStore:
    """Credentials by owner, encrypted at rest in `path` (memory only when `path` is None)."""

    def __init__(self, path: Path | None, key: str | None = None) -> None:
        self.path = path
        self.fernet = Fernet(key.encode() if key else self.keyfile_key())
        self.owners: dict[str, dict[str, ProviderAuth]] = self.load()

    def keyfile_key(self) -> bytes:
        if self.path is None:
            return Fernet.generate_key()
        keyfile = self.path.with_name(self.path.name + ".key")
        if keyfile.is_file():
            return keyfile.read_bytes().strip()
        keyfile.parent.mkdir(parents=True, exist_ok=True)
        key = Fernet.generate_key()
        descriptor = os.open(keyfile, os.O_WRONLY | os.O_CREAT | os.O_EXCL, 0o600)
        with os.fdopen(descriptor, "wb") as handle:
            handle.write(key)
        return key

    def load(self) -> dict[str, dict[str, ProviderAuth]]:
        if self.path is None or not self.path.is_file():
            return {}
        try:
            return _stored_adapter.validate_json(self.fernet.decrypt(self.path.read_bytes()))
        except InvalidToken:
            log.error("credentials file %s could not be decrypted with the configured key; ignoring it", self.path)
            return {}

    def save(self) -> None:
        if self.path is None:
            return
        plain = {
            owner: {pid: {"provider": a.provider, "kind": a.kind, "secret": a.secret.get_secret_value()} for pid, a in auths.items()}
            for owner, auths in self.owners.items()
        }
        token = self.fernet.encrypt(json.dumps(plain).encode())
        self.path.parent.mkdir(parents=True, exist_ok=True)
        staging = self.path.with_name(self.path.name + ".tmp")
        descriptor = os.open(staging, os.O_WRONLY | os.O_CREAT | os.O_TRUNC, 0o600)
        with os.fdopen(descriptor, "wb") as handle:
            handle.write(token)
        staging.replace(self.path)

    def get(self, owner: str, provider: str) -> ProviderAuth | None:
        return self.owners.get(owner, {}).get(provider)

    def put(self, owner: str, auth: ProviderAuth) -> None:
        self.owners.setdefault(owner, {})[auth.provider] = auth
        self.save()

    def delete(self, owner: str, provider: str) -> None:
        if self.owners.get(owner, {}).pop(provider, None) is not None:
            self.save()

    def list_providers(self, owner: str) -> list[str]:
        return sorted(self.owners.get(owner, {}))
