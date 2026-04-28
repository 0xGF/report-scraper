"""Tests for `_http.check_url` and `resolve_with_fallback`.

The HEAD→GET fallback is load-bearing: many asset-CDN backends return
inappropriate status codes for HEAD (403/404/405/5xx) on URLs that
GET serves correctly. Without the fallback, every brand-CDN PDF gets
dropped at validation. Tests use a mocked `httpx.Client` so they
stay offline.
"""

from __future__ import annotations

from typing import Any
from unittest.mock import MagicMock, patch

from report_discover._http import check_url, resolve_with_fallback


class _FakeResponse:
    def __init__(
        self,
        status_code: int,
        content_type: str = "application/pdf",
    ) -> None:
        self.status_code = status_code
        self.headers = {"content-type": content_type}


def _client_with(head: _FakeResponse, get: _FakeResponse | None = None) -> MagicMock:
    """Build a mock `httpx.Client` whose context manager returns canned
    responses for HEAD and GET. If `get` is None, GET raises (so a
    test that expects no fallback can prove HEAD wasn't retried)."""
    client_inst = MagicMock()
    client_inst.head.return_value = head
    if get is not None:
        client_inst.get.return_value = get
    else:
        client_inst.get.side_effect = AssertionError("GET not expected")
    cm = MagicMock()
    cm.__enter__.return_value = client_inst
    cm.__exit__.return_value = None
    return cm


# --------------------------------------------------------------------------- #
# `check_url` — HEAD-first, GET-with-Range fallback on any 4xx/5xx
# --------------------------------------------------------------------------- #


def test_check_url_passes_on_clean_head() -> None:
    """200 + `application/pdf` content-type → True without GET fallback."""
    fake = _client_with(_FakeResponse(200, "application/pdf"))
    with patch("report_discover._http.httpx.Client", return_value=fake):
        assert check_url("https://x.com/r.pdf") is True
    fake.__enter__.return_value.head.assert_called_once()
    fake.__enter__.return_value.get.assert_not_called()


def test_check_url_rejects_html_passing_through() -> None:
    """200 + `text/html` content-type → False (it's an error page)."""
    fake = _client_with(_FakeResponse(200, "text/html; charset=utf-8"))
    with patch("report_discover._http.httpx.Client", return_value=fake):
        assert check_url("https://x.com/r.pdf") is False


def test_check_url_falls_back_to_get_on_403() -> None:
    """403 on HEAD (some CDNs reject HEAD outright) → retry with ranged GET."""
    fake = _client_with(
        _FakeResponse(403, "text/plain"),
        get=_FakeResponse(206, "application/pdf"),
    )
    with patch("report_discover._http.httpx.Client", return_value=fake):
        assert check_url("https://x.com/r.pdf") is True


def test_check_url_falls_back_on_404_frontify_pattern() -> None:
    """Frontify (Adyen's brand CDN) returns 404 on HEAD even when GET
    serves the binary correctly. Without this fallback every Adyen
    brand-CDN PDF was rejected at validation."""
    fake = _client_with(
        _FakeResponse(404, "application/json"),
        get=_FakeResponse(200, "application/pdf"),
    )
    with patch("report_discover._http.httpx.Client", return_value=fake):
        assert check_url("https://brand.adyen.com/api/asset/abc/download") is True


def test_check_url_falls_back_on_500() -> None:
    """5xx on HEAD also triggers the fallback — broken HEAD impl, not
    a dead URL."""
    fake = _client_with(
        _FakeResponse(500),
        get=_FakeResponse(206, "application/pdf"),
    )
    with patch("report_discover._http.httpx.Client", return_value=fake):
        assert check_url("https://x.com/r.pdf") is True


def test_check_url_returns_false_when_get_also_fails() -> None:
    """If both HEAD AND the ranged-GET fallback fail, the URL is
    genuinely dead."""
    fake = _client_with(
        _FakeResponse(404, "text/html"),
        get=_FakeResponse(404, "text/html"),
    )
    with patch("report_discover._http.httpx.Client", return_value=fake):
        assert check_url("https://x.com/dead.pdf") is False


def test_check_url_swallows_network_exception() -> None:
    """Any network exception → False (not a crash)."""

    def raising_client(*_args: Any, **_kwargs: Any) -> Any:
        raise ConnectionError("DNS unreachable")

    with patch("report_discover._http.httpx.Client", side_effect=raising_client):
        assert check_url("https://nonexistent.example/x.pdf") is False


# --------------------------------------------------------------------------- #
# `resolve_with_fallback` — direct check, then Wayback snapshot
# --------------------------------------------------------------------------- #


def test_resolve_returns_url_directly_when_reachable() -> None:
    """If the URL itself validates, no Wayback call."""
    with patch("report_discover._http.check_url", return_value=True):
        assert resolve_with_fallback("https://x.com/r.pdf") == "https://x.com/r.pdf"


def test_resolve_falls_back_to_wayback_snapshot() -> None:
    """When the live URL fails, the Wayback snapshot is queried."""
    snap_url = "https://web.archive.org/web/20200101id_/https://x.com/r.pdf"
    with (
        patch("report_discover._http.check_url", side_effect=[False, True]),
        patch("report_discover._http.wayback_snapshot", return_value=snap_url),
    ):
        assert resolve_with_fallback("https://x.com/r.pdf") == snap_url


def test_resolve_returns_none_when_both_fail() -> None:
    """Live dead AND no Wayback snapshot → None (caller treats as
    'drop this pick')."""
    with (
        patch("report_discover._http.check_url", return_value=False),
        patch("report_discover._http.wayback_snapshot", return_value=None),
    ):
        assert resolve_with_fallback("https://x.com/dead.pdf") is None
