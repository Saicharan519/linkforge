"""The trusted-proxy rule for finding the client address (ARCHITECTURE §6.3).

The point of every case: a caller must not be able to choose the address the rate limiter sees.
"""

import pytest
from starlette.requests import Request

from app.core.client_ip import client_ip

PEER = "10.0.0.7"  # the TCP peer: in a cluster, the proxy's address


def request(xff: list[str] | None = None, peer: str | None = PEER) -> Request:
    headers = [(b"x-forwarded-for", value.encode()) for value in xff or []]
    scope = {
        "type": "http",
        "method": "GET",
        "path": "/",
        "headers": headers,
        "client": (peer, 5555) if peer else None,
    }
    return Request(scope)


def test_no_trusted_proxy_ignores_the_header_completely() -> None:
    # Count 0 (local run, nothing in front): whatever the caller writes is just a claim.
    assert client_ip(request(["6.6.6.6"]), 0) == PEER
    assert client_ip(request(["1.1.1.1, 2.2.2.2"]), 0) == PEER


def test_one_proxy_takes_the_last_entry_the_proxy_appended() -> None:
    # The caller sent "6.6.6.6"; our proxy appended the address it actually saw: 203.0.113.9.
    assert client_ip(request(["6.6.6.6, 203.0.113.9"]), 1) == "203.0.113.9"


def test_a_spoofed_prefix_cannot_change_the_result() -> None:
    real = "203.0.113.9"
    a = client_ip(request([f"1.1.1.1, {real}"]), 1)
    b = client_ip(request([f"2.2.2.2, 3.3.3.3, {real}"]), 1)
    assert a == b == real


def test_two_proxies_take_the_second_from_the_right() -> None:
    # client -> proxy A -> proxy B -> API: header is "<claimed>, <client>, <proxy A>".
    assert client_ip(request(["9.9.9.9, 203.0.113.9, 10.1.1.1"]), 2) == "203.0.113.9"


def test_fewer_entries_than_trusted_proxies_falls_back_to_the_peer() -> None:
    # Configured for 2 proxies but only one entry: the header cannot be trusted as a whole.
    assert client_ip(request(["203.0.113.9"]), 2) == PEER
    assert client_ip(request(), 1) == PEER  # no header at all


def test_a_non_ip_entry_falls_back_to_the_peer() -> None:
    assert client_ip(request(["not-an-ip"]), 1) == PEER
    assert client_ip(request(["1.1.1.1, <script>"]), 1) == PEER
    assert client_ip(request(["1.1.1.1, "]), 1) == "1.1.1.1"  # empty trailing part is dropped


def test_repeated_header_lines_count_as_one_list_in_order() -> None:
    assert client_ip(request(["6.6.6.6", "203.0.113.9"]), 1) == "203.0.113.9"


def test_whitespace_is_ignored_and_ipv6_is_normalised() -> None:
    assert client_ip(request(["  6.6.6.6 ,   203.0.113.9  "]), 1) == "203.0.113.9"
    long_form = client_ip(request(["0:0:0:0:0:0:0:1"]), 1)
    assert long_form == client_ip(request(["::1"]), 1) == "::1"


def test_no_peer_information_is_still_a_string() -> None:
    assert client_ip(request(peer=None), 0) == "unknown"


@pytest.mark.parametrize("count", [-1, 0])
def test_non_positive_counts_never_read_the_header(count: int) -> None:
    assert client_ip(request(["6.6.6.6"]), count) == PEER
