"""Daemon-side 1 Hz sampling with isolated, bounded sensor workers."""

import asyncio
from collections import deque
from concurrent.futures import Future
from dataclasses import dataclass
import math
from queue import Empty, Full, Queue
from threading import Event, Thread
import time
import uuid

from predator_sense.service.sensors import failed
from predator_sense.service.telemetry_model import (
    EC_FIELDS, FIELDS, HISTORY_LIMIT, INTERVAL, Reading, TelemetrySnapshot,
)


def next_deadline(previous, now):
    """Advance an anchored one-second schedule, skipping missed slots, never bursting."""
    return previous + max(1, math.floor((now - previous) / INTERVAL) + 1) * INTERVAL


@dataclass(frozen=True)
class SampleResult:
    readings: tuple[Reading, ...]
    ready: bool = False
    code: str = ""
    message: str = ""


class SensorWorker:
    """One outstanding operation per lane, including when a driver call stalls.

    Threads are daemonized: a hung foreign NVML call cannot prevent process exit.
    No replacement threads or queued retries are created. Cleanup runs on the same
    worker after its current read returns, so NVML shutdown never races a read.
    """
    def __init__(self, name, read, close=None):
        self.read, self.cleanup = read, close
        self.queue = Queue(maxsize=1)
        self.stopped = Event()
        self.future = None
        self.thread = Thread(target=self._run, name=f"telemetry-{name}", daemon=True)
        self.thread.start()

    def _run(self):
        try:
            while not self.stopped.is_set():
                try:
                    future = self.queue.get(timeout=0.5)
                except Empty:
                    continue
                try:
                    if future is None or self.stopped.is_set():
                        return
                    try:
                        future.set_result(self.read())
                    except Exception as exc:
                        future.set_exception(exc)
                finally:
                    self.queue.task_done()
        finally:
            if self.cleanup:
                self.cleanup()

    def submit(self):
        if self.stopped.is_set() or self.future is not None:
            return
        self.future = Future()
        self.queue.put_nowait(self.future)

    def take(self):
        if self.future is None or not self.future.done():
            return None
        future, self.future = self.future, None
        return future.result()

    def close(self):
        self.stopped.set()
        try:
            self.queue.put_nowait(None)
        except Full:
            pass


class TelemetryEngine:
    def __init__(self, cpu, gpu, ec, *, clock=time.monotonic, wall_clock=time.time, worker_factory=SensorWorker):
        self.clock, self.wall_clock = clock, wall_clock
        self.epoch = uuid.uuid4().hex
        self.latest = TelemetrySnapshot.empty(epoch=self.epoch)
        self.history = deque(maxlen=HISTORY_LIMIT)
        self.workers = {
            "cpu": worker_factory("cpu", lambda: SampleResult((cpu.read(),))),
            "gpu": worker_factory("gpu", lambda: SampleResult((gpu.read(),)), getattr(gpu, "close", None)),
            "ec": worker_factory("ec", ec),
        }
        self.fields = {"cpu": ("cpu_temp_c",), "gpu": ("gpu_temp_c",), "ec": EC_FIELDS}
        self.results = {
            name: SampleResult(
                tuple(Reading(field, sampled_at=self.clock()) for field in fields),
                code="Starting", message="Waiting for sensors",
            )
            for name, fields in self.fields.items()
        }
        self.closed = False

    def start_samples(self):
        for worker in self.workers.values():
            worker.submit()

    def tick(self):
        """Nonblocking publication. Slow lanes retain explicitly dated/stale values."""
        now = self.clock()
        for name, worker in self.workers.items():
            try:
                result = worker.take()
            except Exception as exc:
                result = SampleResult(
                    tuple(failed(field, name, exc) for field in self.fields[name]),
                    code="SensorFailed", message="Fan state sampling failed" if name == "ec" else "",
                )
            if result is not None:
                self.results[name] = result
            worker.submit()
        readings = {r.name: r for result in self.results.values() for r in result.readings}
        ec = self.results["ec"]
        self.latest = TelemetrySnapshot(
            self.wall_clock(), now, self.latest.sequence + 1, self.epoch,
            tuple(readings[field] for field in FIELDS), ec.ready, ec.code, ec.message,
        ).aged(now)
        self.history.append(self.latest)
        return self.latest

    async def run(self):
        self.start_samples()
        deadline = self.clock() + INTERVAL
        try:
            while not self.closed:
                await asyncio.sleep(max(0, deadline - self.clock()))
                if self.closed:
                    break
                self.tick()
                deadline = next_deadline(deadline, self.clock())
        finally:
            self.close()

    def close(self):
        self.closed = True
        for worker in self.workers.values():
            worker.close()
