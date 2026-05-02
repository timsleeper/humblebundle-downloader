import asyncio
import datetime
import hashlib
import logging
from pathlib import Path
from typing import Any

import aiofiles
import httpx
from rich.progress import (
    Progress,
    TaskID,
)

from .api import HumbleBundleAPI
from .cache import DownloadCache
from .exceptions import APIError, DownloadError
from .filters import should_download_file, should_download_platform
from .models import AsmJsGame, DownloadItem, DownloadStatus, DownloadType
from .utils import rename_old_file

logger = logging.getLogger(__name__)

DEFAULT_FLUSH_INTERVAL = 10
RETRY_ATTEMPTS = 3
RETRY_BACKOFF_BASE = 2.0
RETRYABLE_EXCEPTIONS = (
    httpx.ReadError,
    httpx.ReadTimeout,
    httpx.WriteError,
    httpx.RemoteProtocolError,
    httpx.ConnectError,
    httpx.ConnectTimeout,
    httpx.PoolTimeout,
)


class DownloadEngine:
    """Async download orchestrator with concurrency control."""

    def __init__(
        self,
        api: HumbleBundleAPI,
        cache: DownloadCache,
        library_path: Path,
        max_concurrent: int = 5,
        update: bool = False,
        ext_include: list[str] | None = None,
        ext_exclude: list[str] | None = None,
        platform_include: list[str] | None = None,
        progress: Progress | None = None,
    ):
        self._api = api
        self._cache = cache
        self._library_path = library_path
        self._semaphore = asyncio.Semaphore(max_concurrent)
        self._update = update
        self._ext_include = ext_include
        self._ext_exclude = ext_exclude
        self._platform_include = platform_include
        self._progress = progress
        self._completed_since_flush = 0
        self._flush_lock = asyncio.Lock()
        self._flush_interval = max(1, max_concurrent // 2)

    async def download_library(self, purchase_keys: list[str] | None = None) -> None:
        """Download all normal (non-trove) bundles."""
        keys = purchase_keys or await self._api.get_purchase_keys()
        tasks = [self._process_order(key) for key in keys]
        await asyncio.gather(*tasks, return_exceptions=True)
        await self._cache.flush()

    async def download_trove(self) -> None:
        """Download all Humble Trove content."""
        logger.info("Only checking the Humble Trove...")
        products = await self._api.get_trove_catalog(self._library_path)
        tasks = []
        for product in products:
            for item in product.downloads:
                tasks.append(self._download_trove_item(item))
        await asyncio.gather(*tasks, return_exceptions=True)
        await self._cache.flush()

    async def _process_order(self, order_id: str) -> None:
        """Process a single order: fetch details, dispatch downloads."""
        try:
            order = await self._api.get_order(order_id, self._library_path)
        except APIError:
            logger.error(f"Failed to get order {order_id}")
            return

        tasks = []
        for product in order.products:
            for link in product.external_links:
                logger.info(f"External link: {order.bundle_title}/{product.human_name}: {link}")
            for item in product.downloads:
                if isinstance(item, AsmJsGame):
                    tasks.append(self._download_asmjs_game(item))
                else:
                    tasks.append(self._download_item(item))

        await asyncio.gather(*tasks, return_exceptions=True)

    async def _download_item(self, item: DownloadItem) -> DownloadStatus:
        """Download a single file with filtering and semaphore gating."""
        if not should_download_platform(item.platform, self._platform_include):
            logger.info(f"Skipping platform {item.platform}: {item.local_path.name}")
            return DownloadStatus.SKIPPED

        if not should_download_file(item.local_path.name, self._ext_include, self._ext_exclude):
            logger.info(f"Skipping extension: {item.local_path.name}")
            return DownloadStatus.SKIPPED

        cached = await self._cache.get(item.cache_key)
        if cached is not None and not self._update:
            return DownloadStatus.SKIPPED

        async with self._semaphore:
            return await self._do_download(item, cached)

    async def _do_download(
        self, item: DownloadItem, cached: dict[str, Any] | None
    ) -> DownloadStatus:
        """Perform the actual streamed download with progress tracking."""
        item.local_path.parent.mkdir(parents=True, exist_ok=True)

        downloaded = 0
        md5_hash = hashlib.md5()
        _last_modified: str | None = None

        for attempt in range(1, RETRY_ATTEMPTS + 1):
            task_id: TaskID | None = None
            downloaded = 0
            md5_hash = hashlib.md5()
            try:
                async with self._api.download_stream(item.url) as response:
                    if response.status_code != 200:
                        logger.debug(f"File unavailable: {item.url} ({response.status_code})")
                        return DownloadStatus.FAILED

                    last_modified = response.headers.get("Last-Modified")
                    if (
                        cached
                        and last_modified
                        and last_modified == cached.get("url_last_modified")
                    ):
                        return DownloadStatus.SKIPPED

                    if cached and "url_last_modified" in cached:
                        try:
                            old_date = datetime.datetime.strptime(
                                cached["url_last_modified"], "%a, %d %b %Y %H:%M:%S %Z"
                            ).strftime("%Y-%m-%d")
                            rename_old_file(item.local_path, old_date)
                        except ValueError:
                            pass

                    total = int(response.headers.get("content-length", 0)) or None
                    if self._progress and total:
                        task_id = self._progress.add_task(item.local_path.name, total=total)

                    async with aiofiles.open(item.local_path, "wb") as f:
                        async for chunk in response.aiter_bytes(chunk_size=8192):
                            await f.write(chunk)
                            downloaded += len(chunk)
                            md5_hash.update(chunk)
                            if task_id is not None:
                                self._progress.update(task_id, completed=downloaded)

                    if total and downloaded < total:
                        # Connection silently dropped before EOF; route to retry.
                        raise httpx.ReadError(f"Incomplete download: {downloaded}/{total} bytes")

                    if item.md5 and md5_hash.hexdigest() != item.md5:
                        raise DownloadError(
                            f"MD5 mismatch for {item.local_path.name}: "
                            f"expected {item.md5}, got {md5_hash.hexdigest()}"
                        )

                    _last_modified = last_modified
                break
            except DownloadError:
                if item.local_path.exists():
                    item.local_path.unlink()
                raise
            except RETRYABLE_EXCEPTIONS as e:
                if item.local_path.exists():
                    item.local_path.unlink()
                if attempt < RETRY_ATTEMPTS:
                    wait = RETRY_BACKOFF_BASE * (2 ** (attempt - 1))
                    logger.warning(
                        f"{item.local_path.name}: {type(e).__name__} on attempt "
                        f"{attempt}/{RETRY_ATTEMPTS}, retrying in {wait:.0f}s"
                    )
                    await asyncio.sleep(wait)
                    continue
                logger.error(
                    f"Failed to download {item.local_path.name} after "
                    f"{RETRY_ATTEMPTS} attempts: {type(e).__name__}: {e}"
                )
                return DownloadStatus.FAILED
            except Exception:
                logger.exception(f"Failed to download {item.local_path.name}")
                if item.local_path.exists():
                    item.local_path.unlink()
                return DownloadStatus.FAILED
            finally:
                if task_id is not None and self._progress is not None:
                    try:
                        self._progress.remove_task(task_id)
                    except KeyError:
                        pass

        # Update cache
        file_info: dict[str, Any] = {
            "local_path": str(item.local_path),
            "file_size": downloaded,
            "file_md5": md5_hash.hexdigest(),
        }
        if item.uploaded_at:
            file_info["uploaded_at"] = item.uploaded_at
        if item.md5:
            file_info["md5"] = item.md5
        if _last_modified:
            file_info["url_last_modified"] = _last_modified
        else:
            file_info["url_last_modified"] = datetime.datetime.now().strftime(
                "%a, %d %b %Y %H:%M:%S %Z"
            )

        await self._cache.set(item.cache_key, file_info)
        await self._maybe_flush_cache()
        logger.info(f"Downloaded: {item.local_path}")
        return DownloadStatus.COMPLETED

    async def _download_trove_item(self, item: DownloadItem) -> DownloadStatus:
        """Download a trove item: sign URL first, then download."""
        if not should_download_platform(item.platform, self._platform_include):
            return DownloadStatus.SKIPPED
        if not should_download_file(item.local_path.name, self._ext_include, self._ext_exclude):
            return DownloadStatus.SKIPPED

        cached = await self._cache.get(item.cache_key)
        if cached is not None and not self._update:
            return DownloadStatus.SKIPPED

        if cached and not self._cache.is_updated(
            item.cache_key,
            uploaded_at=item.uploaded_at,
            md5=item.md5,
        ):
            return DownloadStatus.SKIPPED

        async with self._semaphore:
            # Sign the URL just before downloading (signed URLs expire)
            if not item.machine_name:
                logger.error(f"No machine_name for trove item {item.local_path.name}")
                return DownloadStatus.FAILED

            try:
                signed_url = await self._api.get_signed_trove_url(
                    item.machine_name, item.local_path.name
                )
            except APIError:
                logger.error(f"Failed to sign URL for {item.local_path.name}")
                return DownloadStatus.FAILED

            # Create a new DownloadItem with the signed URL
            signed_item = DownloadItem(
                cache_key=item.cache_key,
                url=signed_url,
                local_path=item.local_path,
                download_type=item.download_type,
                platform=item.platform,
                extension=item.extension,
                uploaded_at=item.uploaded_at,
                md5=item.md5,
                machine_name=item.machine_name,
            )
            return await self._do_download(signed_item, cached)

    async def _download_asmjs_game(self, game: AsmJsGame) -> None:
        """Download an asm.js game: HTML first, then manifest assets in parallel."""
        html_status = await self._download_item(game.html_item)

        if html_status == DownloadStatus.FAILED:
            return

        html_path = game.local_folder / f"{game.game_name}.html"
        if not html_path.exists():
            return

        manifest = self._api.parse_asmjs_manifest(html_path.read_text())

        # Create local.html with URLs replaced by local filenames
        if html_status == DownloadStatus.COMPLETED:
            await self._create_local_asmjs_html(game, manifest)

        # Download manifest assets in parallel
        tasks = []
        for local_name, remote_url in manifest.items():
            cache_key = f"{game.order_id}:{game.game_name}:{local_name}"
            asset_item = DownloadItem(
                cache_key=cache_key,
                url=remote_url,
                local_path=game.local_folder / local_name,
                download_type=DownloadType.URL,
            )
            tasks.append(self._download_item(asset_item))

        await asyncio.gather(*tasks, return_exceptions=True)

    async def _create_local_asmjs_html(self, game: AsmJsGame, manifest: dict[str, str]) -> None:
        """Create local.html with manifest URLs replaced by local filenames."""
        try:
            src = game.local_folder / f"{game.game_name}.html"
            dst = game.local_folder / f"{game.game_name}.local.html"
            async with aiofiles.open(src, "r") as f_in:
                content = await f_in.read()
            for local_name, remote_url in manifest.items():
                content = content.replace(
                    f'"{local_name}": "{remote_url}"',
                    f'"{local_name}": "{local_name}"',
                )
            async with aiofiles.open(dst, "w") as f_out:
                await f_out.write(content)
        except Exception:
            logger.exception(f"Failed to create local HTML for {game.game_name}")

    async def _maybe_flush_cache(self) -> None:
        """Flush cache periodically to avoid losing progress."""
        async with self._flush_lock:
            self._completed_since_flush += 1
            if self._completed_since_flush >= self._flush_interval:
                await self._cache.flush()
                self._completed_since_flush = 0
