"""Host names in the one form the browser's rules and its network guard compare them in."""

from __future__ import annotations


def normalize_host(host: str) -> str:
    """A host name in the one form rules and checks compare: lower case, no trailing dots, punycode (IDNA).

    An IP address stays as it is. ValueError for a name IDNA cannot encode (an empty or over-long label)."""
    name = host.strip().rstrip(".").lower()
    if not name or ":" in name or name.replace(".", "").isdigit():
        return name
    try:
        return name.encode("idna").decode("ascii")
    except UnicodeError as exc:
        raise ValueError(f"{host!r} is not a valid host name") from exc
