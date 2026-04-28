"""`report-scrape market` — pull live price + sparkline from Yahoo Finance.

Writes `data/market.json` consumed by the web viewer's company directory.
Yahoo's chart endpoint is the only public, key-less path that still works
in 2025; it returns `regularMarketPrice` + a daily-close array we
downsample for the sparkline. It does *not* return market cap, so we
compute that as `price * shares_outstanding_m * 1_000_000` using the
shares-outstanding figure each company carries in `companies.yaml`.

Refresh by re-running the CLI; output is committed so the viewer can
serve the most recent snapshot as static JSON.
"""

from __future__ import annotations

import json
import time
from typing import Any

import httpx
import structlog

from pipeline.config import get_settings
from pipeline.domain.companies import get_all_companies

log = structlog.get_logger()

_CHART_URL = "https://query1.finance.yahoo.com/v8/finance/chart/{ticker}"
_HEADERS = {"User-Agent": "Mozilla/5.0 (compatible; report-scrape/0.1)"}
# 3 months of daily closes — plenty for a sparkline, not so much it
# dwarfs the rest of the JSON.
_RANGE = "3mo"
_INTERVAL = "1d"
# Cap sparkline density so the SVG stays light in the browser.
_MAX_POINTS = 60


def _fetch(client: httpx.Client, ticker: str) -> dict[str, Any] | None:
    try:
        r = client.get(
            _CHART_URL.format(ticker=ticker),
            params={"range": _RANGE, "interval": _INTERVAL},
            headers=_HEADERS,
            timeout=15.0,
        )
        r.raise_for_status()
    except Exception as e:
        log.warning("market.fetch_failed", ticker=ticker, err=str(e)[:120])
        return None
    payload = r.json()
    result = (payload.get("chart") or {}).get("result")
    if not result:
        log.warning(
            "market.no_result",
            ticker=ticker,
            err=(payload.get("chart") or {}).get("error"),
        )
        return None
    return result[0]


def _downsample(values: list[float | None]) -> list[float]:
    """Keep only points with values, then thin to ≤_MAX_POINTS evenly spaced."""
    cleaned = [v for v in values if v is not None]
    if len(cleaned) <= _MAX_POINTS:
        return cleaned
    step = len(cleaned) / _MAX_POINTS
    return [cleaned[int(i * step)] for i in range(_MAX_POINTS)]


def _to_record(slug: str, doc: Any) -> dict[str, Any] | None:
    market_ticker = getattr(doc, "market_ticker", None) or doc.ticker
    shares_m = getattr(doc, "shares_outstanding_m", None) or 0
    if not market_ticker:
        return None
    with httpx.Client() as client:
        result = _fetch(client, market_ticker)
    if result is None:
        return None
    meta = result.get("meta") or {}
    price = meta.get("regularMarketPrice")
    currency = meta.get("currency")
    prev_close = meta.get("chartPreviousClose")
    quotes = (result.get("indicators") or {}).get("quote") or [{}]
    closes = quotes[0].get("close") or []
    sparkline = _downsample(closes)
    market_cap = (
        float(price) * float(shares_m) * 1_000_000.0 if price is not None and shares_m else None
    )
    change_pct = (
        ((price - prev_close) / prev_close * 100.0)
        if price is not None and prev_close not in (None, 0)
        else None
    )
    return {
        "slug": slug,
        "ticker": market_ticker,
        "price": price,
        "currency": currency,
        "previous_close": prev_close,
        "change_pct": change_pct,
        "market_cap": market_cap,
        "shares_outstanding_m": shares_m,
        "sparkline": sparkline,
        "fetched_at": int(time.time()),
    }


def run() -> None:
    settings = get_settings()
    out_path = settings.report_scrape_data_dir / "market.json"
    records: list[dict[str, Any]] = []
    for slug, doc in get_all_companies().items():
        rec = _to_record(slug, doc)
        if rec is None:
            log.warning("market.skip", slug=slug)
            continue
        records.append(rec)
        log.info(
            "market.fetched",
            slug=slug,
            price=rec["price"],
            currency=rec["currency"],
            mcap_b=round((rec["market_cap"] or 0) / 1e9, 1),
            sparkline_pts=len(rec["sparkline"]),
        )
    out_path.write_text(json.dumps(records, indent=2))
    log.info("market.done", written=str(out_path), companies=len(records))
