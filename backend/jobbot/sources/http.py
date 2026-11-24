import threading
import time

import httpx

from .. import config

_last: dict[str, float] = {}
_lock = threading.Lock()


def client() -> httpx.Client:
    return httpx.Client(timeout=30, follow_redirects=True, headers={"User-Agent": config.USER_AGENT})


def get_json(c: httpx.Client, url: str, min_gap: float = 0.12, **kw):
    """GET with a per-host minimum gap and one retry on 429/5xx."""
    host = httpx.URL(url).host
    for attempt in range(2):
        with _lock:
            wait = _last.get(host, 0) + min_gap - time.monotonic()
            _last[host] = time.monotonic() + max(wait, 0)
        if wait > 0:
            time.sleep(wait)
        r = c.get(url, **kw)
        if r.status_code == 429 or r.status_code >= 500:
            if attempt == 0:
                time.sleep(min(float(r.headers.get("retry-after") or 5), 30))
                continue
        r.raise_for_status()
        return r.json()
