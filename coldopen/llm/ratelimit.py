"""Async rate limiting: a concurrency semaphore plus a per-minute token bucket.

Why both: free tiers cap requests per minute (the bucket) and punish bursts of
parallel long calls (the semaphore). Cache hits never touch the limiter.
"""

from __future__ import annotations

import asyncio
import time


class RateLimiter:
    def __init__(self, concurrency: int, rpm: int, clock=time.monotonic, sleep=asyncio.sleep):
        if concurrency < 1 or rpm < 1:
            raise ValueError("concurrency and rpm must be >= 1")
        self.concurrency = concurrency
        self.rpm = rpm
        self._sem: asyncio.Semaphore | None = None
        self._lock: asyncio.Lock | None = None
        self._loop: asyncio.AbstractEventLoop | None = None
        self._tokens = float(rpm)
        self._last = clock()
        self._clock = clock
        self._sleep = sleep

    def _ensure_primitives(self) -> None:
        # Created lazily, and re-created per event loop: asyncio primitives bind to
        # the loop they first wait on, and sync callers run a fresh loop per call.
        loop = asyncio.get_running_loop()
        if self._sem is None or self._loop is not loop:
            self._sem = asyncio.Semaphore(self.concurrency)
            self._lock = asyncio.Lock()
            self._loop = loop

    async def _take_token(self) -> None:
        assert self._lock is not None
        rate = self.rpm / 60.0  # tokens per second
        while True:
            async with self._lock:
                now = self._clock()
                self._tokens = min(float(self.rpm), self._tokens + (now - self._last) * rate)
                self._last = now
                if self._tokens >= 1.0:
                    self._tokens -= 1.0
                    return
                wait = (1.0 - self._tokens) / rate
            await self._sleep(wait)

    async def __aenter__(self) -> "RateLimiter":
        self._ensure_primitives()
        assert self._sem is not None
        await self._sem.acquire()
        try:
            await self._take_token()
        except BaseException:
            self._sem.release()
            raise
        return self

    async def __aexit__(self, *exc) -> None:
        assert self._sem is not None
        self._sem.release()
