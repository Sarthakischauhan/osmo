"""In-memory stand-ins: a `SecretStore`, and the slice of the Infisical SDK client the store calls."""

from __future__ import annotations

from dataclasses import dataclass, field

from infisical_sdk import InfisicalError
from infisical_sdk.infisical_requests import APIError

from pydantic import SecretStr

from symphony_server.infisical_client import InfisicalConfig
from symphony_server.secret_store import ProviderAuth

FAKE_CONFIG = InfisicalConfig(
    client_id="machine-id", client_secret=SecretStr("machine-secret-value"), project_id="proj-1", environment="prod"
)


class FakeClock:
    def __init__(self) -> None:
        self.now = 1000.0

    def __call__(self) -> float:
        return self.now


class FakeSecretStore:
    def __init__(self) -> None:
        self.owners: dict[str, dict[str, ProviderAuth]] = {}

    def get(self, owner: str, provider: str) -> ProviderAuth | None:
        return self.owners.get(owner, {}).get(provider)

    def put(self, owner: str, auth: ProviderAuth) -> None:
        self.owners.setdefault(owner, {})[auth.provider] = auth

    def delete(self, owner: str, provider: str) -> None:
        self.owners.get(owner, {}).pop(provider, None)

    def list_providers(self, owner: str) -> list[str]:
        return sorted(self.owners.get(owner, {}))


@dataclass(frozen=True)
class FakeSecret:
    secretValue: str
    secretComment: str


@dataclass(frozen=True)
class FakeFolder:
    name: str


@dataclass(frozen=True)
class FakeFolderList:
    folders: list[FakeFolder]


def join(parent: str, name: str) -> str:
    return f"{parent.rstrip('/')}/{name}"


@dataclass(frozen=True)
class FakeLogin:
    expiresIn: int


@dataclass
class FakeInfisical:
    """Folders and secrets of one project environment. Like Infisical, writing needs an existing folder."""

    project_id: str = "proj-1"
    environment: str = "prod"
    folder_paths: set[str] = field(default_factory=lambda: {"/"})
    values: dict[tuple[str, str], FakeSecret] = field(default_factory=dict)
    calls: list[str] = field(default_factory=list)
    fail: bool = False
    # Token lifetime handed out by login, logins made, and how many upcoming calls answer 401.
    expires_in: int = 3600
    logins: int = 0
    unauthorized: int = 0

    def check(self, call: str, project_id: str, environment_slug: str) -> None:
        self.calls.append(call)
        assert (project_id, environment_slug) == (self.project_id, self.environment)
        if self.fail:
            raise InfisicalError("Infisical is down")
        if self.unauthorized > 0:
            self.unauthorized -= 1
            raise APIError("Token expired", 401, {"message": "Token expired"})

    @property
    def auth(self) -> FakeInfisical:
        return self

    @property
    def universal_auth(self) -> FakeInfisical:
        return self

    def login(self, *, client_id: str, client_secret: str) -> FakeLogin:
        assert (client_id, client_secret) == (FAKE_CONFIG.client_id, FAKE_CONFIG.client_secret.get_secret_value())
        self.logins += 1
        return FakeLogin(self.expires_in)

    @property
    def secrets(self) -> FakeInfisical:
        return self

    @property
    def folders(self) -> FakeInfisical:
        return self

    def get_secret_by_name(self, *, secret_name: str, project_id: str, environment_slug: str, secret_path: str) -> FakeSecret:
        self.check(f"get {secret_path}/{secret_name}", project_id, environment_slug)
        found = self.values.get((secret_path, secret_name))
        if found is None:
            raise InfisicalError("Secret not found")
        return found

    def create_secret_by_name(
        self, *, secret_name: str, project_id: str, environment_slug: str, secret_path: str, secret_value: str, secret_comment: str
    ) -> FakeSecret:
        self.check(f"create {secret_path}/{secret_name}", project_id, environment_slug)
        if secret_path not in self.folder_paths or (secret_path, secret_name) in self.values:
            raise InfisicalError("Folder not found or secret exists")
        self.values[(secret_path, secret_name)] = FakeSecret(secret_value, secret_comment)
        return self.values[(secret_path, secret_name)]

    def update_secret_by_name(
        self, *, current_secret_name: str, project_id: str, environment_slug: str, secret_path: str, secret_value: str, secret_comment: str
    ) -> FakeSecret:
        self.check(f"update {secret_path}/{current_secret_name}", project_id, environment_slug)
        if (secret_path, current_secret_name) not in self.values:
            raise InfisicalError("Secret not found")
        self.values[(secret_path, current_secret_name)] = FakeSecret(secret_value, secret_comment)
        return self.values[(secret_path, current_secret_name)]

    def list_folders(self, *, project_id: str, environment_slug: str, path: str) -> FakeFolderList:
        self.check(f"list {path}", project_id, environment_slug)
        children = [p for p in self.folder_paths if p != "/" and p.rsplit("/", 1)[0] == path.rstrip("/")]
        return FakeFolderList([FakeFolder(p.rsplit("/", 1)[1]) for p in sorted(children)])

    def create_folder(self, *, name: str, project_id: str, environment_slug: str, path: str) -> FakeFolder:
        self.check(f"mkdir {join(path, name)}", project_id, environment_slug)
        if path not in self.folder_paths or join(path, name) in self.folder_paths:
            raise InfisicalError("Parent folder missing or folder exists")
        self.folder_paths.add(join(path, name))
        return FakeFolder(name)

    def delete_folder(
        self, *, folder_id_or_name: str, project_id: str, environment_slug: str, path: str, force_delete: bool
    ) -> FakeFolder:
        target = join(path, folder_id_or_name)
        self.check(f"rmdir {target}", project_id, environment_slug)
        assert force_delete
        self.folder_paths = {p for p in self.folder_paths if p != target and not p.startswith(target + "/")}
        self.values = {k: v for k, v in self.values.items() if k[0] != target}
        return FakeFolder(folder_id_or_name)
