import json
import logging
from contextlib import asynccontextmanager
from pathlib import Path

import httpx
import parsel

from .exceptions import APIError
from .models import AsmJsGame, DownloadItem, DownloadType, Order, Product, TroveProduct
from .utils import clean_name

logger = logging.getLogger(__name__)


class HumbleBundleAPI:
    """Async client for Humble Bundle API endpoints."""

    def __init__(self, client: httpx.AsyncClient):
        self._client = client

    async def get_purchase_keys(self) -> list[str]:
        """Fetch all purchase keys from the user's library page."""
        try:
            r = await self._client.get("/home/library")
            r.raise_for_status()
        except httpx.HTTPError as e:
            raise APIError(f"Failed to fetch library page: {e}") from e

        page = parsel.Selector(text=r.text)
        user_data = page.css("#user-home-json-data").xpath("string()").extract_first()
        if user_data is None:
            raise APIError("Unable to parse library page. Are your cookies valid?")

        orders_json = json.loads(user_data)
        return orders_json["gamekeys"]

    async def get_order(self, order_id: str, library_path: Path) -> Order:
        """Fetch an order and convert to domain models with local paths."""
        url = f"/api/v1/order/{order_id}?all_tpkds=true"
        try:
            r = await self._client.get(
                url,
                headers={
                    "content-type": "application/json",
                    "content-encoding": "gzip",
                },
            )
            r.raise_for_status()
        except httpx.HTTPError as e:
            raise APIError(f"Failed to fetch order {order_id}: {e}") from e

        order = r.json()
        bundle_title = clean_name(order["product"]["human_name"])
        logger.info(f"Checking bundle: {bundle_title}")

        products = []
        for sub in order["subproducts"]:
            product = self._parse_product(order_id, bundle_title, sub, library_path)
            products.append(product)

        return Order(
            order_id=order_id,
            bundle_title=bundle_title,
            products=tuple(products),
        )

    async def get_trove_catalog(self, library_path: Path) -> list[TroveProduct]:
        """Fetch all Trove products (paginated)."""
        all_products = []
        idx = 0
        while True:
            logger.debug(f"Fetching trove catalog page {idx}...")
            try:
                r = await self._client.get(f"/client/catalog?index={idx}")
                r.raise_for_status()
            except httpx.HTTPError as e:
                raise APIError(f"Failed to fetch trove catalog page {idx}: {e}") from e

            page_content = r.json()
            if not page_content:
                break

            for product_data in page_content:
                trove = self._parse_trove_product(product_data, library_path)
                all_products.append(trove)
            idx += 1

        return all_products

    async def get_signed_trove_url(self, machine_name: str, filename: str) -> str:
        """Sign a trove download URL."""
        try:
            r = await self._client.post(
                "/api/v1/user/download/sign",
                data={"machine_name": machine_name, "filename": filename},
            )
            r.raise_for_status()
        except httpx.HTTPError as e:
            raise APIError(f"Failed to sign trove download for {filename}: {e}") from e

        data = r.json()
        if data.get("_errors") == "Unauthorized":
            raise APIError("Your account does not have access to the Trove")
        return data["signed_url"]

    async def get_asmjs_html(self, game_asm_name: str, order_id: str) -> str:
        """Fetch the asm.js game HTML page."""
        url = f"/play/asmjs/{game_asm_name}/{order_id}"
        try:
            r = await self._client.get(url)
            r.raise_for_status()
        except httpx.HTTPError as e:
            raise APIError(f"Failed to fetch asm.js page for {game_asm_name}: {e}") from e
        return r.text

    @staticmethod
    def parse_asmjs_manifest(html_content: str) -> dict[str, str]:
        """Parse #webpack-asm-player-data from asm.js HTML.

        Returns dict mapping local_filename -> remote_url.
        """
        page = parsel.Selector(text=html_content)
        data_text = page.css("#webpack-asm-player-data::text").get()
        if data_text is None:
            raise APIError("Could not find asm player data in HTML")
        data = json.loads(data_text)
        return data["asmOptions"]["manifest"]

    @asynccontextmanager
    async def download_stream(self, url: str):
        """Stream a file download. Must be used as async context manager.

        For absolute URLs (signed trove URLs, manifest assets), the full URL
        is used directly. For relative paths, base_url applies.
        """
        async with self._client.stream("GET", url) as response:
            yield response

    def _parse_product(
        self,
        order_id: str,
        bundle_title: str,
        product_data: dict,
        library_path: Path,
    ) -> Product:
        """Convert a subproduct JSON dict into a Product model."""
        product_title = clean_name(product_data["human_name"])
        product_folder = library_path / bundle_title / product_title

        downloads: list[DownloadItem | AsmJsGame] = []
        external_links: list[str] = []

        for download_type in product_data["downloads"]:
            platform = download_type["platform"]

            for file_type in download_type["download_struct"]:
                if "url" in file_type and "web" in file_type["url"]:
                    url = file_type["url"]["web"]
                    url_filename = url.split("?")[0].split("/")[-1]
                    cache_key = f"{order_id}:{url_filename}"
                    downloads.append(
                        DownloadItem(
                            cache_key=cache_key,
                            url=url,
                            local_path=product_folder / url_filename,
                            download_type=DownloadType.URL,
                            platform=platform,
                            extension=url_filename.rsplit(".", 1)[-1] if "." in url_filename else "",
                        )
                    )
                elif "asm_config" in file_type:
                    game_name = file_type["asm_config"]["display_item"]
                    game_asm_name = file_type["asm_manifest"]["asmFile"].split("/")[2]
                    local_folder = product_folder / game_name

                    html_filename = f"{game_name}.html"
                    html_item = DownloadItem(
                        cache_key=f"{order_id}:{html_filename}",
                        url=f"https://www.humblebundle.com/play/asmjs/{game_asm_name}/{order_id}",
                        local_path=local_folder / html_filename,
                        download_type=DownloadType.URL,
                        platform=platform,
                        extension="html",
                    )
                    downloads.append(
                        AsmJsGame(
                            html_item=html_item,
                            game_name=game_name,
                            asm_name=game_asm_name,
                            order_id=order_id,
                            local_folder=local_folder,
                        )
                    )
                elif "external_link" in file_type:
                    external_links.append(file_type["external_link"])
                # else: no downloadable URL, skip silently

        return Product(
            human_name=product_title,
            downloads=tuple(downloads),
            external_links=tuple(external_links),
        )

    def _parse_trove_product(
        self,
        product_data: dict,
        library_path: Path,
    ) -> TroveProduct:
        """Convert a trove catalog JSON entry into a TroveProduct model."""
        title = clean_name(product_data["human-name"])
        items = []

        for platform, download in product_data["downloads"].items():
            web_name = download["url"]["web"].split("/")[-1]
            cache_key = f"trove:{web_name}"
            uploaded_at = (
                download.get("uploaded_at")
                or download.get("timestamp")
                or product_data.get("date_added", "0")
            )

            items.append(
                DownloadItem(
                    cache_key=cache_key,
                    url=web_name,  # not the real URL yet; needs signing
                    local_path=library_path / "Humble Trove" / title / web_name,
                    download_type=DownloadType.URL,
                    platform=platform,
                    extension=web_name.rsplit(".", 1)[-1] if "." in web_name else "",
                    uploaded_at=str(uploaded_at),
                    md5=download.get("md5", "UNKNOWN_MD5"),
                    machine_name=download.get("machine_name"),
                )
            )

        return TroveProduct(human_name=title, downloads=tuple(items))
