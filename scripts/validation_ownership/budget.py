"""One aggregate deadline and resource budget for ownership probes."""

from __future__ import annotations

import threading
import time
import multiprocessing
import os
import pickle
import selectors
import signal
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
            "snapshot": 256 * 1024 * 1024,
            "worker_results": 16 * 1024 * 1024,
        }
    )
    counts: dict[str, int] = field(
        default_factory=lambda: {
            "cache": 8192,
            "events": 4096,
            "mappings": 4096,
            "pending": 4096,
            "futures": 4096,
            "snapshot_files": 16384,
            "snapshot_ops": 131072,
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
    """Run a bounded batch in killable worker process groups."""
    pending = list(items)
    if not pending:
        return []
    with budget.lease("pending", len(pending)), budget.lease(
        "futures", len(pending)
    ):
        if len(pending) > budget.limits.workers:
            raise ProbeBudgetError(
                "probe worker batch exceeds bounded process fanout"
            )
        context = multiprocessing.get_context("fork")
        processes: list[multiprocessing.Process] = []
        readers: list[int] = []
        selector = selectors.DefaultSelector()
        results: dict[int, R] = {}

        def child(index: int, item: T, descriptor: int, remaining: float) -> None:
            try:
                os.setsid()
                payload = ("ok", worker(item, remaining))
            except BaseException as error:
                payload = ("error", type(error).__name__, str(error))
            encoded = pickle.dumps(payload, protocol=5)
            try:
                framed = len(encoded).to_bytes(8, "little") + encoded
                offset = 0
                while offset < len(framed):
                    offset += os.write(descriptor, framed[offset:])
            finally:
                os.close(descriptor)

        def terminate_all() -> None:
            for process in processes:
                if not process.is_alive():
                    continue
                try:
                    os.killpg(process.pid, signal.SIGKILL)
                except ProcessLookupError:
                    if process.is_alive():
                        process.kill()
            for process in processes:
                process.join(timeout=2)
                if process.is_alive():
                    raise ProbeBudgetError("probe worker process did not terminate")

        try:
            for index, item in enumerate(pending):
                reader, writer = os.pipe2(os.O_CLOEXEC)
                process = context.Process(
                    target=child,
                    args=(
                        index,
                        item,
                        writer,
                        budget.remaining("worker submission"),
                    ),
                )
                process.start()
                os.close(writer)
                processes.append(process)
                readers.append(reader)
                selector.register(
                    reader,
                    selectors.EVENT_READ,
                    {
                        "buffer": bytearray(),
                        "expected": None,
                        "index": index,
                    },
                )
            while selector.get_map():
                events = selector.select(
                    min(0.1, budget.remaining("worker result"))
                )
                for key, _ in events:
                    chunk = os.read(key.fd, 64 * 1024)
                    state = key.data
                    if not chunk:
                        selector.unregister(key.fd)
                        if state["expected"] is None or (
                            len(state["buffer"]) != state["expected"] + 8
                        ):
                            raise ProbeBudgetError(
                                "probe worker result was truncated"
                            )
                        encoded = bytes(state["buffer"][8:])
                        outcome = pickle.loads(encoded)
                        if outcome[0] != "ok":
                            raise ProbeBudgetError(
                                "probe worker failed: "
                                f"{outcome[1]}: {outcome[2]}"
                            )
                        results[state["index"]] = outcome[1]
                        continue
                    budget.charge_bytes("worker_results", len(chunk))
                    state["buffer"].extend(chunk)
                    if state["expected"] is None and len(state["buffer"]) >= 8:
                        state["expected"] = int.from_bytes(
                            state["buffer"][:8],
                            "little",
                        )
                        if state["expected"] > budget.limits.bytes["worker_results"]:
                            raise ProbeBudgetError(
                                "probe worker result exceeds byte bound"
                            )
                    if (
                        state["expected"] is not None
                        and len(state["buffer"]) > state["expected"] + 8
                    ):
                        raise ProbeBudgetError("probe worker result exceeded frame")
            for process in processes:
                process.join(timeout=min(1, budget.remaining("worker reap")))
                if process.is_alive() or process.exitcode != 0:
                    raise ProbeBudgetError(
                        "probe worker did not exit cleanly"
                    )
            return [results[index] for index in range(len(pending))]
        except BaseException:
            terminate_all()
            raise
        finally:
            selector.close()
            for descriptor in readers:
                try:
                    os.close(descriptor)
                except OSError:
                    pass


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
