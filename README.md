# Humble Bundle Downloader

[![PyPI Version](https://img.shields.io/pypi/v/humblebundle-downloader?color=blue)](https://pypi.org/project/humblebundle-downloader/)
[![PyPI License](https://img.shields.io/pypi/l/humblebundle-downloader?color=green)](https://pypi.org/project/humblebundle-downloader/)
[![Python Version](https://img.shields.io/badge/python-3.10+-yellow)](https://www.python.org/)
[![Code Style: Ruff](https://img.shields.io/badge/code%20style-ruff-purple)](https://docs.astral.sh/ruff/)
[![Package Manager: uv](https://img.shields.io/badge/package%20manager-uv-orange)](https://docs.astral.sh/uv/)

**Download your entire Humble Bundle library with a single command.**

Automatically detects browser cookies, downloads in parallel with progress
bars, and tracks what's already been downloaded so re-runs only fetch new or
updated content.

> Originally created by [Eddy Hintze](https://github.com/xtream1101/humblebundle-downloader).
> This is a modernized fork with async downloads, automatic cookie detection, and a new CLI.

## Features

- **Automatic cookie detection** from your browser -- no manual export needed (`--auto` or `--browser`)
- **Parallel async downloads** with configurable concurrency (`--concurrent`, default 5, max 20)
- **Rich progress bars** with download speed and ETA for each file
- **Incremental downloads** -- tracks completed files in `.cache.json`, skips on re-run
- **Humble Trove** support (`--trove`)
- **asm.js browser games** with offline playable local HTML
- **File type filtering** via include or exclude lists (`--include` / `--exclude`)
- **Platform filtering** for ebook, video, audio, etc. (`--platform`)
- Works with SSO and 2FA accounts

## Install

### pip

```bash
pip install humblebundle-downloader
```

### uv

```bash
uv tool install humblebundle-downloader
```

### Docker

```bash
docker run -v /path/to/downloads:/downloads \
  ghcr.io/timsleeper/humblebundle-downloader \
  --auto -l /downloads
```

## Usage

### Authentication

Pick **one** method -- they are mutually exclusive.

| Method | Flag | Description |
|--------|------|-------------|
| Auto-detect | `--auto` | Tries all installed browsers |
| Specific browser | `--browser chrome` | chrome, firefox, edge, brave, opera, chromium, vivaldi |
| Session cookie | `-s 'VALUE'` | Raw `_simpleauth_sess` cookie value from devtools |
| Cookie file | `-c cookies.txt` | Netscape format cookie file |

### Examples

```bash
# Download everything, auto-detect browser cookies
hbd --auto -l ~/HumbleLibrary

# Download only PDFs from a specific browser
hbd --browser firefox -l ~/HumbleLibrary --include pdf

# Download ebooks only, 10 parallel downloads, with debug logging
hbd --auto -l ~/HumbleLibrary -p ebook -n 10 --verbose

# Download Humble Trove content only
hbd --auto -l ~/HumbleLibrary --trove

# Re-check for updated versions of already-downloaded files
hbd --auto -l ~/HumbleLibrary --update

# Download specific bundles by purchase key
hbd --auto -l ~/HumbleLibrary -k PURCHASE_KEY_1 -k PURCHASE_KEY_2

# Using a session cookie (quotes are part of the value)
hbd -s '"eyJ...long_value..."' -l ~/HumbleLibrary
```

### CLI Reference

```
Options:
  -l, --library-path PATH    Folder to download all content to (required)
  -a, --auto                 Automatically detect cookies from any browser
  -b, --browser TEXT         Browser to extract cookies from
  -c, --cookie-file PATH     Path to Netscape cookie file
  -s, --session-auth TEXT    Value of _simpleauth_sess cookie
  -t, --trove                Only download Humble Trove content
  -u, --update               Check for updated versions of downloaded files
  -p, --platform TEXT        Only download for these platforms (repeatable)
  -i, --include TEXT         Only download these file extensions (repeatable)
  -e, --exclude TEXT         Skip these file extensions (repeatable)
  -k, --keys TEXT            Only download specific purchase keys (repeatable)
  -n, --concurrent INTEGER   Max parallel downloads [default: 5, range: 1-20]
  -v, --verbose              Enable debug logging
  --help                     Show this message and exit
```

## Notes

- A `.cache.json` file is saved inside your library folder, tracking what has
  been downloaded. Re-running the same command skips already-downloaded files.
  The cache flushes periodically so progress is preserved even if the process
  is interrupted.
- The `--include` and `--exclude` flags are mutually exclusive.
- Find supported platforms for `--platform` by visiting your Humble Bundle
  Library and looking under the **Platform** dropdown.
- Find purchase keys for `-k` by going to your _Purchases_ section, clicking
  on a product, and copying the `downloads?key=XXXX` value from the URL.
- If your session cookie expires mid-download, grab a fresh one and re-run --
  the cache ensures it picks up where it left off.

## Architecture

```
humblebundle_downloader/
    cli.py           Typer CLI with Rich console output
    auth.py          Cookie extraction (rookiepy, cookie file, session auth)
    api.py           Async Humble Bundle API client (httpx)
    downloader.py    Async download engine with concurrency control
    cache.py         Async-safe download cache (.cache.json)
    filters.py       Extension and platform filtering
    models.py        Domain dataclasses (Order, Product, DownloadItem, etc.)
    utils.py         Filename sanitization and file utilities
    exceptions.py    Exception hierarchy
```

## Development

Requires Python 3.10+ and [uv](https://docs.astral.sh/uv/).

```bash
uv sync --all-extras          # install all dependencies
uv run pytest                  # run tests (169 tests)
uv run ruff check .            # lint
uv run ruff format --check .   # format check
uv run hbd --help              # run CLI locally
```

## License

MIT -- see [LICENSE](LICENSE).
