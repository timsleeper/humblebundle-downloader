from __future__ import annotations

from dataclasses import dataclass, field
from enum import Enum
from pathlib import Path


class DownloadType(Enum):
    URL = "url"
    ASM_JS = "asm_js"
    EXTERNAL = "external"


class DownloadStatus(Enum):
    PENDING = "pending"
    DOWNLOADING = "downloading"
    COMPLETED = "completed"
    SKIPPED = "skipped"
    FAILED = "failed"


@dataclass(frozen=True)
class DownloadItem:
    """A single downloadable file."""

    cache_key: str
    url: str
    local_path: Path
    download_type: DownloadType
    platform: str = ""
    extension: str = ""
    uploaded_at: str | None = None
    md5: str | None = None
    # For trove items: machine_name needed to get signed download URL
    machine_name: str | None = None


@dataclass(frozen=True)
class AsmJsGame:
    """An asm.js game requiring sequential download: HTML first, then manifest assets."""

    html_item: DownloadItem
    game_name: str
    asm_name: str
    order_id: str
    local_folder: Path


@dataclass(frozen=True)
class Product:
    """A product within a purchased order."""

    human_name: str
    downloads: tuple[DownloadItem | AsmJsGame, ...]
    external_links: tuple[str, ...] = ()


@dataclass(frozen=True)
class Order:
    """A purchased order/bundle."""

    order_id: str
    bundle_title: str
    products: tuple[Product, ...]


@dataclass(frozen=True)
class TroveProduct:
    """A Humble Trove product."""

    human_name: str
    downloads: tuple[DownloadItem, ...]


@dataclass
class CacheEntry:
    """Data stored per file in the cache."""

    url_last_modified: str | None = None
    uploaded_at: str | None = None
    md5: str | None = None
