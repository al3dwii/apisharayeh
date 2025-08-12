import asyncio, os, urllib.parse

_MAX = int(os.getenv("MAX_CONCURRENCY_PER_HOST", "8"))
_SEMAPHORES: dict[str, asyncio.Semaphore] = {}

def get_host(url: str) -> str:
    return urllib.parse.urlparse(url).hostname or "default"

def get_semaphore_for_host(host: str):
    if host not in _SEMAPHORES:
        _SEMAPHORES[host] = asyncio.Semaphore(_MAX)
    return _SEMAPHORES[host]
