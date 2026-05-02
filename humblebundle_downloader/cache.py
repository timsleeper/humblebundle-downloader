import asyncio
import json
import logging
from pathlib import Path
from typing import Any

logger = logging.getLogger(__name__)


class DownloadCache:
    """Async-safe download cache backed by .cache.json.

    Uses asyncio.Lock for all mutations. Writes to disk are batched via
    explicit flush() calls rather than after every set().
    """

    def __init__(self, library_path: Path):
        self._cache_file = library_path / ".cache.json"
        self._data: dict[str, dict[str, Any]] = {}
        self._lock = asyncio.Lock()
        self._dirty = False

    async def load(self) -> None:
        """Load cache from disk. Safe to call if file doesn't exist."""
        try:
            text = self._cache_file.read_text()
            self._data = json.loads(text)
        except FileNotFoundError:
            self._data = {}

    async def get(self, cache_key: str) -> dict[str, Any] | None:
        """Get cached entry. Returns None if not found."""
        async with self._lock:
            entry = self._data.get(cache_key)
            return dict(entry) if entry else None

    async def set(self, cache_key: str, entry: dict[str, Any]) -> None:
        """Set a cache entry. Does not write to disk immediately."""
        async with self._lock:
            self._data[cache_key] = entry
            self._dirty = True

    async def has_entry(self, cache_key: str) -> bool:
        """Check if a cache key exists."""
        async with self._lock:
            return cache_key in self._data

    async def flush(self) -> None:
        """Write cache to disk if dirty. Uses atomic write (tmp + rename)."""
        async with self._lock:
            if not self._dirty:
                return
            self._cache_file.parent.mkdir(parents=True, exist_ok=True)
            tmp_path = self._cache_file.with_suffix(".tmp")
            tmp_path.write_text(
                json.dumps(self._data, sort_keys=True, indent=4)
            )
            tmp_path.rename(self._cache_file)
            self._dirty = False
            logger.debug("Cache flushed to disk")

    def is_updated(
        self,
        cache_key: str,
        url_last_modified: str | None = None,
        uploaded_at: str | None = None,
        md5: str | None = None,
    ) -> bool:
        """Check if a file needs re-downloading.

        For URL downloads: compares url_last_modified header.
        For Trove downloads: compares uploaded_at OR md5 (the original code
        incorrectly used AND, meaning both had to change).

        Returns True if the file should be downloaded.
        """
        cached = self._data.get(cache_key)
        if cached is None:
            return True

        # Trove comparison: either timestamp changed OR md5 changed
        if uploaded_at is not None:
            if uploaded_at != cached.get("uploaded_at") or md5 != cached.get("md5"):
                return True
            return False

        # URL comparison: Last-Modified header
        if url_last_modified is not None:
            return url_last_modified != cached.get("url_last_modified")

        return True
