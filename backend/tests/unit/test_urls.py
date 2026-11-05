import pytest

from app.core.urls import MAX_URL_LENGTH, InvalidUrl, validate_destination_url

PROD_BASE = "https://go.example.com"  # default HTTPS port -> every port on this host is "us"
DEV_BASE = "http://localhost:8000"  # explicit port -> only that exact host:port is "us"

VALID = [
    "http://example.com",
    "https://example.com/path?q=1&r=2#frag",
    "HTTPS://EXAMPLE.COM/Path",  # scheme and host are case-insensitive
    "https://sub.example.co.in:8443/a/b",
    "http://localhost:3000/app",  # local targets are allowed (we never fetch them)
    "http://127.0.0.1/",
    "http://[::1]:8080/",
    "https://bücher.example/straße",  # internationalised domain
    "https://xn--bcher-kva.example/",
    "https://example.com./",  # fully-qualified form with a trailing dot
    "https://app.example.com/dashboard",  # our APP host is fine; only the short host is blocked
    "https://example.com/" + "a" * (MAX_URL_LENGTH - len("https://example.com/")),  # exactly 2048
]

INVALID = [
    ("", "empty"),
    ("example.com", "no scheme"),
    ("//example.com", "scheme-relative"),
    ("ftp://example.com/file", "ftp"),
    ("file:///etc/passwd", "file"),
    ("javascript:alert(1)", "javascript"),
    ("JavaScript:alert(1)", "javascript, odd case"),
    ("data:text/html,<script>alert(1)</script>", "data"),
    ("http://", "no host"),
    ("http:///path", "empty host"),
    ("http:example.com", "missing //"),
    ("https://:8080/", "port but no host"),
    ("https://exa mple.com", "space in host"),
    (" https://example.com", "leading space"),
    ("https://example.com ", "trailing space"),
    ("https://example.com/\n", "newline"),
    ("https://example.com/ x", "non-breaking space"),
    ("https://example.com\\@evil.com", "backslash"),
    ("http://user:pass@example.com", "credentials"),
    ("http://google.com@evil.com", "userinfo trick"),
    ("http://@example.com", "empty userinfo"),
    ("https://example.com:99999", "port too large"),
    ("https://example.com:abc", "port not a number"),
    ("https://example.com:0/", "port zero"),
    ("https://-bad.example", "label starts with hyphen"),
    ("https://bad-.example", "label ends with hyphen"),
    ("https://a..b.example", "empty label"),
    ("https://exa_mple.com", "underscore in host"),
    ("http://999.1.1.1", "malformed IPv4"),
    ("http://123", "numeric host"),
    ("https://example.com/" + "a" * MAX_URL_LENGTH, "too long"),
]

# (url, short_url_base) -> must be refused because it points back at our own short domain.
OWN_DOMAIN = [
    ("https://go.example.com/abc", PROD_BASE),
    ("http://go.example.com/abc", PROD_BASE),  # other scheme, same host
    ("http://GO.EXAMPLE.COM/abc", PROD_BASE),  # case-insensitive
    ("https://go.example.com:8443/abc", PROD_BASE),  # other port, same host (default-port base)
    ("https://go.example.com./abc", PROD_BASE),  # trailing dot
    ("http://localhost:8000/abc", DEV_BASE),
    ("https://localhost:8000/abc", DEV_BASE),  # explicit port is what matters for dev bases
]

# Same-looking URLs that are NOT us.
NOT_OWN_DOMAIN = [
    ("https://app.example.com/x", PROD_BASE),
    ("https://example.com/x", PROD_BASE),
    ("https://notgo.example.com/x", PROD_BASE),
    ("http://localhost:3000/x", DEV_BASE),  # another local app
    ("http://localhost/x", DEV_BASE),  # port 80, not 8000
]


@pytest.mark.parametrize("url", VALID)
def test_url_validation_accepts(url: str) -> None:
    # Returned unchanged: we validate, we never rewrite what the user typed.
    assert validate_destination_url(url, short_url_base=PROD_BASE) == url


@pytest.mark.parametrize(("url", "why"), INVALID, ids=[why for _, why in INVALID])
def test_url_validation_rejects(url: str, why: str) -> None:
    with pytest.raises(InvalidUrl):
        validate_destination_url(url, short_url_base=PROD_BASE)


@pytest.mark.parametrize(("url", "base"), OWN_DOMAIN)
def test_url_rejects_own_short_domain(url: str, base: str) -> None:
    with pytest.raises(InvalidUrl, match="own short domain"):
        validate_destination_url(url, short_url_base=base)


@pytest.mark.parametrize(("url", "base"), NOT_OWN_DOMAIN)
def test_url_allows_lookalikes_that_are_not_us(url: str, base: str) -> None:
    assert validate_destination_url(url, short_url_base=base) == url


def test_error_messages_are_user_safe() -> None:
    with pytest.raises(InvalidUrl) as exc:
        validate_destination_url("javascript:alert(1)", short_url_base=PROD_BASE)
    assert str(exc.value) == "only http and https URLs are allowed"
