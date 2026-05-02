import asyncio
import json
from pathlib import Path
from unittest.mock import AsyncMock, MagicMock, patch

import httpx
import pytest
import respx

from humblebundle_downloader.api import HumbleBundleAPI
from humblebundle_downloader.cache import DownloadCache
from humblebundle_downloader.downloader import DownloadEngine
from humblebundle_downloader.exceptions import APIError
from humblebundle_downloader.models import (
    AsmJsGame,
    DownloadItem,
    DownloadStatus,
    DownloadType,
    Order,
    Product,
    TroveProduct,
)


@pytest.fixture
def library_path(tmp_path):
    return tmp_path / "library"


@pytest.fixture
async def cache(library_path):
    library_path.mkdir(parents=True, exist_ok=True)
    c = DownloadCache(library_path)
    await c.load()
    return c


@pytest.fixture
def api():
    client = httpx.AsyncClient(base_url="https://www.humblebundle.com")
    return HumbleBundleAPI(client)


@pytest.fixture
def engine(api, cache, library_path):
    return DownloadEngine(
        api=api,
        cache=cache,
        library_path=library_path,
        max_concurrent=3,
    )


def make_item(
    cache_key="order1:file.pdf",
    url="https://dl.example.com/file.pdf",
    local_path=None,
    platform="windows",
    extension="pdf",
    **kwargs,
):
    return DownloadItem(
        cache_key=cache_key,
        url=url,
        local_path=local_path or Path("/tmp/file.pdf"),
        download_type=DownloadType.URL,
        platform=platform,
        extension=extension,
        **kwargs,
    )


class TestDownloadItemFiltering:
    async def test_skips_excluded_platform(self, api, cache, library_path):
        engine = DownloadEngine(
            api=api, cache=cache, library_path=library_path,
            platform_include=["linux"],
        )
        item = make_item(platform="windows", local_path=library_path / "f.pdf")
        status = await engine._download_item(item)
        assert status == DownloadStatus.SKIPPED

    async def test_skips_excluded_extension(self, api, cache, library_path):
        engine = DownloadEngine(
            api=api, cache=cache, library_path=library_path,
            ext_exclude=["pdf"],
        )
        item = make_item(local_path=library_path / "book.pdf")
        status = await engine._download_item(item)
        assert status == DownloadStatus.SKIPPED

    async def test_skips_when_cached_and_no_update(self, api, cache, library_path):
        await cache.set("order1:file.pdf", {"url_last_modified": "some-date"})
        engine = DownloadEngine(
            api=api, cache=cache, library_path=library_path, update=False,
        )
        item = make_item(local_path=library_path / "f.pdf")
        status = await engine._download_item(item)
        assert status == DownloadStatus.SKIPPED


class TestDoDownload:
    @respx.mock
    async def test_downloads_file_successfully(self, engine, library_path):
        file_content = b"Hello, world! This is a test file."
        download_path = library_path / "bundle" / "product" / "test.txt"

        respx.get("https://dl.example.com/test.txt").mock(
            return_value=httpx.Response(
                200,
                content=file_content,
                headers={
                    "Content-Length": str(len(file_content)),
                    "Last-Modified": "Wed, 15 May 2024 10:30:45 GMT",
                },
            )
        )

        item = make_item(
            cache_key="order1:test.txt",
            url="https://dl.example.com/test.txt",
            local_path=download_path,
            extension="txt",
        )
        status = await engine._do_download(item, None)
        assert status == DownloadStatus.COMPLETED
        assert download_path.exists()
        assert download_path.read_bytes() == file_content

    @respx.mock
    async def test_updates_cache_on_success(self, engine, cache, library_path):
        download_path = library_path / "test.txt"
        respx.get("https://dl.example.com/test.txt").mock(
            return_value=httpx.Response(
                200,
                content=b"data",
                headers={"Last-Modified": "Wed, 15 May 2024 10:30:45 GMT"},
            )
        )

        item = make_item(
            cache_key="order1:test.txt",
            url="https://dl.example.com/test.txt",
            local_path=download_path,
        )
        await engine._do_download(item, None)
        cached = await cache.get("order1:test.txt")
        assert cached is not None
        assert cached["url_last_modified"] == "Wed, 15 May 2024 10:30:45 GMT"

    @respx.mock
    async def test_skips_when_last_modified_unchanged(self, engine, library_path):
        download_path = library_path / "test.txt"
        respx.get("https://dl.example.com/test.txt").mock(
            return_value=httpx.Response(
                200,
                content=b"data",
                headers={"Last-Modified": "Wed, 15 May 2024 10:30:45 GMT"},
            )
        )

        item = make_item(
            url="https://dl.example.com/test.txt",
            local_path=download_path,
        )
        cached = {"url_last_modified": "Wed, 15 May 2024 10:30:45 GMT"}
        status = await engine._do_download(item, cached)
        assert status == DownloadStatus.SKIPPED

    @respx.mock
    async def test_handles_404(self, engine, library_path):
        download_path = library_path / "missing.txt"
        respx.get("https://dl.example.com/missing.txt").mock(
            return_value=httpx.Response(404)
        )

        item = make_item(
            url="https://dl.example.com/missing.txt",
            local_path=download_path,
        )
        status = await engine._do_download(item, None)
        assert status == DownloadStatus.FAILED
        assert not download_path.exists()

    @respx.mock
    async def test_creates_parent_directories(self, engine, library_path):
        deep_path = library_path / "a" / "b" / "c" / "file.txt"
        respx.get("https://dl.example.com/file.txt").mock(
            return_value=httpx.Response(200, content=b"data")
        )

        item = make_item(
            url="https://dl.example.com/file.txt",
            local_path=deep_path,
        )
        status = await engine._do_download(item, None)
        assert status == DownloadStatus.COMPLETED
        assert deep_path.exists()

    @respx.mock
    async def test_stores_trove_metadata_in_cache(self, engine, cache, library_path):
        download_path = library_path / "trove.zip"
        respx.get("https://dl.example.com/trove.zip").mock(
            return_value=httpx.Response(200, content=b"data")
        )

        item = make_item(
            cache_key="trove:trove.zip",
            url="https://dl.example.com/trove.zip",
            local_path=download_path,
            uploaded_at="1715769045",
            md5="abc123",
        )
        await engine._do_download(item, None)
        cached = await cache.get("trove:trove.zip")
        assert cached["uploaded_at"] == "1715769045"
        assert cached["md5"] == "abc123"


class TestDownloadTroveItem:
    @respx.mock
    async def test_signs_and_downloads(self, engine, cache, library_path):
        download_path = library_path / "Humble Trove" / "Game" / "game.zip"

        respx.post("https://www.humblebundle.com/api/v1/user/download/sign").mock(
            return_value=httpx.Response(
                200, json={"signed_url": "https://s3.example.com/signed/game.zip"}
            )
        )
        respx.get("https://s3.example.com/signed/game.zip").mock(
            return_value=httpx.Response(200, content=b"game data")
        )

        item = make_item(
            cache_key="trove:game.zip",
            url="game.zip",
            local_path=download_path,
            uploaded_at="123",
            md5="abc",
            machine_name="game_windows",
        )
        status = await engine._download_trove_item(item)
        assert status == DownloadStatus.COMPLETED
        assert download_path.exists()

    @respx.mock
    async def test_skips_when_cached_not_updated(self, engine, cache, library_path):
        await cache.set("trove:game.zip", {"uploaded_at": "123", "md5": "abc"})
        item = make_item(
            cache_key="trove:game.zip",
            url="game.zip",
            local_path=library_path / "game.zip",
            uploaded_at="123",
            md5="abc",
            machine_name="game_windows",
        )
        engine._update = True  # Must be true to get past first cache check
        status = await engine._download_trove_item(item)
        assert status == DownloadStatus.SKIPPED

    async def test_fails_without_machine_name(self, engine, cache, library_path):
        item = make_item(
            cache_key="trove:game.zip",
            url="game.zip",
            local_path=library_path / "game.zip",
            uploaded_at="123",
            md5="new_md5",
        )
        status = await engine._download_trove_item(item)
        assert status == DownloadStatus.FAILED


class TestDownloadAsmjsGame:
    @respx.mock
    async def test_downloads_html_and_assets(self, engine, library_path):
        game_folder = library_path / "bundle" / "product" / "mygame"

        asmjs_html = """<html>
<script id="webpack-asm-player-data" type="application/json">
{"asmOptions": {"manifest": {"game.js": "https://cdn.example.com/game.js"}}}
</script>
</html>"""

        # Mock HTML download
        respx.get("https://www.humblebundle.com/play/asmjs/mygame_asm/order1").mock(
            return_value=httpx.Response(200, content=asmjs_html.encode())
        )
        # Mock asset download
        respx.get("https://cdn.example.com/game.js").mock(
            return_value=httpx.Response(200, content=b"var game = {};")
        )

        html_item = DownloadItem(
            cache_key="order1:mygame.html",
            url="https://www.humblebundle.com/play/asmjs/mygame_asm/order1",
            local_path=game_folder / "mygame.html",
            download_type=DownloadType.URL,
            platform="windows",
            extension="html",
        )
        game = AsmJsGame(
            html_item=html_item,
            game_name="mygame",
            asm_name="mygame_asm",
            order_id="order1",
            local_folder=game_folder,
        )
        await engine._download_asmjs_game(game)

        assert (game_folder / "mygame.html").exists()
        assert (game_folder / "mygame.local.html").exists()
        assert (game_folder / "game.js").exists()

    @respx.mock
    async def test_local_html_replaces_urls(self, engine, library_path):
        game_folder = library_path / "bundle" / "product" / "mygame"

        asmjs_html = """<html>
<script id="webpack-asm-player-data" type="application/json">
{"asmOptions": {"manifest": {"game.js": "https://cdn.example.com/game.js"}}}
</script>
<script>"game.js": "https://cdn.example.com/game.js"</script>
</html>"""

        respx.get("https://www.humblebundle.com/play/asmjs/mg_asm/o1").mock(
            return_value=httpx.Response(200, content=asmjs_html.encode())
        )
        respx.get("https://cdn.example.com/game.js").mock(
            return_value=httpx.Response(200, content=b"js")
        )

        html_item = DownloadItem(
            cache_key="o1:mygame.html",
            url="https://www.humblebundle.com/play/asmjs/mg_asm/o1",
            local_path=game_folder / "mygame.html",
            download_type=DownloadType.URL,
            extension="html",
        )
        game = AsmJsGame(
            html_item=html_item,
            game_name="mygame",
            asm_name="mg_asm",
            order_id="o1",
            local_folder=game_folder,
        )
        await engine._download_asmjs_game(game)

        local_html = (game_folder / "mygame.local.html").read_text()
        assert '"game.js": "game.js"' in local_html
        assert "cdn.example.com" not in local_html


class TestCacheFlushInterval:
    @respx.mock
    async def test_flushes_periodically(self, api, library_path):
        library_path.mkdir(parents=True, exist_ok=True)
        cache = DownloadCache(library_path)
        await cache.load()

        engine = DownloadEngine(
            api=api, cache=cache, library_path=library_path,
        )

        # Download 15 files to trigger flush
        for i in range(15):
            download_path = library_path / f"file_{i}.txt"
            respx.get(f"https://dl.example.com/file_{i}.txt").mock(
                return_value=httpx.Response(200, content=b"data")
            )
            item = make_item(
                cache_key=f"order1:file_{i}.txt",
                url=f"https://dl.example.com/file_{i}.txt",
                local_path=download_path,
            )
            await engine._do_download(item, None)

        # Cache should have been flushed at least once (at 10)
        cache_file = library_path / ".cache.json"
        assert cache_file.exists()
        data = json.loads(cache_file.read_text())
        # At least 10 entries (flushed at 10, remaining 5 not yet flushed)
        assert len(data) >= 10


class TestConcurrentDownloads:
    @respx.mock
    async def test_semaphore_limits_concurrency(self, api, library_path):
        library_path.mkdir(parents=True, exist_ok=True)
        cache = DownloadCache(library_path)
        await cache.load()

        max_concurrent = 2
        engine = DownloadEngine(
            api=api, cache=cache, library_path=library_path,
            max_concurrent=max_concurrent,
        )

        active_count = 0
        max_active = 0
        lock = asyncio.Lock()

        original_do_download = engine._do_download

        async def tracked_download(item, cached):
            nonlocal active_count, max_active
            async with lock:
                active_count += 1
                max_active = max(max_active, active_count)
            try:
                return await original_do_download(item, cached)
            finally:
                async with lock:
                    active_count -= 1

        engine._do_download = tracked_download

        items = []
        for i in range(10):
            download_path = library_path / f"file_{i}.txt"
            respx.get(f"https://dl.example.com/file_{i}.txt").mock(
                return_value=httpx.Response(200, content=b"data")
            )
            items.append(make_item(
                cache_key=f"key_{i}",
                url=f"https://dl.example.com/file_{i}.txt",
                local_path=download_path,
            ))

        await asyncio.gather(*[engine._download_item(item) for item in items])
        assert max_active <= max_concurrent


class TestProcessOrder:
    @respx.mock
    async def test_processes_full_order(self, engine, library_path):
        order_json = {
            "product": {"human_name": "Test Bundle"},
            "subproducts": [
                {
                    "human_name": "Game 1",
                    "downloads": [
                        {
                            "platform": "windows",
                            "download_struct": [
                                {"url": {"web": "https://dl.example.com/game1.zip?t=x"}},
                            ],
                        },
                    ],
                },
            ],
        }
        respx.get(
            "https://www.humblebundle.com/api/v1/order/key1?all_tpkds=true"
        ).mock(return_value=httpx.Response(200, json=order_json))

        respx.get("https://dl.example.com/game1.zip").mock(
            return_value=httpx.Response(
                200,
                content=b"game data",
                headers={"Last-Modified": "Wed, 01 Jan 2025 00:00:00 GMT"},
            )
        )

        await engine._process_order("key1")
        expected_path = library_path / "Test Bundle" / "Game 1" / "game1.zip"
        assert expected_path.exists()
