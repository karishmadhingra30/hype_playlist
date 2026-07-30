"""Daily caps on playlist generation.

The Anthropic key belongs to whoever deployed this and every visitor spends
it. Two ceilings: one per IP so a single person cannot sit on the button, and
one global so a bad day cannot run up an unbounded bill.

Counters live in memory. They reset when the process restarts and are not
shared between instances, which is fine for one small web service. This is a
cost guard, not a security boundary.
"""

import os
import threading
from datetime import datetime, timezone

from fastapi import HTTPException, Request


def _limit_from_env(name: str, default: int) -> int:
    """A limit of 0 means unlimited."""
    try:
        return max(0, int(os.environ.get(name, default)))
    except (TypeError, ValueError):
        return default


def client_ip(request: Request) -> str:
    """The visitor's address, accounting for the platform's proxy.

    Render terminates TLS in front of the app, so request.client.host is the
    proxy. The leftmost X-Forwarded-For entry is the original caller. It can
    be spoofed by anyone talking to the app directly, which is acceptable for
    a spend cap.
    """
    forwarded = request.headers.get("x-forwarded-for", "")
    if forwarded:
        first = forwarded.split(",")[0].strip()
        if first:
            return first
    return request.client.host if request.client else "unknown"


class DailyLimiter:
    def __init__(self, per_ip: int, per_day: int) -> None:
        self.per_ip = per_ip
        self.per_day = per_day
        self._lock = threading.Lock()
        self._day = self._today()
        self._by_ip: dict[str, int] = {}
        self._total = 0

    @staticmethod
    def _today() -> str:
        return datetime.now(timezone.utc).strftime("%Y-%m-%d")

    def _roll(self) -> None:
        """Drop yesterday's counters. Also what keeps the dict from growing."""
        today = self._today()
        if today != self._day:
            self._day = today
            self._by_ip = {}
            self._total = 0

    def snapshot(self, ip: str) -> dict:
        with self._lock:
            self._roll()
            return {
                "per_ip": self.per_ip,
                "per_day": self.per_day,
                "used_by_ip": self._by_ip.get(ip, 0),
                "used_today": self._total,
            }

    def check(self, ip: str) -> int:
        """Count one request against both ceilings. Returns what is left."""
        with self._lock:
            self._roll()

            if self.per_day and self._total >= self.per_day:
                raise HTTPException(
                    status_code=429,
                    detail="This site has hit its playlists for today. "
                           "Try again tomorrow.",
                    headers={"Retry-After": "3600"},
                )

            used = self._by_ip.get(ip, 0)
            if self.per_ip and used >= self.per_ip:
                raise HTTPException(
                    status_code=429,
                    detail=f"You have made {used} playlists today, which is "
                           f"the limit. Try again tomorrow.",
                    headers={"Retry-After": "3600"},
                )

            self._by_ip[ip] = used + 1
            self._total += 1
            return 0 if not self.per_ip else self.per_ip - self._by_ip[ip]

    def refund(self, ip: str) -> None:
        """Give back a request that never produced a playlist."""
        with self._lock:
            if self._by_ip.get(ip):
                self._by_ip[ip] -= 1
                self._total = max(0, self._total - 1)


def from_env() -> DailyLimiter:
    return DailyLimiter(
        per_ip=_limit_from_env("RATE_LIMIT_PER_IP", 5),
        per_day=_limit_from_env("RATE_LIMIT_PER_DAY", 200),
    )
