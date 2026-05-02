import http.cookiejar
import json
from pathlib import Path
from unittest.mock import MagicMock, patch

import httpx
import pytest

from humblebundle_downloader.auth import (
    _cookies_from_file,
    _cookies_from_rookiepy,
    _cookies_from_session_auth,
    create_client,
)
from humblebundle_downloader.exceptions import AuthError


class TestCookiesFromSessionAuth:
    def test_builds_cookie_header(self):
        result = _cookies_from_session_auth("my_session_value")
        assert result == {"cookie": "_simpleauth_sess=my_session_value"}

    def test_preserves_quotes_in_value(self):
        result = _cookies_from_session_auth('"quoted_value"')
        assert result == {"cookie": '_simpleauth_sess="quoted_value"'}

    def test_empty_value(self):
        result = _cookies_from_session_auth("")
        assert result == {"cookie": "_simpleauth_sess="}


class TestCookiesFromFile:
    def test_raw_cookie_string_fallback(self, tmp_path):
        cookie_file = tmp_path / "cookies.txt"
        cookie_file.write_text("_simpleauth_sess=abc123; other=def456\n")
        result = _cookies_from_file(cookie_file)
        assert isinstance(result, dict)
        assert result["cookie"] == "_simpleauth_sess=abc123; other=def456"

    def test_raw_cookie_strips_whitespace(self, tmp_path):
        cookie_file = tmp_path / "cookies.txt"
        cookie_file.write_text("  cookie_value  \n")
        result = _cookies_from_file(cookie_file)
        assert result["cookie"] == "cookie_value"

    def test_netscape_format(self, tmp_path):
        cookie_file = tmp_path / "cookies.txt"
        # Valid Netscape cookie format
        cookie_file.write_text(
            "# Netscape HTTP Cookie File\n"
            ".humblebundle.com\tTRUE\t/\tTRUE\t0\t_simpleauth_sess\ttest_value\n"
        )
        result = _cookies_from_file(cookie_file)
        # MozillaCookieJar returns an httpx.Cookies on success
        assert isinstance(result, httpx.Cookies)


class TestCookiesFromRookiepy:
    def test_raises_when_rookiepy_not_installed(self):
        with patch.dict("sys.modules", {"rookiepy": None}):
            with pytest.raises(AuthError, match="rookiepy is not installed"):
                _cookies_from_rookiepy()

    def test_raises_for_unknown_browser(self):
        mock_rookiepy = MagicMock()
        mock_rookiepy.netscape = None  # simulate missing attribute
        delattr(mock_rookiepy, "netscape") if hasattr(mock_rookiepy, "netscape") else None
        with patch.dict("sys.modules", {"rookiepy": mock_rookiepy}):
            with pytest.raises(AuthError, match="Unknown browser"):
                _cookies_from_rookiepy("netscape")

    def test_raises_when_no_cookies_found(self):
        mock_rookiepy = MagicMock()
        mock_rookiepy.load.return_value = []
        with patch.dict("sys.modules", {"rookiepy": mock_rookiepy}):
            with pytest.raises(AuthError, match="No cookies found"):
                _cookies_from_rookiepy()

    def test_auto_detect_uses_load(self):
        mock_rookiepy = MagicMock()
        mock_rookiepy.load.return_value = [
            {"name": "_simpleauth_sess", "value": "abc", "domain": ".humblebundle.com"}
        ]
        with patch.dict("sys.modules", {"rookiepy": mock_rookiepy}):
            result = _cookies_from_rookiepy()
        mock_rookiepy.load.assert_called_once_with(["humblebundle.com"])
        assert isinstance(result, httpx.Cookies)

    def test_specific_browser(self):
        mock_rookiepy = MagicMock()
        mock_rookiepy.chrome.return_value = [
            {"name": "_simpleauth_sess", "value": "abc", "domain": ".humblebundle.com"}
        ]
        with patch.dict("sys.modules", {"rookiepy": mock_rookiepy}):
            result = _cookies_from_rookiepy("chrome")
        mock_rookiepy.chrome.assert_called_once_with(["humblebundle.com"])
        assert isinstance(result, httpx.Cookies)

    def test_browser_case_insensitive(self):
        mock_rookiepy = MagicMock()
        mock_rookiepy.firefox.return_value = [
            {"name": "session", "value": "val", "domain": ".humblebundle.com"}
        ]
        with patch.dict("sys.modules", {"rookiepy": mock_rookiepy}):
            _cookies_from_rookiepy("Firefox")
        mock_rookiepy.firefox.assert_called_once()

    def test_multiple_cookies_preserved(self):
        mock_rookiepy = MagicMock()
        mock_rookiepy.load.return_value = [
            {"name": "_simpleauth_sess", "value": "sess1", "domain": ".humblebundle.com"},
            {"name": "csrf_cookie", "value": "csrf1", "domain": ".humblebundle.com"},
        ]
        with patch.dict("sys.modules", {"rookiepy": mock_rookiepy}):
            result = _cookies_from_rookiepy()
        assert result.get("_simpleauth_sess") == "sess1"
        assert result.get("csrf_cookie") == "csrf1"


class TestCreateClient:
    @pytest.mark.asyncio
    async def test_raises_when_no_auth(self):
        with pytest.raises(AuthError, match="No authentication method"):
            await create_client()

    @pytest.mark.asyncio
    async def test_session_auth_creates_client(self):
        client = await create_client(session_auth="test_value")
        assert isinstance(client, httpx.AsyncClient)
        assert client.headers.get("cookie") == "_simpleauth_sess=test_value"
        await client.aclose()

    @pytest.mark.asyncio
    async def test_cookie_file_raw(self, tmp_path):
        cookie_file = tmp_path / "cookies.txt"
        cookie_file.write_text("raw_cookie_data")
        client = await create_client(cookie_file=cookie_file)
        assert isinstance(client, httpx.AsyncClient)
        assert client.headers.get("cookie") == "raw_cookie_data"
        await client.aclose()

    @pytest.mark.asyncio
    async def test_auto_detect(self):
        mock_rookiepy = MagicMock()
        mock_rookiepy.load.return_value = [
            {"name": "_simpleauth_sess", "value": "auto", "domain": ".humblebundle.com"}
        ]
        with patch.dict("sys.modules", {"rookiepy": mock_rookiepy}):
            client = await create_client(auto_detect=True)
        assert isinstance(client, httpx.AsyncClient)
        await client.aclose()

    @pytest.mark.asyncio
    async def test_browser_explicit(self):
        mock_rookiepy = MagicMock()
        mock_rookiepy.chrome.return_value = [
            {"name": "_simpleauth_sess", "value": "chrome", "domain": ".humblebundle.com"}
        ]
        with patch.dict("sys.modules", {"rookiepy": mock_rookiepy}):
            client = await create_client(browser="chrome")
        assert isinstance(client, httpx.AsyncClient)
        await client.aclose()

    @pytest.mark.asyncio
    async def test_client_has_base_url(self):
        client = await create_client(session_auth="test")
        assert str(client.base_url) == "https://www.humblebundle.com"
        await client.aclose()

    @pytest.mark.asyncio
    async def test_client_follows_redirects(self):
        client = await create_client(session_auth="test")
        assert client.follow_redirects is True
        await client.aclose()

    @pytest.mark.asyncio
    async def test_client_timeout(self):
        client = await create_client(session_auth="test")
        assert client.timeout.connect == 10.0
        assert client.timeout.read == 30.0
        await client.aclose()
