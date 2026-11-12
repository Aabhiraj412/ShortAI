"""Thread-safe synchronous runner for async coroutines."""

import asyncio
from concurrent.futures import ThreadPoolExecutor
from typing import Coroutine, TypeVar

T = TypeVar("T")


def run_coro_sync(coro: Coroutine[object, object, T]) -> T:
    """Executes *coro* from synchronous code even if the caller thread already runs an event loop."""
    try:
        asyncio.get_running_loop()
    except RuntimeError:
        return asyncio.run(coro)
    with ThreadPoolExecutor(max_workers=1) as pool:
        return pool.submit(asyncio.run, coro).result()
