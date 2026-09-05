"""One aggregate deadline and resource budget for ownership probes."""

from __future__ import annotations

import threading
import time
from concurrent.futures import ThreadPoolExecutor
from contextlib import contextmanager
from dataclasses import dataclass, field
from typing import Callable, Iterable, Iterator, TypeVar


MAX_PROBE_SECONDS = 3600


class ProbeBudgetError(RuntimeError):
    """Raised when a probe exceeds one of its aggregate bounds."""


@dataclass(frozen=True)
class ProbeLimits:
    seconds: float = MAX_PROBE_SECONDS
    variants: int = 4096
    subprocesses: int = 12288
    workers: int = 32
    bytes: dict[str, int] = field(
        default_factory=lambda: {
            "cache": 16 * 1024 * 1024,
            "events": 16 * 1024 * 1024,
            "mappings": 16 * 1024 * 1024,
            "outputs": 16 * 1024 * 1024,
        }
    )
    counts: dict[str, int] = field(
        default_factory=lambda: {
            "cache": 8192,
            "events": 4096,
            "mappings": 4096,
            "pending": 4096,
            "futures": 4096,
        }
    )

    def __post_init__(self) -> None:
        if not 0 < self.seconds <= MAX_PROBE_SECONDS:
            raise ValueError(
                f"probe deadline must be in (0, {MAX_PROBE_SECONDS}] seconds"
            )
        for label, value in (
            ("variants", self.variants),
            ("subprocesses", self.subprocesses),
            ("workers", self.workers),
        ):
            if value <= 0:
                raise ValueError(f"probe {label} bound must be positive")
        for collection in (self.bytes, self.counts):
            if not collection or any(
                not isinstance(name, str) or not name or value <= 0
                for name, value in collection.items()
            ):
                raise ValueError("probe category bounds must be positive")


class ProbeBudget:
    """Thread-safe aggregate authority shared by an entire probe."""

    def __init__(
        self,
        limits: ProbeLimits | None = None,
        *,
        clock: Callable[[], float] = time.monotonic,
    ):
        self.limits = ProbeLimits() if limits is None else limits
        self._clock = clock
        self.started = clock()
        self.deadline = self.started + self.limits.seconds
        self._lock = threading.Lock()
        self._variants = 0
        self._subprocesses = 0
        self._bytes = {name: 0 for name in self.limits.bytes}
        self._counts = {name: 0 for name in self.limits.counts}
        self._active = {
            name: 0 for name in ("pending", "futures") if name in self.limits.counts
        }

    def remaining(self, label: str = "probe") -> float:
        remaining = self.deadline - self._clock()
        if remaining <= 0:
            raise ProbeBudgetError(f"{label} exceeded aggregate deadline")
        return remaining

    def preflight_variants(self, count: int) -> None:
        if count < 0:
            raise ProbeBudgetError("variant count is negative")
        with self._lock:
            if self._variants + count > self.limits.variants:
                raise ProbeBudgetError(
                    "probe variant/state count exceeds aggregate bound"
                )
            self._variants += count
        self.remaining("variant preflight")

    def reserve_subprocess(self) -> float:
        with self._lock:
            if self._subprocesses >= self.limits.subprocesses:
                raise ProbeBudgetError(
                    "probe subprocess count exceeds aggregate bound"
                )
            self._subprocesses += 1
        return self.remaining("subprocess launch")

    def charge_bytes(self, category: str, amount: int) -> None:
        if amount < 0 or category not in self.limits.bytes:
            raise ProbeBudgetError(f"invalid probe byte category {category!r}")
        with self._lock:
            total = self._bytes[category] + amount
            if total > self.limits.bytes[category]:
                raise ProbeBudgetError(
                    f"probe {category} bytes exceed aggregate bound"
                )
            self._bytes[category] = total
        self.remaining(f"{category} accounting")

    def charge_count(self, category: str, amount: int = 1) -> None:
        if amount < 0 or category not in self.limits.counts:
            raise ProbeBudgetError(f"invalid probe count category {category!r}")
        with self._lock:
            total = self._counts[category] + amount
            if total > self.limits.counts[category]:
                raise ProbeBudgetError(
                    f"probe {category} count exceeds aggregate bound"
                )
            self._counts[category] = total
        self.remaining(f"{category} accounting")

    @contextmanager
    def lease(self, category: str, amount: int = 1) -> Iterator[None]:
        if amount < 0 or category not in self._active:
            raise ProbeBudgetError(f"invalid probe lease category {category!r}")
        self.charge_count(category, amount)
        with self._lock:
            active = self._active[category] + amount
            if active > self.limits.counts[category]:
                raise ProbeBudgetError(
                    f"probe {category} fanout exceeds aggregate bound"
                )
            self._active[category] = active
        try:
            yield
        finally:
            with self._lock:
                self._active[category] -= amount

    def snapshot(self) -> dict[str, object]:
        with self._lock:
            return {
                "active": dict(self._active),
                "bytes": dict(self._bytes),
                "counts": dict(self._counts),
                "subprocesses": self._subprocesses,
                "variants": self._variants,
            }


T = TypeVar("T")
R = TypeVar("R")


def run_bounded_futures(
    budget: ProbeBudget,
    items: Iterable[T],
    worker: Callable[[T, float], R],
) -> list[R]:
    """Submit one bounded batch and pass aggregate remaining time to each job."""
    pending = list(items)
    if not pending:
        return []
    with budget.lease("pending", len(pending)), budget.lease(
        "futures", len(pending)
    ):
        workers = min(len(pending), budget.limits.workers)
        executor = ThreadPoolExecutor(max_workers=workers)
        futures = []
        try:
            for item in pending:
                futures.append(
                    executor.submit(
                        worker,
                        item,
                        budget.remaining("worker submission"),
                    )
                )
            results = []
            for future in futures:
                results.append(
                    future.result(timeout=budget.remaining("worker result"))
                )
            return results
        except BaseException:
            for future in futures:
                future.cancel()
            executor.shutdown(wait=True, cancel_futures=True)
            raise
        finally:
            executor.shutdown(wait=True, cancel_futures=True)


class ProbeCache:
    """Budgeted per-probe cache; never a process-global authority."""

    def __init__(self, budget: ProbeBudget):
        self._budget = budget
        self._values: dict[tuple[object, ...], bytes] = {}
        self._lock = threading.Lock()

    def get(self, key: tuple[object, ...]) -> bytes | None:
        with self._lock:
            return self._values.get(key)

    def put(
        self,
        key: tuple[object, ...],
        value: bytes,
        *,
        declared_size: int | None = None,
    ) -> None:
        size = len(value) if declared_size is None else declared_size
        if size < len(value):
            raise ProbeBudgetError("cache declared size is smaller than its value")
        with self._lock:
            if key in self._values:
                if self._values[key] != value:
                    raise ProbeBudgetError("cache key resolved to different raw bytes")
                return
            self._budget.charge_count("cache")
            self._budget.charge_bytes("cache", size)
            self._values[key] = bytes(value)
