"""Tests for the daily spend caps.

    python -m tests.test_limits
"""

import sys

from fastapi import HTTPException, Request
from fastapi.testclient import TestClient

from backend import limits, main
from tests.test_playlist import GOOD, NO_TRACKS, FakeClient


def fake_request(headers: dict, host: str = "10.0.0.1") -> Request:
    scope = {
        "type": "http",
        "headers": [(k.lower().encode(), v.encode()) for k, v in headers.items()],
        "client": (host, 1234),
    }
    return Request(scope)


def check_ip_parsing() -> None:
    # Render puts the visitor first and its own proxies after.
    r = fake_request({"x-forwarded-for": "203.0.113.9, 10.2.3.4"})
    assert limits.client_ip(r) == "203.0.113.9", limits.client_ip(r)

    # No header means talking to the app directly.
    r = fake_request({}, host="198.51.100.7")
    assert limits.client_ip(r) == "198.51.100.7", limits.client_ip(r)

    # A blank header must not become a shared empty-string bucket.
    r = fake_request({"x-forwarded-for": "  "}, host="198.51.100.7")
    assert limits.client_ip(r) == "198.51.100.7", limits.client_ip(r)
    print("[ ok ] the visitor's address is read through the proxy header")


def check_per_ip() -> None:
    lim = limits.DailyLimiter(per_ip=3, per_day=0)
    for _ in range(3):
        lim.check("a")
    try:
        lim.check("a")
        raise AssertionError("a fourth request should have been refused")
    except HTTPException as exc:
        assert exc.status_code == 429, exc.status_code
        assert exc.headers.get("Retry-After"), exc.headers

    # One noisy visitor must not lock anyone else out.
    lim.check("b")
    print("[ ok ] per-IP cap holds and does not affect other visitors")


def check_global() -> None:
    lim = limits.DailyLimiter(per_ip=0, per_day=2)
    lim.check("a")
    lim.check("b")
    try:
        lim.check("c")
        raise AssertionError("the global cap should have been reached")
    except HTTPException as exc:
        assert "site" in exc.detail, exc.detail
    print("[ ok ] global cap stops a busy day regardless of who is asking")


def check_unlimited() -> None:
    lim = limits.DailyLimiter(per_ip=0, per_day=0)
    for _ in range(50):
        lim.check("a")
    print("[ ok ] 0 means unlimited")


def check_refund() -> None:
    lim = limits.DailyLimiter(per_ip=1, per_day=10)
    lim.check("a")
    lim.refund("a")
    lim.check("a")  # the refunded attempt freed the slot again
    assert lim.snapshot("a")["used_by_ip"] == 1
    # A refund with nothing to give back must not go negative.
    lim.refund("z")
    assert lim.snapshot("z")["used_by_ip"] == 0
    print("[ ok ] a failed generation is refunded and never goes negative")


def check_rollover() -> None:
    lim = limits.DailyLimiter(per_ip=1, per_day=1)
    lim.check("a")
    lim._day = "1999-01-01"  # pretend the counters are from yesterday
    lim.check("a")
    assert lim.snapshot("a")["used_today"] == 1
    print("[ ok ] counters reset on a new UTC day")


def check_endpoint() -> None:
    """The cap has to actually apply to the route, not just the class."""
    main.limiter = limits.DailyLimiter(per_ip=2, per_day=10)
    main._client = FakeClient([GOOD])
    body = {"situation": "gym", "length": "medium", "vibe": 50}
    head = {"X-Forwarded-For": "203.0.113.9"}

    with TestClient(main.app) as client:
        assert client.post("/api/playlist", json=body, headers=head).status_code == 200
        assert client.post("/api/playlist", json=body, headers=head).status_code == 200
        third = client.post("/api/playlist", json=body, headers=head)
        assert third.status_code == 429, third.status_code
        assert "limit" in third.json()["detail"], third.json()

        # A different visitor is unaffected.
        other = client.post("/api/playlist", json=body,
                            headers={"X-Forwarded-For": "198.51.100.7"})
        assert other.status_code == 200, other.status_code
    print("[ ok ] the route enforces the cap per visitor")


def check_failure_is_refunded() -> None:
    """A visitor should not lose a slot to our own bad response."""
    main.limiter = limits.DailyLimiter(per_ip=5, per_day=10)
    main._client = FakeClient([NO_TRACKS, NO_TRACKS])
    body = {"situation": "gym", "length": "medium", "vibe": 50}
    head = {"X-Forwarded-For": "203.0.113.9"}

    with TestClient(main.app) as client:
        assert client.post("/api/playlist", json=body, headers=head).status_code == 502
    assert main.limiter.snapshot("203.0.113.9")["used_by_ip"] == 0
    print("[ ok ] a 502 does not spend the visitor's daily allowance")


def run() -> int:
    check_ip_parsing()
    check_per_ip()
    check_global()
    check_unlimited()
    check_refund()
    check_rollover()
    check_endpoint()
    check_failure_is_refunded()
    print("\nall assertions passed")
    return 0


if __name__ == "__main__":
    sys.exit(run())
