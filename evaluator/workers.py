"""Bounded evaluation-call concurrency; persistence stays on the calling thread."""

from concurrent.futures import FIRST_COMPLETED, Future, ThreadPoolExecutor, wait
from collections.abc import Callable, Iterable, Iterator
from typing import TypeVar

Job = TypeVar("Job")
Result = TypeVar("Result")


def positive_workers(value: str) -> int:
    workers = int(value)
    if workers < 1:
        raise ValueError("workers must be a positive integer")
    return workers


def completed_jobs(
    function: Callable[[Job], Result], jobs: Iterable[Job], workers: int,
    *, on_wait: Callable[[], None] | None = None,
) -> Iterator[tuple[Job, Future[Result]]]:
    if not isinstance(workers, int) or isinstance(workers, bool) or workers < 1:
        raise ValueError("workers must be a positive integer")
    iterator = iter(jobs)
    pool = ThreadPoolExecutor(max_workers=workers)
    pending: dict[Future[Result], Job] = {}

    def submit_next() -> None:
        try:
            job = next(iterator)
        except StopIteration:
            return
        pending[pool.submit(function, job)] = job

    try:
        for _ in range(workers):
            submit_next()
        while pending:
            ready, _ = wait(pending, timeout=15, return_when=FIRST_COMPLETED)
            if not ready and on_wait is not None:
                on_wait()
            for future in ready:
                job = pending.pop(future)
                yield job, future
                submit_next()
    finally:
        for future in pending:
            future.cancel()
        pool.shutdown(wait=True, cancel_futures=True)
