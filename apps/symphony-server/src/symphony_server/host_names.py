"""Host names in the one form the browser's rules and its network guard compare them in."""

from __future__ import annotations

from urllib.parse import urlsplit

import idna

MAX_LABEL = 63


def normalize_host(host: str) -> str:
    """A host name in the one form rules and checks compare: lower case, no trailing dots, and an internationalised
    name as the punycode Chrome connects to (IDNA2008 with UTS #46 non-transitional mapping, so `faß.de` stays
    `xn--fa-hia.de` instead of IDNA2003's `fass.de`).

    An IP address stays as it is, and so does an ASCII name (Chrome takes `my_host` and other names IDNA2008 would
    refuse as they are). ValueError for a name that cannot be encoded (an empty or over-long label, a disallowed
    character in an internationalised name)."""
    name = host.strip().rstrip(".").lower()
    if not name or ":" in name or name.replace(".", "").isdigit():
        return name
    if name.isascii():
        if any(not 0 < len(label) <= MAX_LABEL for label in name.split(".")):
            raise ValueError(f"{host!r} is not a valid host name")
        return name
    try:
        return idna.encode(name, uts46=True, transitional=False).decode("ascii")
    except idna.IDNAError as exc:
        raise ValueError(f"{host!r} is not a valid host name") from exc


def site_of(url: str | None) -> str | None:
    """The normalised host of `url`: None without one (about:blank, a data: URL) or when it is not a valid name."""
    if not url:
        return None
    try:
        host = normalize_host(urlsplit(url).hostname or "")
    except ValueError:
        return None
    return host or None
