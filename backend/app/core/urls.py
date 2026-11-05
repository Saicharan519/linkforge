"""Destination URL validation: the ONE place that decides whether a URL may be shortened.

Rules (PRD F2): http/https only, at most 2048 characters, a valid host, and never our own short
domain (that would create a redirect loop). We deliberately go a bit further than the minimum:
no whitespace/control characters or backslashes, and no embedded credentials. Those are classic
tricks for making a link look like it goes somewhere it does not (decision D43).

The URL is validated, never rewritten: the caller stores exactly what the user typed.
"""

import ipaddress
import re
from urllib.parse import urlsplit, urlunsplit

MAX_URL_LENGTH = 2048
ALLOWED_SCHEMES = frozenset({"http", "https"})
_DEFAULT_PORTS = {"http": 80, "https": 443}

# Whitespace (incl. Unicode spaces), control characters and backslashes. Browsers treat "\" like
# "/", so "http://evil.com\@good.com" can mean something different to a browser than to a parser.
_FORBIDDEN_CHARS = re.compile(r"[\s\x00-\x1f\x7f\\]")
# One DNS label (RFC 1123): letters, digits and inner hyphens, 1-63 characters.
_LABEL = re.compile(r"[a-z0-9](?:[a-z0-9-]{0,61}[a-z0-9])?")


class InvalidUrl(ValueError):
    """The URL may not be shortened; the message is safe to show to the user."""


def _normalise_host(hostname: str) -> str:
    """Return a canonical form of a host (lowercase, no trailing dot, IDNA as ASCII).

    Raises InvalidUrl if it is neither an IP literal nor a valid domain name.
    """
    host = hostname.lower()
    try:
        ipaddress.ip_address(host)  # IPv4, or IPv6 (urlsplit strips the brackets)
    except ValueError:
        pass
    else:
        return host

    host = host.removesuffix(".")  # "example.com." is the same host as "example.com"
    try:
        ascii_host = host.encode("idna").decode("ascii")  # "bücher.example" -> "xn--bcher-kva..."
    except UnicodeError:
        raise InvalidUrl("the host is not a valid domain name") from None

    labels = ascii_host.split(".")
    if (
        not ascii_host
        or len(ascii_host) > 253
        or not all(_LABEL.fullmatch(label) for label in labels)
        # An all-numeric last label would be a malformed IPv4 address ("999.1.1.1"), not a domain.
        or labels[-1].isdigit()
    ):
        raise InvalidUrl("the host is not a valid domain name")
    return ascii_host


def _own_origin(short_url_base: str) -> tuple[str, int | None]:
    """(host, explicit non-default port) of our own short domain."""
    base = urlsplit(short_url_base)
    if not base.hostname:
        raise ValueError(f"SHORT_URL_BASE has no host: {short_url_base!r}")
    port = base.port
    if port == _DEFAULT_PORTS.get(base.scheme):
        port = None
    return _normalise_host(base.hostname), port


def location_for(url: str) -> str:
    """The stored URL as a value that is safe to put in a `Location` header.

    HTTP headers are ASCII. An internationalised host such as "bücher.example" must be sent in its
    punycode form ("xn--bcher-kva.example"); percent-encoding it would point at a different host.
    (Non-ASCII characters in the path/query are percent-encoded by the response class.)
    A plain ASCII URL is returned untouched.
    """
    if url.isascii():
        return url
    parts = urlsplit(url)
    try:
        host = _normalise_host(parts.hostname or "")
    except InvalidUrl:  # cannot happen for a URL that passed validation; never crash a redirect
        return url
    if ":" in host:  # an IPv6 literal needs its brackets back
        host = f"[{host}]"
    netloc = host if parts.port is None else f"{host}:{parts.port}"
    return urlunsplit(parts._replace(netloc=netloc))


def validate_destination_url(url: str, *, short_url_base: str) -> str:
    """Return `url` unchanged if it may be shortened, else raise InvalidUrl."""
    if not url:
        raise InvalidUrl("the URL is required")
    if len(url) > MAX_URL_LENGTH:
        raise InvalidUrl(f"the URL must be at most {MAX_URL_LENGTH} characters")
    if _FORBIDDEN_CHARS.search(url):
        raise InvalidUrl("the URL must not contain spaces, control characters or backslashes")

    try:
        parts = urlsplit(url)
        port = parts.port  # raises ValueError for a non-numeric or out-of-range port
    except ValueError:
        raise InvalidUrl("the URL is not valid") from None

    # This also rejects javascript:, data:, file:, ftp: and friends.
    if parts.scheme not in ALLOWED_SCHEMES:
        raise InvalidUrl("only http and https URLs are allowed")
    # "http://google.com@evil.com" really goes to evil.com; refuse credentials in URLs outright.
    if parts.username is not None or parts.password is not None:
        raise InvalidUrl("URLs with embedded credentials are not allowed")
    if not parts.hostname:
        raise InvalidUrl("the URL must contain a host")
    if port is not None and not 1 <= port <= 65535:
        raise InvalidUrl("the port is not valid")

    host = _normalise_host(parts.hostname)

    # Never shorten a link that points back at our own short domain (redirect loops).
    # If our base uses a non-default port (local dev: :8000), only that exact host+port is "us",
    # so a different local app on localhost:3000 stays allowed. With a default port (production),
    # every port on our host counts as "us".
    own_host, own_port = _own_origin(short_url_base)
    if host == own_host:
        effective_port = port if port is not None else _DEFAULT_PORTS[parts.scheme]
        if own_port is None or effective_port == own_port:
            raise InvalidUrl("links to this service's own short domain are not allowed")

    return url
