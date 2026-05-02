import asyncio
import json
from pathlib import Path

import pytest

from humblebundle_downloader.cache import DownloadCache


@pytest.fixture
def cache(tmp_path):
    return DownloadCache(tmp_path)


@pytest.fixture
def populated_cache(tmp_path):
    """Cache with pre-existing data on disk."""
    cache_file = tmp_path / ".cache.json"
    data = {
        "order1:file.pdf": {"url_last_modified": "Wed, 15 May 2024 10:30:45 GMT"},
        "trove:game.zip": {"uploaded_at": "1715769045", "md5": "abc123"},
    }
    cache_file.write_text(json.dumps(data))
    return DownloadCache(tmp_path)


class TestLoad:
    @pytest.mark.asyncio
    async def test_load_missing_file(self, cache):
        await cache.load()
        assert await cache.get("anything") is None

    @pytest.mark.asyncio
    async def test_load_existing_file(self, populated_cache):
        await populated_cache.load()
        entry = await populated_cache.get("order1:file.pdf")
        assert entry is not None
        assert entry["url_last_modified"] == "Wed, 15 May 2024 10:30:45 GMT"

    @pytest.mark.asyncio
    async def test_load_trove_entry(self, populated_cache):
        await populated_cache.load()
        entry = await populated_cache.get("trove:game.zip")
        assert entry["uploaded_at"] == "1715769045"
        assert entry["md5"] == "abc123"


class TestGetSet:
    @pytest.mark.asyncio
    async def test_get_nonexistent_returns_none(self, cache):
        await cache.load()
        assert await cache.get("missing") is None

    @pytest.mark.asyncio
    async def test_set_and_get_roundtrip(self, cache):
        await cache.load()
        await cache.set("key1", {"url_last_modified": "some-date"})
        entry = await cache.get("key1")
        assert entry == {"url_last_modified": "some-date"}

    @pytest.mark.asyncio
    async def test_set_overwrites_existing(self, cache):
        await cache.load()
        await cache.set("key1", {"value": "old"})
        await cache.set("key1", {"value": "new"})
        entry = await cache.get("key1")
        assert entry == {"value": "new"}

    @pytest.mark.asyncio
    async def test_get_returns_copy_not_reference(self, cache):
        await cache.load()
        await cache.set("key1", {"value": "original"})
        entry = await cache.get("key1")
        entry["value"] = "mutated"
        fresh = await cache.get("key1")
        assert fresh["value"] == "original"


class TestHasEntry:
    @pytest.mark.asyncio
    async def test_missing_entry(self, cache):
        await cache.load()
        assert await cache.has_entry("missing") is False

    @pytest.mark.asyncio
    async def test_existing_entry(self, cache):
        await cache.load()
        await cache.set("key1", {"data": True})
        assert await cache.has_entry("key1") is True


class TestFlush:
    @pytest.mark.asyncio
    async def test_flush_writes_to_disk(self, cache, tmp_path):
        await cache.load()
        await cache.set("key1", {"url_last_modified": "date1"})
        await cache.flush()
        cache_file = tmp_path / ".cache.json"
        assert cache_file.exists()
        data = json.loads(cache_file.read_text())
        assert data["key1"]["url_last_modified"] == "date1"

    @pytest.mark.asyncio
    async def test_flush_noop_when_not_dirty(self, cache, tmp_path):
        await cache.load()
        await cache.flush()
        cache_file = tmp_path / ".cache.json"
        assert not cache_file.exists()

    @pytest.mark.asyncio
    async def test_flush_atomic_write(self, cache, tmp_path):
        """Verify flush uses tmp+rename pattern (no .tmp file left)."""
        await cache.load()
        await cache.set("key1", {"data": True})
        await cache.flush()
        tmp_file = tmp_path / ".cache.tmp"
        assert not tmp_file.exists()
        assert (tmp_path / ".cache.json").exists()

    @pytest.mark.asyncio
    async def test_flush_sorted_keys(self, cache, tmp_path):
        await cache.load()
        await cache.set("z_key", {"data": "z"})
        await cache.set("a_key", {"data": "a"})
        await cache.flush()
        raw = (tmp_path / ".cache.json").read_text()
        assert raw.index("a_key") < raw.index("z_key")

    @pytest.mark.asyncio
    async def test_flush_creates_parent_dirs(self, tmp_path):
        nested = tmp_path / "deep" / "nested"
        c = DownloadCache(nested)
        await c.load()
        await c.set("key", {"v": 1})
        await c.flush()
        assert (nested / ".cache.json").exists()

    @pytest.mark.asyncio
    async def test_double_flush_noop(self, cache, tmp_path):
        await cache.load()
        await cache.set("key1", {"data": True})
        await cache.flush()
        # Modify file externally
        cache_file = tmp_path / ".cache.json"
        cache_file.write_text('{"external": true}')
        # Second flush should be noop (not dirty)
        await cache.flush()
        data = json.loads(cache_file.read_text())
        assert data == {"external": True}


class TestIsUpdated:
    @pytest.mark.asyncio
    async def test_missing_key_returns_true(self, cache):
        await cache.load()
        assert cache.is_updated("missing") is True

    @pytest.mark.asyncio
    async def test_url_same_last_modified(self, cache):
        await cache.load()
        await cache.set("key1", {"url_last_modified": "date1"})
        assert cache.is_updated("key1", url_last_modified="date1") is False

    @pytest.mark.asyncio
    async def test_url_different_last_modified(self, cache):
        await cache.load()
        await cache.set("key1", {"url_last_modified": "date1"})
        assert cache.is_updated("key1", url_last_modified="date2") is True

    @pytest.mark.asyncio
    async def test_trove_same_uploaded_at_and_md5(self, cache):
        await cache.load()
        await cache.set("key1", {"uploaded_at": "123", "md5": "abc"})
        assert cache.is_updated("key1", uploaded_at="123", md5="abc") is False

    @pytest.mark.asyncio
    async def test_trove_different_uploaded_at_same_md5(self, cache):
        """This is the fixed AND->OR bug: changing uploaded_at alone triggers update."""
        await cache.load()
        await cache.set("key1", {"uploaded_at": "123", "md5": "abc"})
        assert cache.is_updated("key1", uploaded_at="456", md5="abc") is True

    @pytest.mark.asyncio
    async def test_trove_same_uploaded_at_different_md5(self, cache):
        """This is the fixed AND->OR bug: changing md5 alone triggers update."""
        await cache.load()
        await cache.set("key1", {"uploaded_at": "123", "md5": "abc"})
        assert cache.is_updated("key1", uploaded_at="123", md5="def") is True

    @pytest.mark.asyncio
    async def test_trove_both_changed(self, cache):
        await cache.load()
        await cache.set("key1", {"uploaded_at": "123", "md5": "abc"})
        assert cache.is_updated("key1", uploaded_at="456", md5="def") is True

    @pytest.mark.asyncio
    async def test_no_comparison_data_returns_true(self, cache):
        await cache.load()
        await cache.set("key1", {"url_last_modified": "date1"})
        assert cache.is_updated("key1") is True


class TestConcurrentAccess:
    @pytest.mark.asyncio
    async def test_concurrent_sets_do_not_corrupt(self, cache):
        """Multiple concurrent set() calls should not lose data."""
        await cache.load()

        async def set_entry(i: int):
            await cache.set(f"key_{i}", {"index": i})

        await asyncio.gather(*[set_entry(i) for i in range(100)])

        for i in range(100):
            entry = await cache.get(f"key_{i}")
            assert entry is not None
            assert entry["index"] == i

    @pytest.mark.asyncio
    async def test_concurrent_set_and_flush(self, cache, tmp_path):
        """Flush during concurrent sets should not crash."""
        await cache.load()

        async def set_and_flush(i: int):
            await cache.set(f"key_{i}", {"index": i})
            if i % 10 == 0:
                await cache.flush()

        await asyncio.gather(*[set_and_flush(i) for i in range(50)])
        await cache.flush()

        data = json.loads((tmp_path / ".cache.json").read_text())
        assert len(data) == 50
