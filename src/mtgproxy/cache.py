"""Persistent Scryfall image cache.

Card *lookups* always go to Scryfall (so a decklist without printings still
resolves to the latest art); only the image bytes are cached, keyed by the
exact URL. cards.scryfall.io CDN URLs carry a cache-busting timestamp, so those
entries are immutable; the API's ``?format=image`` form doesn't, and Scryfall does
replace scans (placeholders around a set's release), so those expire after 30 days.

Installed by monkey-patching ``request_scryfall`` in the engine's scryfall
module: image requests (``format=image`` or the cards.scryfall.io CDN) are
served from disk when present and stored after a real download; everything
else passes straight through.
"""

from __future__ import annotations

import hashlib
import os
import shutil
import threading
import time
from dataclasses import dataclass
from pathlib import Path
from types import ModuleType

IMAGE_HOST = "cards.scryfall.io"
# cards.scryfall.io URLs carry a cache-busting timestamp, so they are immutable; the
# api.scryfall.com ?format=image form doesn't, and Scryfall does replace scans
# (placeholders around set release) — those entries expire.
API_HOST = "api.scryfall.com"
API_MAX_AGE_S = 30 * 24 * 3600


def is_image_url(url: str) -> bool:
    return IMAGE_HOST in url or "format=image" in url


def key_for(url: str) -> str:
    return hashlib.sha1(url.encode()).hexdigest()  # content key, not security


@dataclass
class _Cached:
    """Quacks like the slice of requests.Response the engine reads."""

    content: bytes
    status_code: int = 200


@dataclass
class CacheStats:
    files: int
    bytes: int
    hits: int = 0
    misses: int = 0


class ImageCache:
    def __init__(self, directory: Path):
        self.dir = directory
        self.hits = 0
        self.misses = 0
        self._lock = threading.Lock()

    def path(self, url: str) -> Path:
        k = key_for(url)
        return self.dir / k[:2] / f"{k}.png"

    def get(self, url: str) -> bytes | None:
        p = self.path(url)
        try:
            if API_HOST in url and time.time() - p.stat().st_mtime > API_MAX_AGE_S:
                return None
            data = p.read_bytes()
        except OSError:
            return None
        if not data:
            return None
        return data

    def put(self, url: str, data: bytes) -> None:
        if not data:
            return
        p = self.path(url)
        p.parent.mkdir(parents=True, exist_ok=True)
        # The engine downloads from worker threads; a per-writer temp name plus an
        # atomic rename keeps a duplicated card from corrupting its own cache entry.
        tmp = p.with_name(f"{p.name}.{os.getpid()}.{threading.get_ident()}.part")
        try:
            tmp.write_bytes(data)
            tmp.replace(p)
        except OSError:
            tmp.unlink(missing_ok=True)

    def stats(self) -> CacheStats:
        files = [f for f in self.dir.rglob("*.png")] if self.dir.exists() else []
        return CacheStats(len(files), sum(f.stat().st_size for f in files), self.hits, self.misses)

    def clear(self) -> int:
        n = self.stats().files
        if self.dir.exists():
            shutil.rmtree(self.dir)
        return n

    def install(self, scryfall: ModuleType) -> None:
        """Route ``scryfall.request_scryfall`` image downloads through this cache.

        Wraps once per process; a later install re-points the wrapper at the new
        instance (its own directory and counters) instead of stacking wrappers.
        """
        current = scryfall.request_scryfall
        if getattr(current, "_mtgproxy_wrapper", False):
            current._cache = self  # type: ignore[attr-defined]
            return
        real = current

        def cached_request(url: str, *args, **kwargs):
            cache: ImageCache = cached_request._cache  # type: ignore[attr-defined]
            if not is_image_url(url):
                return real(url, *args, **kwargs)
            data = cache.get(url)
            if data is not None:
                with cache._lock:
                    cache.hits += 1
                return _Cached(data)
            with cache._lock:
                cache.misses += 1
            resp = real(url, *args, **kwargs)
            cache.put(url, resp.content)
            return resp

        cached_request._mtgproxy_wrapper = True  # type: ignore[attr-defined]
        cached_request._cache = self  # type: ignore[attr-defined]
        scryfall.request_scryfall = cached_request
