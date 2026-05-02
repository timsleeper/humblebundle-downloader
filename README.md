# Humble Bundle Downloader

[![PyPI](https://img.shields.io/pypi/v/humblebundle-downloader.svg)](https://pypi.python.org/pypi/humblebundle-downloader)
[![PyPI](https://img.shields.io/pypi/l/humblebundle-downloader.svg)](https://pypi.python.org/pypi/humblebundle-downloader)

**Download all of your content from your Humble Bundle Library!**

The first time this runs it may take a while because it will download everything.
After that it will only download the content that has been updated or is missing.

## Features

- **automatic cookie detection** from your browser -- no manual export needed _(`--auto` or `--browser` flag)_
- **parallel downloads** with configurable concurrency _(`--concurrent` flag, default 5)_
- **rich progress bars** with download speed and ETA for each file
- support for Humble Trove _(`--trove` flag)_
- downloads new and updated content from your Humble Bundle Library on each run _(only check for updates if using `--update`)_
- cli command for easy use (downloading will also work on a headless system)
- works for SSO and 2FA accounts
- optional filter by file types using an include _or_ exclude list _(`--include/--exclude` flag)_
- optional filter by platform types like video, ebook, etc... _(`--platform` flag)_
- support for asm.js browser games with offline playable local HTML

## Install

### Using pip

`pip install humblebundle-downloader`

### Using uv

`uv tool install humblebundle-downloader`

### Using docker

Remember to mount your download directory in the container using docker's `-v` argument.
`docker run ghcr.io/xtream1101/humblebundle-downloader --help`

## Instructions

### 1. Authentication

There are several ways to authenticate with Humble Bundle:

- **Method 1: Automatic browser detection (recommended)**

    If you are logged into Humble Bundle in your browser, the tool can
    automatically extract your session cookies:

    ```bash
    # Auto-detect from any installed browser
    hbd --auto --library-path "Downloaded Library"

    # Or specify a browser
    hbd --browser chrome --library-path "Downloaded Library"
    ```

    Supported browsers: chrome, firefox, edge, brave, opera, chromium, vivaldi

- **Method 2: Session cookie value**

    Get the value of the cookie called `_simpleauth_sess` from your browser's
    developer tools and pass it directly:

    ```bash
    hbd -s 'COOKIE_VALUE' --library-path "Downloaded Library"
    ```

    Note: The quotes in the cookie value are part of the value, you might need
    to wrap the entire value (including double quotes) in single quotes. Some
    suggestions for common issues can be found in
    [issue #50](https://github.com/xtream1101/humblebundle-downloader/issues/50)

- **Method 3: Cookie file**

    Export the cookies in the Netscape format using a browser extension:

    ```bash
    hbd --cookie-file cookies.txt --library-path "Downloaded Library"
    ```

    If your exported cookie file is not working, it may be a formatting issue.
    This can be fixed by running:
    `curl -b cookies.orig.txt --cookie-jar cookies.txt http://bogus`

### 2. Downloading your library

Basic usage with automatic authentication:

```bash
hbd --auto --library-path "Downloaded Library"
```

With all options:

```bash
hbd --auto \
    --library-path "Downloaded Library" \
    --concurrent 10 \
    --platform ebook \
    --include pdf epub \
    --update \
    --verbose
```

This directory structure will be used:
`Downloaded Library/Purchase Name/Item Name/downloaded_file.ext`

### CLI reference

```text
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

- Inside your library folder a file named `.cache.json` is saved and keeps
  track of the files that have been downloaded. This way running the download
  command again pointing to the same directory will only download new or
  updated files.
- Use `--help` to see all available options.
- Find supported platforms for the `--platform` flag by visiting your Humble
  Bundle Library and look under the **Platform** dropdown.
- Download select bundles by using the `-k` or `--keys` flag. Find these keys
  by going to your _Purchases_ section, click on a product and there should be
  a `downloads?key=XXXX` in the url.
- The `--include` and `--exclude` flags are mutually exclusive.
- The `--auto`, `--browser`, `--cookie-file`, and `--session-auth` flags are
  mutually exclusive -- use exactly one.

## Architecture

```text
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
uv sync                      # install all dependencies
uv run pytest                 # run tests
uv run pytest tests/test_filters.py::test_include_with_values  # single test
uv run ruff check .           # lint
uv run ruff format --check .  # format check
uv run hbd --help             # run CLI locally
```
