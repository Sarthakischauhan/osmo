"""What the agent browser may reach on the network: the public internet, never this machine or its local network.

A page (or a redirect, a subresource, a worker) aimed at `localhost`, a `.local` name, a loopback, private
(RFC 1918, unique-local), link-local (169.254/16, fe80::/10, so cloud metadata too), shared (100.64/10),
unspecified, reserved or multicast address is refused. Names are checked on the addresses they resolve to, so a
public name pointing at 127.0.0.1 is refused like 127.0.0.1 itself. `BrowserSession` asks before every request
Chrome makes (CDP Fetch interception, which pauses each redirect hop as a request of its own), and
`browser_open` asks before it navigates.

Hosts are compared in one form (`host_names.normalize_host`: lower case, no trailing dot, punycode), so
`LOCALHOST.`, `localhost` and an internationalised spelling of a name are the same host.

Two gaps stay, both outside what CDP can intercept: a name whose DNS answer changes between this check and
Chrome's own lookup (DNS rebinding with a zero TTL), and WebSocket handshakes (the Fetch domain does not pause
them). Osmo's own server refuses both on its side (`local_access`: Host allowlist, Origin check, app token).
"""

from __future__ import annotations

import asyncio
import ipaddress
import socket
import time
from collections.abc import Awaitable, Callable
from urllib.parse import urlsplit

from symphony_server.host_names import normalize_host

# The schemes that reach the network; the rest (about:, data:, blob:, chrome-extension:) never leave Chrome.
NETWORK_SCHEMES = frozenset({"http", "https", "ws", "wss"})
# Names that are this machine or its link by definition, whatever DNS says.
LOCAL_SUFFIXES = (".localhost", ".local", ".internal", ".home.arpa")
LOCAL_NAMES = frozenset({"localhost", "local", "internal", "home.arpa"})
# How long a host's verdict is kept (a page loads dozens of resources from the same few hosts).
VERDICT_TTL_S = 30.0

IpAddress = ipaddress.IPv4Address | ipaddress.IPv6Address
Resolve = Callable[[str], Awaitable[list[str]]]


async def system_resolve(host: str) -> list[str]:
    """The addresses `host` resolves to here (what Chrome will connect to); an OSError when it does not resolve."""
    infos = await asyncio.get_running_loop().getaddrinfo(host, None, type=socket.SOCK_STREAM)
    return [str(info[4][0]) for info in infos]


def private_address(address: IpAddress) -> bool:
    """Whether an address is anything but a public unicast one (an IPv4-mapped IPv6 address is judged as its IPv4)."""
    if isinstance(address, ipaddress.IPv6Address) and address.ipv4_mapped is not None:
        address = address.ipv4_mapped
    return not address.is_global or address.is_multicast


def literal_address(host: str) -> IpAddress | None:
    """`host` as an IP address, when it is one (IPv6 without its brackets, as urlsplit gives it)."""
    try:
        return ipaddress.ip_address(host.split("%", 1)[0])
    except ValueError:
        return None


class NetworkGuard:
    """Decides whether the browser may make a request; `resolve` defaults to this machine's resolver."""

    def __init__(self, resolve: Resolve | None = None, clock: Callable[[], float] = time.monotonic) -> None:
        self.resolve, self.clock = resolve, clock
        self.verdicts: dict[str, tuple[float, str | None]] = {}

    async def refusal(self, url: str) -> str | None:
        """Why the browser may not request `url`, or None when it may."""
        parts = urlsplit(url)
        if parts.scheme.lower() not in NETWORK_SCHEMES:
            return None
        try:
            host = normalize_host(parts.hostname or "")
        except ValueError:
            return f"{url} has a host name that is not valid"
        if not host:
            return f"{url} has no host"
        cached = self.verdicts.get(host)
        if cached is not None and self.clock() - cached[0] < VERDICT_TTL_S:
            return cached[1]
        verdict = await self.judge(host)
        self.verdicts[host] = (self.clock(), verdict)
        return verdict

    async def judge(self, host: str) -> str | None:
        """Why `host` (normalised) is off limits, or None for a public host."""
        if host in LOCAL_NAMES or host.endswith(LOCAL_SUFFIXES):
            return f"{host} is a local name: the browser only opens sites on the public internet"
        literal = literal_address(host)
        if literal is not None:
            return f"{host} is a private or local address: the browser only opens sites on the public internet" if private_address(literal) else None
        resolve = self.resolve or system_resolve
        try:
            addresses = [found for found in (literal_address(text) for text in await resolve(host)) if found is not None]
        except OSError:
            return f"{host} does not resolve"
        if not addresses:
            return f"{host} does not resolve"
        if any(private_address(address) for address in addresses):
            return f"{host} points at a private or local address: the browser only opens sites on the public internet"
        return None
