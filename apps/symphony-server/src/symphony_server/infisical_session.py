"""Machine-identity universal-auth login for the Infisical SDK client, kept fresh.

The access token's lifetime comes from the login response's `expiresIn`; the session logs in again
`REFRESH_MARGIN_S` (or 10% of the lifetime, whichever is shorter) before it runs out. A call that still
gets a 401 logs in again and is retried exactly once.
"""

from __future__ import annotations

import logging
from collections.abc import Callable
from typing import TypeVar

from infisical_sdk.infisical_requests import APIError

from symphony_server.infisical_client import InfisicalClient, InfisicalConfig

log = logging.getLogger("symphony.secrets.infisical")

REFRESH_MARGIN_S = 60.0
UNAUTHORIZED = 401

T = TypeVar("T")


class InfisicalSession:
    def __init__(self, client: InfisicalClient, config: InfisicalConfig, clock: Callable[[], float]) -> None:
        self.client, self.config, self.clock = client, config, clock
        self.refresh_at: float | None = None

    def login(self) -> None:
        response = self.client.auth.universal_auth.login(
            client_id=self.config.client_id, client_secret=self.config.client_secret.get_secret_value()
        )
        lifetime = float(response.expiresIn)
        self.refresh_at = self.clock() + lifetime - min(REFRESH_MARGIN_S, lifetime * 0.1)
        log.info("Infisical: logged in as the machine identity; token valid for %ds", response.expiresIn)

    def call(self, request: Callable[[], T]) -> T:
        """`request()` with a live token; on a 401, log in again and retry once."""
        if self.refresh_at is None or self.clock() >= self.refresh_at:
            self.login()
        try:
            return request()
        except APIError as exc:
            if exc.status_code != UNAUTHORIZED:
                raise
        log.warning("Infisical: token rejected (401); logging in again")
        self.login()
        return request()
