"""normalize_host: the form browser rules and the network guard compare host names in, as Chrome resolves them."""

from __future__ import annotations

import pytest

from symphony_server.browser_rules import DomainRules
from symphony_server.host_names import normalize_host, site_of


def test_an_internationalised_name_is_encoded_the_way_chrome_connects_to_it() -> None:
    # Re-review N3: IDNA2003 maps ß to ss (fass.de, another domain); IDNA2008 with UTS #46 keeps it.
    assert normalize_host("faß.de") == "xn--fa-hia.de"
    assert normalize_host("FASS.de.") == "fass.de"
    assert normalize_host("BÜCHER.de.") == "xn--bcher-kva.de"
    rules = DomainRules.parse("faß.de=deny,fass.de=allow")
    assert rules.decide("https://xn--fa-hia.de/") == "deny"
    assert rules.decide("https://faß.de/") == "deny"
    assert rules.decide("https://fass.de/") == "allow"


def test_ascii_names_and_addresses_stay_as_they_are_and_bad_names_are_refused() -> None:
    assert normalize_host("My_Host.Example.") == "my_host.example"
    assert normalize_host("127.0.0.1") == "127.0.0.1"
    assert normalize_host("::1") == "::1"
    for bad in ("a..b", "x" * 64 + ".com", "ex\u00a0ample.com"):
        with pytest.raises(ValueError):
            normalize_host(bad)


def test_the_site_of_a_url_is_its_normalised_host_or_none() -> None:
    # One reading of a URL's site for the rules, the approval check and the take-over record (out of BrowserSession).
    assert site_of("https://WWW.Bücher.de./path?q=1") == "www.xn--bcher-kva.de"
    assert site_of("http://127.0.0.1:8787/api") == "127.0.0.1"
    for nothing in (None, "", "about:blank", "data:text/html,hi", "https://a..b/"):
        assert site_of(nothing) is None, nothing
    assert DomainRules.parse("a.example=deny").match("https://a..b/") is None
