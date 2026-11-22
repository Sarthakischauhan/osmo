"""Per-domain rules for the agent browser (OSMO_BROWSER_RULES).

Each rule names a site and what the agent may do there:

- `allow`: clicking, typing and pressing keys go ahead without asking (a site the user trusts the agent on);
- `ask`: the default; the agent's steps wait for approval as `OSMO_APPROVAL_TOOLS` says, and clicking, typing
  and pressing keys always wait;
- `deny`: the agent may not open the site or act on it at all.

`github.com=allow` covers github.com and every subdomain; `*.github.com=allow` only the subdomains. The most
specific (longest) matching pattern wins, so `example.com=allow,pay.example.com=deny` denies the payment site.

Patterns and hosts are compared in one form (`normalize_host`): lower case, without a trailing dot, and
internationalised names as punycode, so `Example.COM.` and `xn--bcher-kva.de` / `bücher.de` match their rules.
"""

from __future__ import annotations

import re
from dataclasses import dataclass, field
from typing import Literal, get_args
from urllib.parse import urlsplit

from symphony_server.host_names import normalize_host

DomainRule = Literal["allow", "ask", "deny"]
DOMAIN_RULES: tuple[DomainRule, ...] = get_args(DomainRule)
PATTERN = re.compile(r"^(\*\.)?[a-z0-9-]+(\.[a-z0-9-]+)*$")


@dataclass(frozen=True)
class DomainRules:
    """The user's per-domain browser rules, by domain pattern (`example.com` or `*.example.com`)."""

    rules: dict[str, DomainRule] = field(default_factory=dict)

    @classmethod
    def parse(cls, value: str | None) -> DomainRules:
        """`pattern=rule` pairs separated by commas; a malformed pair is an error, not something to guess at."""
        rules: dict[str, DomainRule] = {}
        for pair in (part.strip() for part in (value or "").split(",") if part.strip()):
            raw, sep, rule_name = (piece.strip().lower() for piece in pair.partition("="))
            rule = next((known for known in DOMAIN_RULES if known == rule_name), None)
            pattern = normalized_pattern(raw)
            if not sep or rule is None or pattern is None or not PATTERN.match(pattern):
                raise ValueError(f"OSMO_BROWSER_RULES: {pair!r} is not site=allow|ask|deny (e.g. github.com=allow)")
            rules[pattern] = rule
        return cls(rules)

    def match(self, url: str | None) -> DomainRule | None:
        """The rule the user set for the site of `url`; None when none covers it (or it has no host, as about:blank)."""
        try:
            host = normalize_host(urlsplit(url).hostname or "") if url else ""
        except ValueError:
            return None
        matches = [(len(pattern), rule) for pattern, rule in self.rules.items() if host and covers(pattern, host)]
        # Equally long patterns: the stricter rule wins ("deny" > "ask" > "allow" as strings, too).
        return max(matches)[1] if matches else None

    def decide(self, url: str | None) -> DomainRule:
        """The rule for the site of `url`: the user's, or `ask`."""
        return self.match(url) or "ask"


def covers(pattern: str, host: str) -> bool:
    """Whether a domain pattern covers `host` (the domain itself or a subdomain; `*.` only subdomains)."""
    if pattern.startswith("*."):
        return host.endswith(pattern[1:])
    return host == pattern or host.endswith(f".{pattern}")


def normalized_pattern(pattern: str) -> str | None:
    """A rule's pattern in `normalize_host` form (keeping a leading `*.`); None when it is not a valid name."""
    wildcard = pattern.startswith("*.")
    try:
        name = normalize_host(pattern[2:] if wildcard else pattern)
    except ValueError:
        return None
    return f"*.{name}" if wildcard else name
