import http.cookiejar
import logging
from pathlib import Path

import httpx

from .exceptions import AuthError

logger = logging.getLogger(__name__)

HUMBLE_DOMAIN = "humblebundle.com"
BASE_URL = "https://www.humblebundle.com"

_SUPPORTED_BROWSERS = (
    "chrome",
    "firefox",
    "edge",
    "brave",
    "opera",
    "chromium",
    "vivaldi",
)


def _cookies_from_rookiepy(browser: str | None = None) -> httpx.Cookies:
    """Extract cookies from browser using rookiepy.

    Args:
        browser: Specific browser name. None means try all browsers.

    Raises:
        AuthError: If rookiepy is not installed or no cookies found.
    """
    try:
        import rookiepy
    except ImportError:
        raise AuthError(
            "rookiepy is not installed. Install with: pip install rookiepy"
        )

    if browser:
        fn = getattr(rookiepy, browser.lower(), None)
        if fn is None:
            raise AuthError(
                f"Unknown browser: {browser}. "
                f"Supported: {', '.join(_SUPPORTED_BROWSERS)}"
            )
        cookie_list = fn([HUMBLE_DOMAIN])
    else:
        cookie_list = rookiepy.load([HUMBLE_DOMAIN])

    if not cookie_list:
        raise AuthError(
            f"No cookies found for {HUMBLE_DOMAIN}. "
            "Make sure you are logged in to humblebundle.com in your browser."
        )

    jar = httpx.Cookies()
    for cookie in cookie_list:
        jar.set(
            cookie["name"],
            cookie["value"],
            domain=cookie.get("domain", HUMBLE_DOMAIN),
        )
    return jar


def _cookies_from_file(cookie_path: Path) -> httpx.Cookies | dict[str, str]:
    """Load cookies from a Netscape cookie file or raw cookie text file.

    Returns either an httpx.Cookies jar (Netscape format) or
    a headers dict (raw cookie string fallback).
    """
    try:
        jar = http.cookiejar.MozillaCookieJar(str(cookie_path))
        jar.load()
        return httpx.Cookies(jar)
    except http.cookiejar.LoadError:
        raw = cookie_path.read_text().strip()
        return {"cookie": raw}


def _cookies_from_session_auth(session_auth: str) -> dict[str, str]:
    """Build cookie header from _simpleauth_sess value."""
    return {"cookie": f"_simpleauth_sess={session_auth}"}


async def create_client(
    cookie_file: Path | None = None,
    session_auth: str | None = None,
    browser: str | None = None,
    auto_detect: bool = False,
) -> httpx.AsyncClient:
    """Create an authenticated httpx.AsyncClient.

    Priority: cookie_file > session_auth > browser > auto_detect.

    Args:
        cookie_file: Path to Netscape cookie file or raw cookie text.
        session_auth: Value of _simpleauth_sess cookie.
        browser: Browser name for rookiepy extraction.
        auto_detect: If True, use rookiepy.load() to try all browsers.

    Returns:
        Configured httpx.AsyncClient ready for API calls.

    Raises:
        AuthError: If no authentication method provided or cookies not found.
    """
    cookies = None
    extra_headers: dict[str, str] = {}

    if cookie_file:
        result = _cookies_from_file(cookie_file)
        if isinstance(result, dict):
            extra_headers = result
        else:
            cookies = result
    elif session_auth:
        extra_headers = _cookies_from_session_auth(session_auth)
    elif browser or auto_detect:
        cookies = _cookies_from_rookiepy(browser if browser else None)
    else:
        raise AuthError("No authentication method provided")

    return httpx.AsyncClient(
        base_url=BASE_URL,
        cookies=cookies,
        headers=extra_headers,
        follow_redirects=True,
        timeout=httpx.Timeout(60.0, connect=30.0),
    )
